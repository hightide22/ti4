"""PoK/Codex III starting setup and leader lifecycle regressions."""

import unittest

from board import ROOT, load_board
from factions import faction_catalog
from leaders import (exhaust_leader, leader_available, purge_leader,
                     ready_leaders, record_scored_objective, refresh_leader_unlocks)
from main_menu import MainMenu
from movement import MovementController
from player import PlanetCard, create_players
from strategic_action import StrategyController, StrategyResolution
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation, unit_profile


class PokFactionSetupTests(unittest.TestCase):
    def test_every_new_faction_has_its_home_fleet_tech_and_leaders(self):
        for faction in ('argent', 'cabal', 'empyrean', 'mahact', 'naaz', 'nomad',
                        'titans', 'keleresm', 'keleresx', 'keleresa'):
            with self.subTest(faction=faction):
                config, board = load_board(ROOT / 'maps/four_player.json',
                                           (faction, 'sol', 'jolnar', 'hacan'))
                player = create_players(board, config)[0]
                data = faction_catalog()[faction]
                home = next(tile for tile in board.values()
                            if tile.system_id == data['homeSystem'])
                self.assertTrue(all(planet.faction_homeworld == faction
                                    for planet in home.planets))
                self.assertTrue(all(home.planet_owners[p.planet_id] == faction
                                    for p in home.planets))
                self.assertTrue(any(unit.owner == faction for unit in home.units))
                self.assertEqual(set(player.leaders), set(data['leaders']))
                self.assertTrue(all(leader_available(player, leader_id)
                                    for leader_id in data['leaders'] if 'agent' in leader_id))
                self.assertTrue(all(not leader_available(player, leader_id)
                                    for leader_id in data['leaders'] if 'commander' in leader_id or
                                    'hero' in leader_id))
                if faction != 'keleresm' and faction != 'keleresx' and faction != 'keleresa':
                    self.assertEqual(len(player.technologies),
                                     len(data.get('startingTech') or ()) or
                                     data.get('startingTechAmount', 0))

    def test_nomad_flagship_and_naaz_mech_use_faction_profiles(self):
        for faction, kind, expected_profile in (
            ('nomad', 'flagship', 'nomad_flagship'),
            ('naaz', 'mech', 'naaz_mech'),
        ):
            with self.subTest(faction=faction):
                config, board = load_board(ROOT / 'maps/four_player.json',
                                           (faction, 'sol', 'jolnar', 'hacan'))
                unit = next(unit for tile in board.values() for unit in tile.units
                            if unit.owner == faction and unit.kind == kind)
                self.assertEqual(unit_profile(unit)['id'], expected_profile)
                self.assertEqual(unit.location.region,
                                 Region.SPACE if kind == 'flagship' else Region.PLANET)

    def test_naaz_mech_can_use_its_space_side_only_with_the_space_profile(self):
        from units import Unit, UnitLocation
        valid = Unit('eidolon-space', 'mech', 'naaz', 'grn',
                     UnitLocation(Region.SPACE), profile_id='naaz_mech_space')
        self.assertTrue(unit_profile(valid)['isShip'])
        with self.assertRaisesRegex(ValueError, 'Ground units'):
            Unit('eidolon-no-space-side', 'mech', 'naaz', 'grn',
                 UnitLocation(Region.SPACE))

    def test_argent_starting_technology_choice_is_honored(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('argent', 'sol', 'jolnar', 'hacan'))
        config['starting_technology_choices'] = {'argent': ['nm', 'ps']}
        self.assertEqual(create_players(board, config)[0].technologies,
                         frozenset({'nm', 'ps'}))
        config['starting_technology_choices'] = {'argent': ['nm', 'nm']}
        with self.assertRaisesRegex(ValueError, 'Invalid starting technologies'):
            create_players(board, config)

    def test_keleres_requires_an_unselected_parent_and_one_variant(self):
        map_path = ROOT / 'maps/four_player.json'
        with self.assertRaisesRegex(ValueError, 'unchosen faction'):
            load_board(map_path, ('keleresa', 'argent', 'sol', 'hacan'))
        with self.assertRaisesRegex(ValueError, 'Only one Council Keleres'):
            load_board(map_path, ('keleresa', 'keleresm', 'sol', 'hacan'))

    def test_menu_skips_unavailable_keleres_variants(self):
        menu = MainMenu()
        menu.player_count = 4
        menu.factions[0] = 'keleresa'
        menu.factions[1] = 'titans'
        menu.select_faction(1, 1)
        self.assertEqual(menu.factions[1], 'arborec')


class LeaderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board(ROOT / 'maps/four_player.json',
                                            ('argent', 'sol', 'jolnar', 'hacan'))
        self.players = create_players(self.board, self.config)
        self.argent = self.players[0]

    def test_agents_ready_each_round_and_heroes_purge_only_once(self):
        self.assertTrue(exhaust_leader(self.argent, 'argentagent'))
        self.assertFalse(leader_available(self.argent, 'argentagent'))
        ready_leaders(self.argent)
        self.assertTrue(leader_available(self.argent, 'argentagent'))
        for _ in range(3):
            record_scored_objective(self.argent)
        self.assertTrue(leader_available(self.argent, 'argenthero'))
        self.assertTrue(purge_leader(self.argent, 'argenthero'))
        ready_leaders(self.argent)
        self.assertFalse(leader_available(self.argent, 'argenthero'))

    def test_board_condition_unlocks_argent_commander(self):
        from units import Unit, UnitLocation
        home = next(tile for tile in self.board.values() if tile.system_id == '58')
        self.assertFalse(leader_available(self.argent, 'argentcommander'))
        for index in range(4):
            home.units.append(Unit(f'extra-destroyer-{index}', 'destroyer',
                                   'argent', self.argent.color_code,
                                   UnitLocation(Region.SPACE)))
        self.assertIn('argentcommander', refresh_leader_unlocks(self.argent, self.board))
        self.assertTrue(leader_available(self.argent, 'argentcommander'))

    def test_cabal_commander_counts_dimensional_tears_and_unlocks_on_construction(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('cabal', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        cabal = players[0]
        home = next(tile for tile in board.values() if any(
            planet.faction_homeworld == 'cabal' for planet in tile.planets))
        self.assertTrue(home.has_dimensional_tear)
        self.assertTrue(home.is_gravity_rift)
        self.assertFalse(leader_available(cabal, 'cabalcommander'))

        available = [tile for tile in board.values()
                     if tile.player is None and tile.planets and not tile.units][:2]
        self.assertEqual(len(available), 2)
        for tile in available:
            planet = tile.planets[0]
            tile.planet_owners[planet.planet_id] = cabal.faction
            cabal.planets.append(PlanetCard(planet))
        second, third = available
        second.units.append(Unit('second-dimensional-tear', 'spacedock', 'cabal',
                                 cabal.color_code,
                                 UnitLocation(Region.PLANET, second.planets[0].planet_id)))
        unitless_rift = next(tile for tile in board.values()
                             if 'gravity_rift' in tile.anomalies and tile.planets)
        unitless_rift.planet_owners[unitless_rift.planets[0].planet_id] = cabal.faction
        self.assertEqual([], refresh_leader_unlocks(cabal, board, players))

        turn = TurnOrder(players)
        movement = MovementController(board, players)
        strategy = StrategyController(board, turn, movement)
        strategy.session = StrategyResolution(4, cabal, cabal, [], stage='construction',
                                              selected_system=third.position)
        strategy.build(third.planets[0].planet_id)
        self.assertTrue(third.has_dimensional_tear)
        self.assertTrue(movement.is_gravity_rift(third))
        self.assertTrue(leader_available(cabal, 'cabalcommander'))
        dock = next(unit for unit in third.units if unit.kind == 'spacedock')
        production = movement.start_strategy_production(cabal, third, dock)
        movement.adjust_production('fighter', 1)
        self.assertEqual(2, production.production_choices['fighter'])
        self.assertEqual(0, movement.production_total(production))

        third.units.clear()
        self.assertFalse(third.has_dimensional_tear)
        self.assertFalse(third.is_gravity_rift)


if __name__ == '__main__':
    unittest.main()
