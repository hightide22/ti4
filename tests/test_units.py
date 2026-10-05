import math
import unittest
from collections import Counter

from board import load_board
from units import Region, Unit, UnitLocation, hex_clearance, layout_units, placed_outline, circle_overlap, polygons_overlap, system_inventory


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.board = load_board()[1]
        self.homes = {t.number: t for t in self.board.values() if t.units}

    def test_starting_fleets(self):
        sol = self.homes[1]
        self.assertEqual(Counter(u.kind for u in sol.units), {"carrier": 2, "destroyer": 1, "fighter": 3, "infantry": 5, "spacedock": 1})
        self.assertEqual(len(self.homes[16].units), 10)
        self.assertEqual(len(self.homes[12].units), 9)
        ids = [u.unit_id for tile in self.homes.values() for u in tile.units]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_unit_is_rendered_once(self):
        for tile in self.homes.values():
            for detailed in (False, True):
                placements = layout_units(tile, detailed)
                rendered = [u.unit_id for p in placements for u in p.units]
                self.assertCountEqual(rendered, [u.unit_id for u in tile.units])

    def test_fleet_avoids_planets_and_other_ships(self):
        for tile in self.homes.values():
            for detailed in (False, True):
                fleet = [p for p in layout_units(tile, detailed) if p.units[0].location.region == Region.SPACE]
                for i, p in enumerate(fleet):
                    outline = placed_outline(p)
                    self.assertTrue(all(hex_clearance(x, y) >= 3 for x, y in outline))
                    for planet in tile.planets:
                        self.assertFalse(circle_overlap(outline, planet.center, planet.radius + 2))
                    for other in fleet[:i]:
                        self.assertFalse(polygons_overlap(outline, placed_outline(other)))

    def test_small_groups_and_count_threshold(self):
        from board import TILES
        for count in (4, 5, 12):
            tile = TILES.create(50, (0, 0))
            tile.units = [Unit(str(i), "fighter", "sol", "blu", UnitLocation(Region.SPACE)) for i in range(count)]
            placements = layout_units(tile, True)
            self.assertEqual(len(placements), count if count < 5 else 1)
            self.assertEqual(sum(len(p.units) for p in placements), count)
            self.assertEqual(len(system_inventory(tile)["fleet"]["fighter"]), count)

    def test_inventory_separates_planet_garrisons(self):
        tile = self.homes[16]
        inventory = system_inventory(tile)
        self.assertEqual(len(inventory["fleet"]["carrier"]), 2)
        self.assertEqual(set(inventory["planets"]), {p.planet_id for p in tile.planets})
        for planet_id, groups in inventory["planets"].items():
            for members in groups.values():
                self.assertTrue(all(u.location.planet_id == planet_id for u in members))
        self.assertEqual(sum(len(us) for groups in inventory["planets"].values() for us in groups.values()), 5)

    def test_ground_stays_on_its_planet(self):
        for tile in self.homes.values():
            for p in layout_units(tile, True):
                unit = p.units[0]
                if unit.location.region == Region.PLANET:
                    planet = next(x for x in tile.planets if x.planet_id == unit.location.planet_id)
                    self.assertLess(math.dist((p.x, p.y), planet.center) + p.size / 2, planet.radius)

    def test_illegal_locations_are_rejected(self):
        with self.assertRaises(ValueError):
            UnitLocation(Region.PLANET)
        with self.assertRaises(ValueError):
            Unit("bad", "infantry", "sol", "blu", UnitLocation(Region.SPACE))
        with self.assertRaises(ValueError):
            Unit("bad", "carrier", "sol", "blu", UnitLocation(Region.PLANET, "jord"))

    def test_tile_instances_have_independent_units(self):
        from board import TILES
        another = TILES.create(1, (0, 0))
        self.assertEqual(another.units, [])
        self.assertEqual(len(self.homes[1].units), 12)


if __name__ == "__main__":
    unittest.main()
