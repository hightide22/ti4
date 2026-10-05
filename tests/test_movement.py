import unittest

from board import load_board
from movement import MovementController, MovementError
from player import create_players
from units import Region


class MovementTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.controller = MovementController(self.board)
        self.player = next(player for player in self.players if player.faction == 'sol')
        self.home = next(tile for tile in self.board.values() if any(u.owner == 'sol' for u in tile.units))
        self.target = next(tile for tile in self.board.neighbors(self.home.position)
                           if not tile.command_tokens)

    def test_activation_moves_carrier_and_selected_infantry_and_undo_restores(self):
        tactical = self.player.command_pools['tactical']
        original_units = list(self.home.units)
        session = self.controller.activate(self.player, self.target.position)
        self.assertEqual(self.player.command_pools['tactical'], tactical - 1)
        self.assertIn('sol', self.target.command_tokens)

        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = [unit for unit in source.passengers if unit.kind == 'infantry'][:2]
        session.toggle(carrier.unit_id)
        for unit in infantry:
            session.toggle(unit.unit_id)
        self.controller.confirm()

        self.assertIn(carrier, self.target.units)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                             unit.location.region == Region.TRANSPORT and
                             unit.location.carrier_id == carrier.unit_id
                             for unit in self.target.units), 2)
        self.assertTrue(self.controller.undo())
        self.assertEqual(self.home.units, original_units)
        self.assertNotIn('sol', self.target.command_tokens)
        self.assertEqual(self.player.command_pools['tactical'], tactical)

    def test_cancel_activation_restores_tactical_token(self):
        tactical = self.player.command_pools['tactical']
        self.controller.activate(self.player, self.target.position)
        self.assertTrue(self.controller.undo())
        self.assertIsNone(self.controller.session)
        self.assertNotIn('sol', self.target.command_tokens)
        self.assertEqual(self.player.command_pools['tactical'], tactical)

    def test_activated_system_cannot_be_activated_twice(self):
        self.controller.activate(self.player, self.target.position)
        with self.assertRaises(MovementError):
            self.controller.activate(self.player, self.target.position)


if __name__ == '__main__':
    unittest.main()
