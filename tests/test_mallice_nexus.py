"""The wormhole nexus has one planet and two persistent tile sides."""

import unittest

from board import ROOT, TILES, load_board
from exploration import ExplorationController
from movement import MovementController
from player import PlanetCard, create_players


class MalliceNexusTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board(ROOT / 'maps/three_player.json',
                                        ('sol', 'jolnar', 'hacan'))
        self.players = create_players(self.board, config)
        self.sol = self.players[0]
        self.movement = MovementController(self.board, self.players)
        self.exploration = ExplorationController(self.board, self.players)
        self.movement.exploration = self.exploration
        self.nexus = next(tile for tile in self.board.values() if tile.number == 82)

    def test_nexus_starts_isolated_and_gamma_only(self):
        self.assertEqual('82a', self.nexus.system_id)
        self.assertEqual(('GAMMA',), self.nexus.wormholes)
        self.assertEqual(['mallice'], [planet.planet_id for planet in self.nexus.planets])
        self.assertEqual([], self.board.neighbors(self.nexus.position))
        self.assertEqual('82b', TILES['82b'].system_id)
        alpha = next(tile for tile in self.board.values() if 'ALPHA' in tile.wormholes)
        self.assertNotIn(alpha, self.movement.neighbors(self.nexus))

    def test_first_ship_enters_through_gamma_then_reveals_other_wormholes(self):
        home = next(tile for tile in self.board.home_tiles
                    if any(planet.faction_homeworld == 'sol' for planet in tile.planets))
        carrier = next(unit for unit in home.units if unit.kind == 'carrier'
                       and unit.owner == 'sol')
        self.exploration.space_tokens[home.position] = ['gamma']
        self.assertEqual((home.position, self.nexus.position),
                         self.movement.route(home, self.nexus, carrier, self.sol))
        session = self.movement.activate(self.sol, self.nexus.position)
        session.toggle(carrier.unit_id)
        self.movement.confirm()
        self.assertEqual('82b', self.nexus.system_id)
        self.assertEqual({'ALPHA', 'BETA', 'GAMMA'}, set(self.nexus.wormholes))
        self.assertIn(carrier, self.nexus.units)
        alpha = next(tile for tile in self.board.values() if 'ALPHA' in tile.wormholes)
        self.assertIn(alpha, self.movement.neighbors(self.nexus))
        self.assertTrue(self.movement.cancel())
        self.assertEqual('82a', self.nexus.system_id)
        self.assertIn(carrier, home.units)

    def test_control_flips_nexus_and_mallice_ability_pays_and_undoes(self):
        planet = self.nexus.planets[0]
        self.nexus.planet_owners[planet.planet_id] = self.sol.faction
        self.sol.planets.append(PlanetCard(planet))
        self.assertTrue(self.board.flip_wormhole_nexus())
        self.assertEqual('82b', self.nexus.system_id)
        self.assertFalse(self.board.flip_wormhole_nexus())

        before = self.sol.trade_goods
        self.movement.use_legendary_planet(self.sol, 'mallice', choice='trade_goods')
        self.assertEqual(before + 2, self.sol.trade_goods)
        self.assertNotIn('mallice', self.movement.legendary_planet_options(self.sol))
        self.assertTrue(self.movement.undo())
        self.assertEqual(before, self.sol.trade_goods)

        self.sol.commodities = 3
        self.movement.use_legendary_planet(self.sol, 'mallice',
                                           choice='convert_commodities')
        self.assertEqual(0, self.sol.commodities)
        self.assertEqual(before + 3, self.sol.trade_goods)


if __name__ == '__main__':
    unittest.main()
