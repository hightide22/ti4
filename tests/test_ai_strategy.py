"""Board-level regression scenarios for the bot's tactical decisions."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ai_combat import win_probability
from board import load_board
from game_ai import GameAI
from movement import MovementController
from player import create_players
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


MAP = Path(__file__).resolve().parents[1] / 'maps/three_player.json'


def setup_bot():
    config, board = load_board(MAP)
    players = create_players(board, config)
    sol = next(player for player in players if player.faction == 'sol')
    hacan = next(player for player in players if player.faction == 'hacan')
    home = board[(-3, 3)]
    movement = MovementController(board, players)
    window = SimpleNamespace(
        board=board, movement=movement, turn_order=TurnOrder(players),
        strategy=SimpleNamespace(session=None),
        action_cards=SimpleNamespace(pending=None),
        transaction=SimpleNamespace(session=None),
        movement_panel=SimpleNamespace(reset=Mock()),
        frame_action_route=Mock(), pass_turn=Mock(),
    )
    return GameAI(window, {'sol', 'jolnar'}), sol, hacan, home


def leave_only_target(ai, target, *sources):
    """Spend tokens on other reachable planets, without blocking source fleets."""
    player = ai.window.turn_order.players[0]
    movement = ai.window.movement
    for tile in ai.window.board.values():
        if (tile is not target and tile not in sources and
                any(tile.planet_owners.get(planet.planet_id) != player.faction
                    for planet in tile.planets) and
                any(movement.route(source, tile, unit, player,
                                   bonus=int('gd' in player.technologies))
                    for source in sources for unit in source.units
                    if unit.owner == player.faction and unit.location.region == Region.SPACE
                    and unit.move_value)):
            tile.command_tokens.add('sol')


class BotStrategyTests(unittest.TestCase):
    def test_claims_two_neutral_planets_before_premature_home_production(self):
        ai, sol, _, home = setup_bot()
        target = ai.window.board[(-3, 2)]  # Tequran/Torkan, two neutral planets.
        carrier = next(unit for unit in home.units if unit.kind == 'carrier')
        infantry = [unit for unit in home.units if unit.kind == 'infantry'][:3]
        dock = next(unit for unit in home.units if unit.kind == 'spacedock')
        home.units[:] = [carrier, *infantry, dock]
        sol.trade_goods = 2
        ai.window.turn_order.round_number = 2
        leave_only_target(ai, target, home)

        ai.step()

        session = ai.window.movement.session
        self.assertIsNotNone(session)
        self.assertIs(session.target, target)
        self.assertGreaterEqual(sum(unit.unit_id in session.selected for unit in infantry), 2)

    def test_combines_two_sources_to_attack_fleet_neither_can_beat_alone(self):
        ai, sol, hacan, home = setup_bot()
        second = ai.window.board[(-2, 2)]  # Adjacent to Tequran/Torkan.
        target = ai.window.board[(-3, 2)]
        carrier, fighter = (next(unit for unit in home.units if unit.kind == kind)
                            for kind in ('carrier', 'fighter'))
        infantry = next(unit for unit in home.units if unit.kind == 'infantry')
        home.units[:] = [carrier, fighter, infantry]
        second_carrier = Unit('second-carrier', 'carrier', sol.faction,
                              sol.color_code, UnitLocation(Region.SPACE))
        second_fighter = Unit('second-fighter', 'fighter', sol.faction,
                              sol.color_code, UnitLocation(Region.SPACE))
        second_infantry = Unit('second-infantry', 'infantry', sol.faction,
                               sol.color_code,
                               UnitLocation(Region.PLANET, second.planets[0].planet_id))
        second.units[:] = [second_carrier, second_fighter, second_infantry]
        second.planet_owners.update({planet.planet_id: sol.faction
                                     for planet in second.planets})
        target.units.append(Unit('guard-cruiser', 'cruiser', hacan.faction,
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        sol.planets[0].exhausted = True
        leave_only_target(ai, target, home, second)
        self.assertLess(win_probability([carrier, fighter], target.units,
                                        ai.window.turn_order.players,
                                        require_transport=True), .8)
        self.assertLess(win_probability([second_carrier, second_fighter], target.units,
                                        ai.window.turn_order.players,
                                        require_transport=True), .8)
        self.assertGreaterEqual(win_probability([carrier, fighter, second_carrier,
                                                  second_fighter], target.units,
                                                 ai.window.turn_order.players,
                                                 require_transport=True), .8)

        ai.step()

        session = ai.window.movement.session
        self.assertIsNotNone(session)
        self.assertIs(session.target, target)
        self.assertIn(carrier.unit_id, session.selected)
        self.assertIn(second_carrier.unit_id, session.selected)
        self.assertTrue({infantry.unit_id, second_infantry.unit_id} & session.selected)
        selected_space = [unit for unit in session.choices.values()
                          if unit.unit_id in session.selected and unit.kind != 'infantry']
        self.assertGreaterEqual(win_probability(selected_space, target.units,
                                                 ai.window.turn_order.players,
                                                 require_transport=True), .8)

    def test_gravity_drive_attack_uses_only_one_boosted_ship(self):
        ai, sol, hacan, home = setup_bot()
        source = ai.window.board[(-2, 3)]  # Thibah, two steps from Lodor.
        target = ai.window.board[(-2, 1)]
        carriers = [unit for unit in home.units if unit.kind == 'carrier'][:2]
        fighter = next(unit for unit in home.units if unit.kind == 'fighter')
        infantry = next(unit for unit in home.units if unit.kind == 'infantry')
        home.units.clear()
        infantry.location = UnitLocation(Region.PLANET, source.planets[0].planet_id)
        source.units[:] = [*carriers, fighter, infantry]
        source.planet_owners[source.planets[0].planet_id] = sol.faction
        sol.technologies = sol.technologies | {'gd'}
        sol.planets[0].exhausted = True
        target.units.append(Unit('guard-cruiser', 'cruiser', hacan.faction,
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        leave_only_target(ai, target, source)
        self.assertGreaterEqual(win_probability([*carriers, fighter], target.units,
                                                 ai.window.turn_order.players,
                                                 require_transport=True), .8)
        self.assertLess(win_probability([carriers[0], fighter], target.units,
                                        ai.window.turn_order.players,
                                        require_transport=True), .8)

        ai.step()

        session = ai.window.movement.session
        if session is None or session.target is not target:
            return  # Declining the unsafe attack is a sound choice.
        selected = [unit for unit in session.choices.values()
                    if unit.unit_id in session.selected and unit.kind != 'infantry']
        self.assertGreaterEqual(win_probability(selected, target.units,
                                                 ai.window.turn_order.players,
                                                 require_transport=True), .8,
                                'The chosen fleet must meet the same safety bar as the preview.')
        self.assertLessEqual(sum(unit_id in session.gravity_bonus_ids
                                 for unit_id in session.selected), 1)


if __name__ == '__main__':
    unittest.main()
