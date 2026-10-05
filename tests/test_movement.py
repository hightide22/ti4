import unittest

from board import load_board
from movement import MovementController, MovementError
from player import create_players
from units import Region


class MovementTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.controller = MovementController(self.board, self.players)
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
        self.assertEqual(session.stage, 'invasion')
        self.controller.establish_control()
        self.assertEqual(session.stage, 'complete')
        self.controller.finish()
        self.assertTrue(self.controller.undo())
        self.assertEqual(self.home.units, original_units)
        self.assertNotIn('sol', self.target.command_tokens)
        self.assertEqual(self.player.command_pools['tactical'], tactical)

    def test_landed_infantry_captures_planet_and_undo_restores_cards_and_control(self):
        target = next(tile for tile in self.board.neighbors(self.home.position)
                      if tile.planets and not tile.command_tokens and
                      any(self.controller.route(self.home, tile, unit, self.player)
                          for unit in self.home.units if unit.owner == 'sol' and unit.kind == 'carrier'))
        original_cards = list(self.player.planets)
        original_units = list(self.home.units)
        session = self.controller.activate(self.player, target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = next(unit for unit in source.passengers if unit.kind == 'infantry')
        session.toggle(carrier.unit_id)
        session.toggle(infantry.unit_id)
        self.controller.confirm()

        self.controller.cycle_landing(infantry.unit_id)
        planet = target.planets[0]
        self.assertEqual(session.landings[infantry.unit_id], planet.planet_id)
        self.controller.establish_control()
        self.assertEqual(infantry.location.planet_id, planet.planet_id)
        self.assertEqual(target.planet_owners[planet.planet_id], 'sol')
        captured_card = next(card for card in self.player.planets if card.planet.planet_id == planet.planet_id)
        self.assertTrue(captured_card.exhausted)

        self.controller.finish()
        self.assertTrue(self.controller.undo())
        self.assertNotIn(planet.planet_id, target.planet_owners)
        self.assertEqual(self.player.planets, original_cards)
        self.assertEqual(self.home.units, original_units)

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
