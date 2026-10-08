from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from game_ai import GameAI, payment_plan
from player import PlayerState
from turn_order import TurnOrder
from board import load_board
from player import create_players
from movement import MovementController
from units import Region, Unit, UnitLocation


@dataclass
class Card:
    resources: int
    influence: int
    planet_id: str
    exhausted: bool = False

    @property
    def planet(self):
        return self


class GameAITests(unittest.TestCase):
    def test_payment_uses_exact_planets_and_only_needed_goods(self):
        player = SimpleNamespace(planets=[Card(4, 0, 'four'), Card(3, 0, 'three'),
                                          Card(1, 0, 'one')], trade_goods=2)
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four', 'one'), 0))
        player.planets[-1].exhausted = True
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four',), 1))
        player.trade_goods = 0
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four', 'three'), 0))

    def test_strategy_draft_adapts_to_command_pool_and_faction(self):
        players = [PlayerState('sol', 'Sol', 'blue', 4),
                   PlayerState('jolnar', 'Jol-Nar', 'cyan', 4),
                   PlayerState('hacan', 'Hacan', 'yellow', 6)]
        turns = TurnOrder(players, strategy_enabled=True)
        window = SimpleNamespace(turn_order=turns, board={})
        ai = GameAI(window, {'sol', 'jolnar', 'hacan'})
        players[0].command_pools['tactical'] = 0
        self.assertEqual(ai.choose_strategy_card(players[0]), 1)
        turns.choose_strategy_card(1)
        self.assertEqual(ai.choose_strategy_card(players[1]), 7)
        turns.choose_strategy_card(7)
        self.assertEqual(ai.choose_strategy_card(players[2]), 5)

    def test_frontline_target_prefers_human_over_other_bot(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        human_tile = next(tile for tile in board.neighbors(home.position) if tile.name == 'Tequran/Torkan')
        bot_tile = next(tile for tile in board.neighbors(home.position) if tile.name == 'Mellon/Zohbat')
        for planet in human_tile.planets:
            human_tile.planet_owners[planet.planet_id] = 'hacan'
        for planet in bot_tile.planets:
            bot_tile.planet_owners[planet.planet_id] = 'jolnar'
        turn = TurnOrder(players)
        window = SimpleNamespace(turn_order=turn, board=board,
                                 movement=MovementController(board, players))
        ai = GameAI(window, {'sol', 'jolnar'})
        self.assertEqual(ai.best_activation(sol)[1], human_tile.position)

    def test_first_bot_action_expands_instead_of_locking_home_dock(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        turn = TurnOrder(players, strategy_enabled=True)
        turn.strategy_selection = False
        turn.strategy_assignments['sol'] = [4, 7]
        movement = MovementController(board, players)
        window = SimpleNamespace(turn_order=turn, board=board, movement=movement,
                                 strategy=SimpleNamespace(session=None, start=Mock()),
                                 action_cards=SimpleNamespace(pending=None),
                                 transaction=SimpleNamespace(session=None))
        ai = GameAI(window, {'sol'})
        ai.start_activation = Mock()
        ai.step()
        ai.start_activation.assert_called_once()
        selected = ai.start_activation.call_args.args[1]
        self.assertNotEqual(selected[1], selected[2])
        self.assertFalse(window.strategy.start.called)

    def test_bot_assignment_sustains_ship_before_losing_fighter(self):
        # Combat's existing assigner decides how damage is applied; the bot
        # should choose a free sustain hit before removing a plastic unit.
        class FakeUnit:
            def __init__(self, kind, damaged=False):
                self.kind, self.damaged, self.unit_id = kind, damaged, kind

        fighter, dreadnought = FakeUnit('fighter'), FakeUnit('dreadnought')
        session = SimpleNamespace(combat_hits={'sol': 1}, combat_assignments={'sol': []})
        chosen = []
        movement = SimpleNamespace(
            combat_hit_capacity=lambda s, f: 3,
            combat_units=lambda s, f: [fighter, dreadnought],
            combat_assignment_target=lambda s, f, k: fighter if k == 'fighter' else dreadnought,
            assign_combat_hit=lambda f, k: chosen.append(k),
        )
        window = SimpleNamespace(movement=movement)
        ai = GameAI(window, {'sol'})
        # The real profile reader needs a genuine Unit, so patch at the module
        # boundary while keeping the hit-choice logic under test.
        from unittest.mock import patch
        with patch('game_ai.unit_profile', side_effect=lambda u: {
                'sustainDamage': u.kind == 'dreadnought'}):
            self.assertTrue(ai._bot_hit('sol', session))
        self.assertEqual(chosen, ['dreadnought'])

    def test_two_bots_resolve_space_combat_without_manual_clicks(self):
        from unittest.mock import patch
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        origin = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in board.neighbors(origin.position)
                      if not tile.anomalies and tile.planets)
        target.units.append(Unit('ai-test-enemy-cruiser', 'cruiser', hacan.faction,
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        movement = MovementController(board, players)
        session = movement.activate(sol, target.position)
        source = session.sources[origin.position]
        for ship in source.ships:
            session.toggle(ship.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'space_combat')
        window = SimpleNamespace(movement=movement, board=board,
                                 action_cards=SimpleNamespace(participants=lambda s: []))
        ai = GameAI(window, {'sol', 'hacan'})
        with patch('movement.random.randint', return_value=10):
            for _ in range(30):
                if movement.session is None or movement.session.stage not in (
                        'space_combat', 'combat_end', 'space_combat_won', 'assault_choice',
                        'retreat_selection'):
                    break
                ai.resolve_movement()
            else:
                self.fail('Bot combat remained unresolved')
        self.assertFalse(any(unit.unit_id == 'ai-test-enemy-cruiser' for unit in target.units))

    def test_bot_production_buys_transport_escort_and_ground_forces(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        tile = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'spacedock' for unit in tile.units))
        dock = next(unit for unit in tile.units if unit.owner == 'sol' and unit.kind == 'spacedock')
        tile.units[:] = [dock]
        sol.trade_goods = 8
        movement = MovementController(board, players)
        movement.start_strategy_production(sol, tile, dock)
        turn = TurnOrder(players)
        window = SimpleNamespace(movement=movement, turn_order=turn)
        ai = GameAI(window, {'sol'})
        ai.resolve_production()
        produced = [unit.kind for unit in tile.units if unit is not dock]
        self.assertIn('carrier', produced)
        self.assertTrue(any(kind in produced for kind in ('cruiser', 'destroyer', 'dreadnought')))
        self.assertIn('infantry', produced)
        self.assertLess(sol.trade_goods, 8)


if __name__ == '__main__':
    unittest.main()
