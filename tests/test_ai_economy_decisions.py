"""Regression tests for resource-aware AI strategy decisions."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from action_cards import ActionCardController
from board import load_board
from game_ai import GameAI
from movement import MovementController
from player import PlanetCard, create_players
from strategic_action import StrategyController, StrategyResolution
from technology import available_technologies
from turn_order import TurnOrder


MAP_PATH = Path(__file__).resolve().parents[1] / 'maps/three_player.json'


class GameAIEconomyDecisionTests(unittest.TestCase):
    def secondary_offer(self, card, faction='sol'):
        config, board = load_board(MAP_PATH)
        players = create_players(board, config)
        player = next(p for p in players if p.faction == faction)
        owner = next(p for p in players if p is not player)
        turn = TurnOrder(players)
        turn.active_index = players.index(owner)
        movement = MovementController(board, players)
        strategy = StrategyController(board, turn, movement)
        strategy.session = StrategyResolution(card, owner, player, [], primary=False,
                                              stage='offer')
        turn.strategy_resolution = strategy.session
        window = SimpleNamespace(board=board, turn_order=turn, movement=movement,
                                 strategy=strategy)
        return GameAI(window, {faction}), player, strategy, movement

    def test_technology_secondary_preserves_token_without_affordable_research(self):
        ai, player, strategy, _ = self.secondary_offer(7)
        player.command_pools['strategic'] = 1
        player.trade_goods = 0
        for planet in player.planets:
            planet.exhausted = True

        ai.resolve_strategy()

        self.assertEqual(player.command_pools['strategic'], 1)
        self.assertIsNone(strategy.session)

    def test_technology_secondary_preserves_token_without_available_research(self):
        ai, player, strategy, _ = self.secondary_offer(7)
        player.command_pools['strategic'] = 1
        player.trade_goods = 10
        player.technologies = frozenset(tech['alias'] for tech in available_technologies(player))

        ai.resolve_strategy()

        self.assertEqual(player.command_pools['strategic'], 1)
        self.assertIsNone(strategy.session)

    def test_jolnar_brilliant_secondary_researches_for_free(self):
        ai, player, strategy, _ = self.secondary_offer(7, faction='jolnar')
        player.command_pools['strategic'] = 1
        player.trade_goods = 0
        for planet in player.planets:
            planet.exhausted = True
        starting_technologies = player.technologies

        ai.resolve_strategy()  # Accept the secondary and activate Brilliant.
        self.assertIsNotNone(strategy.session)
        self.assertTrue(strategy.session.technology_brilliant)
        ai.resolve_strategy()  # Research the free first technology.

        self.assertEqual(player.command_pools['strategic'], 0)
        self.assertEqual(len(player.technologies), len(starting_technologies) + 1)
        self.assertFalse(any(not card.exhausted for card in player.planets))

    def test_leadership_secondary_buys_tokens_for_urgent_pool_deficit(self):
        ai, player, strategy, _ = self.secondary_offer(1)
        player.command_pools.update(tactical=0, fleet=3, strategic=0)
        player.trade_goods = 0
        planets = []
        for tile in ai.window.board.values():
            for planet in tile.planets:
                if planet.name in ('Mecatol Rex', 'Torkan'):
                    tile.planet_owners[planet.planet_id] = player.faction
                    planets.append(PlanetCard(planet))
        self.assertEqual(sum(card.planet.influence for card in planets), 9)
        player.planets = planets
        strategy.session.stage = 'leadership'

        ai.resolve_strategy()

        self.assertEqual(strategy.session.purchases, 3)
        self.assertEqual(player.pending_commands, 3)
        self.assertTrue(all(card.exhausted for card in player.planets))

    def test_warfare_secondary_preserves_token_without_production_resources(self):
        ai, player, strategy, movement = self.secondary_offer(6)
        player.command_pools['strategic'] = 1
        player.trade_goods = 0
        player.technologies = frozenset()
        for planet in player.planets:
            planet.exhausted = True

        ai.resolve_strategy()
        if strategy.session is not None:
            ai.resolve_strategy()

        self.assertEqual(player.command_pools['strategic'], 1)
        self.assertIsNone(strategy.session)
        self.assertIsNone(movement.session)

    def test_trade_secondary_preserves_token_when_commodities_are_full(self):
        ai, player, strategy, _ = self.secondary_offer(5)
        player.command_pools['strategic'] = 1
        player.commodities = player.commodity_limit

        ai.resolve_strategy()

        self.assertEqual(player.command_pools['strategic'], 1)
        self.assertIsNone(strategy.session)

    def test_politics_secondary_draws_cards_when_hand_has_room(self):
        ai, player, strategy, movement = self.secondary_offer(3)
        player.command_pools['strategic'] = 1
        strategy.action_cards = ActionCardController(
            strategy.turn.players, movement, deck=['fs1', 'mb1'], turn=strategy.turn)

        ai.resolve_strategy()

        self.assertEqual(player.command_pools['strategic'], 0)
        self.assertCountEqual(player.action_cards, ['fs1', 'mb1'])
        self.assertIsNone(strategy.session)

    def test_command_allocation_refills_empty_strategy_pool_before_extra_fleet(self):
        player = SimpleNamespace(command_pools={'tactical': 3, 'fleet': 3, 'strategic': 0})
        window = SimpleNamespace(turn_order=SimpleNamespace(round_number=2))
        ai = GameAI(window, {'sol'})

        self.assertEqual(ai.command_pool(player), 'strategic')

    def test_action_compares_move_with_best_unplayed_strategy_card(self):
        config, board = load_board(MAP_PATH)
        players = create_players(board, config)
        player = next(p for p in players if p.faction == 'sol')
        turn = TurnOrder(players)
        turn.active_index = players.index(player)
        turn.strategy_assignments[player.faction] = [8, 7]
        player.trade_goods = 6
        strategy = SimpleNamespace(session=None, start=Mock())
        window = SimpleNamespace(board=board, turn_order=turn,
                                 movement=SimpleNamespace(session=None), strategy=strategy,
                                 action_cards=SimpleNamespace(pending=None),
                                 transaction=SimpleNamespace(session=None),
                                 sync_strategy_actor=Mock())
        ai = GameAI(window, {'sol'})
        ai.best_activation = Mock(return_value=(18, (0, 0), (0, 0)))
        ai.start_activation = Mock()

        ai.step()

        strategy.start.assert_called_once_with(7)
        ai.start_activation.assert_not_called()

    def test_warfare_waits_for_a_tactical_token_to_remove(self):
        config, board = load_board(MAP_PATH)
        players = create_players(board, config)
        player = next(p for p in players if p.faction == 'sol')
        turn = TurnOrder(players)
        turn.active_index = players.index(player)
        turn.strategy_assignments[player.faction] = [6]
        self.assertFalse(any(player.faction in tile.command_tokens for tile in board.values()))
        strategy = SimpleNamespace(session=None, start=Mock())
        window = SimpleNamespace(board=board, turn_order=turn,
                                 movement=SimpleNamespace(session=None), strategy=strategy,
                                 action_cards=SimpleNamespace(pending=None),
                                 transaction=SimpleNamespace(session=None),
                                 sync_strategy_actor=Mock())
        ai = GameAI(window, {'sol'})
        ai.best_activation = Mock(return_value=(11, (0, 0), (0, 0)))
        ai.start_activation = Mock()

        ai.step()

        ai.start_activation.assert_called_once()
        strategy.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
