"""PoK/Codex III starting setup and leader lifecycle regressions."""

import unittest
from dataclasses import replace

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

    def test_keleres_codex_units_use_their_unique_profiles(self):
        flagship = Unit('keleres-flagship-test', 'flagship', 'keleresm', 'blu',
                        UnitLocation(Region.SPACE))
        mech = Unit('keleres-mech-test', 'mech', 'keleresm', 'blu',
                    UnitLocation(Region.PLANET, 'mollprimusk'))

        flagship_profile = unit_profile(flagship)
        mech_profile = unit_profile(mech)
        self.assertEqual(flagship_profile['id'], 'keleres_flagship')
        self.assertEqual((flagship_profile['moveValue'], flagship_profile['capacityValue'],
                          flagship_profile['combatHitsOn'], flagship_profile['combatDieCount']),
                         (1, 6, 7, 2))
        self.assertEqual(mech_profile['id'], 'keleres_mech')
        self.assertEqual((mech_profile['combatHitsOn'], mech_profile['capacityUsed'],
                          mech_profile['sustainDamage']), (6, 1, True))

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

    def test_ghost_commander_requires_units_in_three_wormhole_systems(self):
        from units import Unit, UnitLocation
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('ghost', 'sol', 'jolnar', 'hacan'))
        ghost = create_players(board, config)[0]
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units if unit.owner != ghost.faction]
            tile.planet_owners = {planet_id: owner for planet_id, owner in tile.planet_owners.items()
                                  if owner != ghost.faction}
        wormhole_systems = [tile for tile in board.values()
                            if {'ALPHA', 'BETA'} & set(tile.wormholes)]
        self.assertGreaterEqual(len(wormhole_systems), 3)
        planet_system = next(tile for tile in wormhole_systems if tile.planets)
        ship_systems = [tile for tile in wormhole_systems if tile is not planet_system][:2]
        self.assertEqual(len(ship_systems), 2)
        for index, tile in enumerate(ship_systems):
            tile.units.append(Unit(f'ghost-ship-{index}', 'cruiser', ghost.faction,
                                   ghost.color_code, UnitLocation(Region.SPACE)))
        planet_system.planet_owners[planet_system.planets[0].planet_id] = ghost.faction

        self.assertEqual([], refresh_leader_unlocks(ghost, board))
        self.assertFalse(leader_available(ghost, 'ghostcommander'))

        planet_system.units.append(Unit('ghost-third-ship', 'cruiser', ghost.faction,
                                        ghost.color_code, UnitLocation(Region.SPACE)))
        self.assertIn('ghostcommander', refresh_leader_unlocks(ghost, board))
        self.assertTrue(leader_available(ghost, 'ghostcommander'))

    def test_empyrean_commander_counts_players_connected_by_matching_wormholes(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('empyrean', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        empyrean = players[0]
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if unit.owner not in {player.faction for player in players}]
            tile.planet_owners.clear()

        alpha, matching = next(
            (left, right)
            for left in board.values() if left.wormholes
            for right in board.values()
            if right is not left and left.wormholes == right.wormholes and
            not any(neighbor is right for neighbor in board.neighbors(left.position)))
        alpha.units.append(Unit('empyrean-alpha-presence', 'cruiser', 'empyrean',
                                empyrean.color_code, UnitLocation(Region.SPACE)))
        for index, player in enumerate(players[1:]):
            matching.units.append(Unit(f'{player.faction}-wormhole-presence-{index}',
                                       'cruiser', player.faction, player.color_code,
                                       UnitLocation(Region.SPACE)))

        original_definition = matching.definition
        matching.definition = replace(original_definition, wormholes=('GAMMA',))
        self.assertEqual([], refresh_leader_unlocks(empyrean, board, players))
        self.assertFalse(leader_available(empyrean, 'empyreancommander'))

        matching.definition = original_definition
        self.assertIn('empyreancommander',
                      refresh_leader_unlocks(empyrean, board, players))
        self.assertTrue(leader_available(empyrean, 'empyreancommander'))

    def test_naalu_commander_uses_wormhole_adjacency_to_mecatol(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('naalu', 'sol', 'jolnar', 'hacan'))
        naalu = create_players(board, config)[0]
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units if unit.owner != 'naalu']
            tile.planet_owners = {planet_id: owner for planet_id, owner in
                                  tile.planet_owners.items() if owner != 'naalu'}
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        lodor = next(tile for tile in board.values() if tile.number == 26)
        planet = lodor.planets[0]
        lodor.units.append(Unit('naalu-commander-ground-force', 'infantry', 'naalu',
                                naalu.color_code, UnitLocation(Region.PLANET,
                                                              planet.planet_id)))

        self.assertEqual([], refresh_leader_unlocks(naalu, board))
        self.assertFalse(leader_available(naalu, 'naalucommander'))

        mecatol.definition = replace(mecatol.definition, wormholes=('ALPHA',))
        self.assertIn('naalucommander', refresh_leader_unlocks(naalu, board))
        self.assertTrue(leader_available(naalu, 'naalucommander'))

    def test_sol_and_xxcha_commander_unlocks_include_exploration_attachments(self):
        from exploration import ExplorationController

        for faction, leader_id, value_index, attachment in (
                ('sol', 'solcommander', 0, 'ds'),
                ('xxcha', 'xxchacommander', 1, 'pw')):
            with self.subTest(faction=faction):
                config, board = load_board(ROOT / 'maps/four_player.json',
                                           (faction, 'sol', 'jolnar', 'hacan')
                                           if faction != 'sol' else
                                           ('sol', 'jolnar', 'hacan', 'argent'))
                players = create_players(board, config)
                player = next(item for item in players if item.faction == faction)
                exploration = ExplorationController(
                    board, players, relic_deck=[],
                    decks={'cultural': [], 'hazardous': [],
                           'industrial': [], 'frontier': []})
                planets = [planet for tile in board.values() for planet in tile.planets]
                values = [planet.resources if value_index == 0 else planet.influence
                          for planet in planets]
                subsets = {0: []}
                for index, value in enumerate(values):
                    for total, indexes in tuple(subsets.items()):
                        new_total = total + value
                        if value and new_total < 12 and new_total not in subsets:
                            subsets[new_total] = indexes + [index]
                qualifying = next((indexes for total, indexes in subsets.items()
                                   if indexes and total + 2 * len(indexes) >= 12), None)
                self.assertIsNotNone(qualifying, 'map must offer a near-threshold planet set')
                selected_ids = {planets[index].planet_id for index in qualifying}
                for tile in board.values():
                    for planet_id, owner in tuple(tile.planet_owners.items()):
                        if owner == faction:
                            del tile.planet_owners[planet_id]
                    for planet in tile.planets:
                        if planet.planet_id in selected_ids:
                            tile.planet_owners[planet.planet_id] = faction
                total_before = sum(values[index] for index in qualifying)
                self.assertLess(total_before, 12)
                self.assertFalse(leader_available(player, leader_id))
                self.assertEqual([], refresh_leader_unlocks(player, board, players))

                for planet_id in selected_ids:
                    exploration.attachments[planet_id] = [attachment]

                self.assertIn(leader_id, refresh_leader_unlocks(
                    player, board, players, exploration))
                self.assertTrue(leader_available(player, leader_id))

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
