"""Combat decisions exercised through real movement and combat sessions."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_combat import win_probability
from board import load_board
from game_ai import GameAI
from movement import MovementController
from player import create_players
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


def setup_game(bot_factions):
    config, board = load_board()
    players = create_players(board, config)
    movement = MovementController(board, players)
    window = SimpleNamespace(
        board=board, movement=movement, turn_order=TurnOrder(players),
        action_cards=SimpleNamespace(pending=None, participants=lambda session: ()),
        strategy=SimpleNamespace(session=None),
        transaction=SimpleNamespace(session=None),
        movement_panel=SimpleNamespace(reset=Mock()),
        frame_action_route=Mock(), pass_turn=Mock(),
    )
    sol = next(player for player in players if player.faction == 'sol')
    hacan = next(player for player in players if player.faction == 'hacan')
    return GameAI(window, bot_factions), sol, hacan


class BotCombatDecisionTests(unittest.TestCase):
    def test_intercept_blocked_retreat_does_not_interrupt_combat(self):
        ai, sol, hacan = setup_game({'hacan'})
        movement, board = ai.window.movement, ai.window.board
        home, target, refuge = board[(-3, 3)], board[(-3, 2)], board[(-2, 2)]
        defender = Unit('intercepted-defender', 'cruiser', hacan.faction,
                        hacan.color_code, UnitLocation(Region.SPACE))
        target.units.append(defender)
        refuge.units.append(Unit('intercepted-refuge', 'cruiser', hacan.faction,
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        attackers = [unit for unit in home.units if unit.owner == sol.faction
                     and unit.kind in ('carrier', 'destroyer', 'fighter')]
        self.assertLess(win_probability([defender], attackers,
                                        ai.window.turn_order.players), .25)

        session = movement.activate(sol, target.position)
        for unit in attackers:
            session.toggle(unit.unit_id)
        movement.confirm()
        self.assertIn(refuge, movement.retreat_options(session, hacan.faction))
        session.retreat_blocked_round = session.combat_round + 1

        ai.resolve_movement()

        self.assertIsNone(session.retreat_announced)
        self.assertEqual(session.stage, 'space_combat')
        with patch('movement.random.randint', return_value=1):
            movement.advance_combat()
        self.assertEqual(session.combat_round, 1)
        self.assertTrue(session.combat_needs_resolution)

    def test_outmatched_defender_retreats_to_friendly_safe_system(self):
        ai, sol, hacan = setup_game({'hacan'})
        movement, board = ai.window.movement, ai.window.board
        home, target = board[(-3, 3)], board[(-3, 2)]
        safe, exposed = board[(-2, 2)], board[(-2, 1)]
        danger = board[(-1, 0)]  # Adjacent to Lodor, not Mellon/Zohbat.
        defender = Unit('weak-defender', 'cruiser', hacan.faction,
                        hacan.color_code, UnitLocation(Region.SPACE))
        target.units.append(defender)
        for tile, name in ((safe, 'safe-receiver'), (exposed, 'exposed-receiver')):
            tile.units.append(Unit(name, 'cruiser', hacan.faction,
                                   hacan.color_code, UnitLocation(Region.SPACE)))
        for index in range(2):
            danger.units.append(Unit(f'near-exposed-{index}', 'dreadnought', sol.faction,
                                     sol.color_code, UnitLocation(Region.SPACE)))
        safe.planet_owners[safe.planets[0].planet_id] = hacan.faction
        attackers = [unit for unit in home.units if unit.owner == sol.faction
                     and unit.kind in ('carrier', 'destroyer', 'fighter')]
        self.assertLess(win_probability([defender], attackers,
                                        ai.window.turn_order.players), .25)

        session = movement.activate(sol, target.position)
        for unit in attackers:
            session.toggle(unit.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'space_combat')
        self.assertEqual({tile.position for tile in movement.retreat_options(session, hacan.faction)},
                         {safe.position, exposed.position})

        ai.resolve_movement()  # The active attacker is human; the defender is a bot.
        self.assertEqual(session.retreat_announced, hacan.faction)
        with patch('movement.random.randint', return_value=1):
            movement.advance_combat()
        movement.advance_combat()
        self.assertEqual(session.stage, 'retreat_selection')

        ai.resolve_movement()
        self.assertIn(defender, safe.units)
        self.assertNotIn(defender, exposed.units)
        self.assertNotIn(defender, target.units)

    def test_landing_claims_neutral_planet_without_suicidal_second_assault(self):
        ai, sol, hacan = setup_game({'sol'})
        movement, board = ai.window.movement, ai.window.board
        home, target = board[(-3, 3)], board[(-3, 2)]
        neutral, defended = target.planets
        target.planet_owners[defended.planet_id] = hacan.faction
        defenders = [Unit(f'ground-defender-{index}', 'infantry', hacan.faction,
                          hacan.color_code,
                          UnitLocation(Region.PLANET, defended.planet_id))
                     for index in range(2)]
        target.units.extend(defenders)
        carrier = next(unit for unit in home.units if unit.kind == 'carrier')
        infantry = [unit for unit in home.units if unit.kind == 'infantry'][:2]
        self.assertLess(win_probability(infantry[:1], defenders,
                                        ai.window.turn_order.players,
                                        space=False), .8)

        session = movement.activate(sol, target.position)
        for unit in (carrier, *infantry):
            session.toggle(unit.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'invasion')

        ai.resolve_movement()

        self.assertEqual(sum(1 for unit in infantry if session.landings[unit.unit_id]
                             == neutral.planet_id), 1)
        self.assertEqual(sum(1 for unit in infantry if session.landings[unit.unit_id]
                             is None), 1)
        self.assertEqual(target.planet_owners[neutral.planet_id], sol.faction)
        self.assertEqual(target.planet_owners[defended.planet_id], hacan.faction)
        self.assertFalse(session.ground_planets)

    def test_single_infantry_does_not_treat_hostile_pds_as_free_planet(self):
        ai, sol, hacan = setup_game({'sol'})
        board, movement = ai.window.board, ai.window.movement
        home, target = board[(-3, 3)], board[(-2, 3)]
        planet = target.planets[0]
        target.planet_owners[planet.planet_id] = hacan.faction
        pds = Unit('hostile-pds', 'pds', hacan.faction, hacan.color_code,
                   UnitLocation(Region.PLANET, planet.planet_id))
        target.units.append(pds)
        infantry = next(unit for unit in home.units if unit.kind == 'infantry')
        home.units[:] = [unit for unit in home.units if unit.kind in
                         ('carrier', 'destroyer', 'fighter', 'spacedock')] + [infantry]
        sol.planets[0].exhausted = True
        ai.window.turn_order.round_number = 2
        for tile in board.values():
            if tile in (home, target) or not tile.planets:
                continue
            if any(movement.route(home, tile, ship, sol) for ship in home.units
                   if ship.location.region == Region.SPACE and ship.move_value):
                tile.command_tokens.add(sol.faction)
        ships = [unit for unit in home.units if unit.location.region == Region.SPACE]
        self.assertGreaterEqual(win_probability(ships, [], ai.window.turn_order.players,
                                                 cannons=[pds], require_transport=True), .8)
        self.assertLess(win_probability([infantry], [], ai.window.turn_order.players,
                                        space=False, cannons=[pds]), .8)

        ai.step()

        session = movement.session
        self.assertTrue(session is None or session.target is not target,
                        'A lone infantry cannot safely survive the PDS landing fire.')


if __name__ == '__main__':
    unittest.main()
