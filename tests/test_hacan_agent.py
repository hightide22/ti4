"""Carth of Golden Sands choices, commodity caps, and readiness."""

import unittest

from board import ROOT, load_board
from leader_effects import use_hacan_agent
from leaders import leader_available
from player import create_players


class HacanAgentTests(unittest.TestCase):
    def setUp(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('hacan', 'sol', 'jolnar', 'letnev'))
        self.players = create_players(board, config)
        self.hacan = next(player for player in self.players if player.faction == 'hacan')
        self.sol = next(player for player in self.players if player.faction == 'sol')

    def test_agent_gains_up_to_two_commodities_without_exceeding_limit(self):
        self.hacan.commodities = self.hacan.commodity_limit - 1

        result = use_hacan_agent(self.hacan)

        self.assertEqual(result, self.hacan.commodity_limit)
        self.assertFalse(leader_available(self.hacan, 'hacanagent'))

    def test_agent_replenishes_another_player_even_when_they_are_already_full(self):
        self.sol.commodities = self.sol.commodity_limit

        result = use_hacan_agent(self.hacan, replenish_player=self.sol)

        self.assertEqual(result, self.sol.commodity_limit)
        self.assertFalse(leader_available(self.hacan, 'hacanagent'))

    def test_agent_cannot_choose_hacan_as_the_other_player(self):
        with self.assertRaisesRegex(ValueError, 'another player'):
            use_hacan_agent(self.hacan, replenish_player=self.hacan)
        self.assertTrue(leader_available(self.hacan, 'hacanagent'))


if __name__ == '__main__':
    unittest.main()
