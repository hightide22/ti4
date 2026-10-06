import unittest

from board import load_board
from player import create_players


class PlayerTests(unittest.TestCase):
    def setUp(self):
        config, board = load_board()
        self.players = {p.faction: p for p in create_players(board, config)}

    def test_faction_commodity_limits_and_initial_reserves(self):
        self.assertEqual({f: p.commodity_limit for f, p in self.players.items()},
                         {'sol': 4, 'hacan': 6, 'jolnar': 4})
        for player in self.players.values():
            self.assertEqual((player.trade_goods, player.commodities), (0, 0))
            self.assertEqual(player.command_pools, {'tactical': 3, 'fleet': 3, 'strategic': 2})

    def test_currency_bounds(self):
        player = self.players['hacan']
        player.change_currency('commodities', 100)
        self.assertEqual(player.commodities, 6)
        player.change_currency('commodities', -100)
        self.assertEqual(player.commodities, 0)
        player.change_currency('trade_goods', 100)
        self.assertEqual(player.trade_goods, 100)
        player.change_currency('trade_goods', -200)
        self.assertEqual(player.trade_goods, 0)

    def test_exhaustion_changes_available_values_without_changing_planet(self):
        player = self.players['sol']
        self.assertEqual(player.available_values, (4, 2))
        player.planets[0].flip()
        self.assertEqual(player.available_values, (0, 0))
        self.assertEqual((player.planets[0].planet.resources, player.planets[0].planet.influence), (4, 2))
        player.planets[0].flip()
        self.assertEqual(player.available_values, (4, 2))

    def test_command_transfer_conserves_tokens_and_handles_empty_source(self):
        player = self.players['sol']
        for _ in range(3):
            self.assertTrue(player.transfer_command('tactical', 'strategic'))
        self.assertFalse(player.transfer_command('tactical', 'fleet'))
        self.assertFalse(player.transfer_command('strategic', 'strategic'))
        self.assertEqual(sum(player.command_pools.values()), 8)
        self.assertEqual(player.command_pools, {'tactical': 0, 'fleet': 3, 'strategic': 5})

    def test_round_commands_are_allocated_into_pools(self):
        sol = self.players['sol']
        hacan = self.players['hacan']
        self.assertEqual(sol.round_command_gain(), 3)
        self.assertEqual(hacan.round_command_gain(), 2)
        before = dict(sol.command_pools)
        sol.receive_round_commands()
        self.assertEqual(sol.pending_commands, 3)
        self.assertTrue(sol.allocate_command('fleet'))
        self.assertTrue(sol.allocate_command('tactical'))
        self.assertTrue(sol.allocate_command('strategic'))
        self.assertEqual(sol.pending_commands, 0)
        self.assertEqual(sol.command_pools, {
            'tactical': before['tactical'] + 1,
            'fleet': before['fleet'] + 1,
            'strategic': before['strategic'] + 1,
        })

    def test_player_states_are_independent(self):
        self.players['sol'].planets[0].flip()
        self.players['sol'].change_currency('commodities', 4)
        self.players['sol'].transfer_command('fleet', 'strategic')
        for faction in ('hacan', 'jolnar'):
            player = self.players[faction]
            self.assertTrue(all(not card.exhausted for card in player.planets))
            self.assertEqual(player.commodities, 0)
            self.assertEqual(player.command_pools['fleet'], 3)


if __name__ == '__main__':
    unittest.main()
