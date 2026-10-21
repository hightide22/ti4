"""Prophecy of Kings starter layouts retain fair, connected home approaches."""

from collections import deque
import unittest

from board import ROOT, load_board
from exploration import ExplorationController
from player import create_players


MAP_CASES = (
    ('three_player.json', 29, {(-3, 3), (0, -3), (3, 0)}),
    ('four_player.json', 38, {(0, -3), (3, -2), (-3, 2), (0, 3)}),
)


def axial_distance(a, b):
    dq, dr = a[0] - b[0], a[1] - b[1]
    return max(abs(dq), abs(dr), abs(dq + dr))


def safe_distance_to_center(board, start):
    """A route without an anomaly never needs a traversal technology or die."""
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        position, distance = queue.popleft()
        if position == (0, 0):
            return distance
        for neighbor in board.neighbors(position):
            if neighbor.position not in visited and not neighbor.anomalies:
                visited.add(neighbor.position)
                queue.append((neighbor.position, distance + 1))
    return None


class ProphecyOfKingsMapTests(unittest.TestCase):
    def test_new_systems_keep_starter_geometry_and_home_slots(self):
        for filename, count, home_positions in MAP_CASES:
            with self.subTest(map=filename):
                config, board = load_board(ROOT / 'maps' / filename)
                self.assertEqual(len(board), count)
                self.assertEqual(len({tile.number for tile in board.values()}), count)
                self.assertEqual({tile.position for tile in board.home_tiles}, home_positions)
                self.assertEqual(board[(0, 0)].number, 18)
                self.assertTrue(all(board.neighbors(tile.position) for tile in board.values()
                                    if tile.number != 82))
                nexus = next(tile for tile in board.values() if tile.number == 82)
                self.assertFalse(board.neighbors(nexus.position))
                self.assertEqual(len(create_players(board, config)), len(home_positions))

    def test_both_layouts_offer_pok_exploration_and_legendary_planets(self):
        required = {64, 65, 66, 67, 68, 79, 82}
        for filename, _, _ in MAP_CASES:
            with self.subTest(map=filename):
                config, board = load_board(ROOT / 'maps' / filename)
                by_number = {tile.number: tile for tile in board.values()}
                self.assertTrue(required <= by_number.keys())
                self.assertTrue(all(by_number[number].definition.source == 'pok'
                                    for number in required))
                self.assertIn('BETA', by_number[64].wormholes)
                self.assertIn('ALPHA', by_number[79].wormholes)
                self.assertIn('asteroid_field', by_number[79].anomalies)
                self.assertIn('gravity_rift', by_number[67].anomalies)
                self.assertIn('nebula', by_number[68].anomalies)
                exploration = ExplorationController(board, create_players(board, config))
                for number in (65, 66):
                    self.assertTrue(exploration.planet_is_legendary(by_number[number].planets[0]))
                all_planet_ids = [planet.planet_id for tile in board.values()
                                  for planet in tile.planets]
                self.assertEqual(len(all_planet_ids), len(set(all_planet_ids)))

    def test_each_home_has_planets_and_a_safe_route_into_the_galaxy(self):
        for filename, _, _ in MAP_CASES:
            with self.subTest(map=filename):
                _, board = load_board(ROOT / 'maps' / filename)
                legendary = [tile.position for tile in board.values()
                             if tile.number in (65, 66)]
                for home in board.home_tiles:
                    with self.subTest(home=home.position):
                        adjacent_planets = [planet for tile in board.neighbors(home.position)
                                            for planet in tile.planets]
                        self.assertGreaterEqual(len(adjacent_planets), 2)
                        self.assertGreaterEqual(sum(planet.resources for planet in adjacent_planets), 6)
                        self.assertLessEqual(min(axial_distance(home.position, position)
                                                 for position in legendary), 3)
                        self.assertLessEqual(safe_distance_to_center(board, home.position), 4)


if __name__ == '__main__':
    unittest.main()
