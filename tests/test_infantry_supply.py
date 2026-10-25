"""Infantry plastic anchors and token stacks follow the Living Rules Reference."""

import unittest

from board import ROOT, load_board
from exploration import ExplorationController
from movement import MovementController, Session, Snapshot
from player import create_players
from technology import restore_infantry_on_cards
from units import Region, Unit, UnitLocation, layout_units
from unit_supply import (FIGHTER_SUPPLY, INFANTRY_SUPPLY, can_place_fighter,
                         can_place_infantry, can_transfer_fighters,
                         can_transfer_infantry,
                         fighter_area_counts, fighter_areas_remaining,
                         infantry_area_counts, infantry_count, infantry_remaining,
                         unit_supply_remaining)


class InfantrySupplyTests(unittest.TestCase):
    def game(self, factions=('sol', 'jolnar', 'hacan', 'letnev')):
        config, board = load_board(ROOT / 'maps/four_player.json', factions)
        players = create_players(board, config)
        return board, players, MovementController(board, players)

    @staticmethod
    def player(players, faction):
        return next(player for player in players if player.faction == faction)

    @staticmethod
    def home_planet(board, faction):
        tile = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == faction for planet in tile.planets))
        planet = next(planet for planet in tile.planets
                      if tile.planet_owners.get(planet.planet_id) == faction)
        return tile, planet

    @staticmethod
    def clear_infantry(board, player):
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == player.faction and unit.kind == 'infantry')]

    @staticmethod
    def add_infantry(tile, player, location, count, prefix='supply'):
        tile.units.extend(Unit(f'{player.faction}-{prefix}-{index}', 'infantry',
                               player.faction, player.color_code, location)
                          for index in range(count))

    def test_production_can_make_one_hundred_infantry_tokens_on_one_planet(self):
        board, players, movement = self.game()
        sol = self.player(players, 'sol')
        home, planet = self.home_planet(board, sol.faction)
        self.clear_infantry(board, sol)
        self.add_infantry(home, sol, UnitLocation(Region.PLANET, planet.planet_id), 1, 'anchor')

        session = Session(sol, home, {}, Snapshot.capture(board, sol, players))
        session.stage = 'production'
        session.production_limit = 100
        session.production_sites = [(planet.planet_id, 100)]
        session.trade_goods_to_spend = 50
        sol.trade_goods = 50
        movement.session = session

        for _ in range(50):
            movement.adjust_production('infantry', 1)
        self.assertEqual(session.production_choices['infantry'], 100)
        movement.produce()

        self.assertEqual(infantry_count(sol, board), 101)
        self.assertEqual(len(infantry_area_counts(sol, board)), 1)
        infantry_placements = [placement for placement in layout_units(home)
                               if placement.kind == 'infantry']
        self.assertEqual(sum(len(placement.units) for placement in infantry_placements), 101)
        self.assertTrue(any(placement.badge_count == 101 for placement in infantry_placements))

    def test_twelve_plastic_anchors_limit_distinct_areas_not_token_count(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        self.clear_infantry(board, sol)
        planets = [(tile, planet) for tile in board.values() for planet in tile.planets]
        self.assertGreaterEqual(len(planets), INFANTRY_SUPPLY + 1)

        for index, (tile, planet) in enumerate(planets[:INFANTRY_SUPPLY]):
            self.add_infantry(tile, sol, UnitLocation(Region.PLANET, planet.planet_id),
                              1, f'anchor-{index}')

        self.assertEqual(infantry_remaining(sol, board), 0)
        self.assertTrue(can_place_infantry(
            sol, board, planets[0][0], UnitLocation(Region.PLANET, planets[0][1].planet_id)))
        self.assertFalse(can_place_infantry(
            sol, board, planets[INFANTRY_SUPPLY][0],
            UnitLocation(Region.PLANET, planets[INFANTRY_SUPPLY][1].planet_id)))

    def test_fighter_tokens_need_one_of_ten_plastic_anchors_per_system(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == sol.faction and unit.kind == 'fighter')]
        systems = list(board.values())
        self.assertGreaterEqual(len(systems), FIGHTER_SUPPLY + 1)
        for index, tile in enumerate(systems[:FIGHTER_SUPPLY]):
            tile.units.append(Unit(f'fighter-anchor-{index}', 'fighter', sol.faction,
                                   sol.color_code, UnitLocation(Region.SPACE)))

        self.assertEqual(len(fighter_area_counts(sol, board)), FIGHTER_SUPPLY)
        self.assertEqual(fighter_areas_remaining(sol, board), 0)
        self.assertTrue(can_place_fighter(sol, board, systems[0], UnitLocation(Region.SPACE)))
        self.assertFalse(can_place_fighter(
            sol, board, systems[FIGHTER_SUPPLY], UnitLocation(Region.SPACE)))

    def test_fighter_tokens_in_an_anchored_system_do_not_use_extra_anchors(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == sol.faction and unit.kind == 'fighter')]
        tile = next(iter(board.values()))
        tile.units.extend(Unit(f'fighter-stack-{index}', 'fighter', sol.faction,
                               sol.color_code, UnitLocation(Region.SPACE))
                          for index in range(10))

        self.assertEqual(len(fighter_area_counts(sol, board)), 1)
        self.assertEqual(fighter_areas_remaining(sol, board), FIGHTER_SUPPLY - 1)
        self.assertTrue(can_place_fighter(sol, board, tile, UnitLocation(Region.SPACE)))

    def test_moving_fighters_cannot_create_an_eleventh_anchored_system(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == sol.faction and unit.kind == 'fighter')]
        systems = list(board.values())
        self.assertGreaterEqual(len(systems), FIGHTER_SUPPLY + 1)
        for index, tile in enumerate(systems[:FIGHTER_SUPPLY]):
            tile.units.append(Unit(f'fighter-transfer-{index}', 'fighter', sol.faction,
                                   sol.color_code, UnitLocation(Region.SPACE)))
        source, destination = systems[0], systems[FIGHTER_SUPPLY]
        fighter = next(unit for unit in source.units if unit.owner == sol.faction and
                       unit.kind == 'fighter')
        transfer = [(source, fighter, destination, UnitLocation(Region.TRANSPORT,
                                                                carrier_id='carrier'))]

        self.assertTrue(can_transfer_fighters(sol, board, transfer))

        source.units.append(Unit('second-source-fighter', 'fighter', sol.faction,
                                 sol.color_code, UnitLocation(Region.SPACE)))
        self.assertFalse(can_transfer_fighters(sol, board, transfer))

    def test_other_unit_reinforcements_use_their_component_limits(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        self.assertEqual(unit_supply_remaining(sol, board, 'carrier'), 4 - sum(
            unit.owner == sol.faction and unit.kind == 'carrier'
            for tile in board.values() for unit in tile.units))
        limit = 5
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == sol.faction and unit.kind == 'dreadnought')]
        home = next(tile for tile in board.values() if any(unit.owner == sol.faction
                                                            for unit in tile.units))
        home.units.extend(Unit(f'dreadnought-supply-{index}', 'dreadnought', sol.faction,
                               sol.color_code, UnitLocation(Region.SPACE))
                          for index in range(limit))

        self.assertEqual(unit_supply_remaining(sol, board, 'dreadnought'), 0)

    def test_transported_infantry_and_space_area_share_an_anchor(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        self.clear_infantry(board, sol)
        tile = next(iter(board.values()))
        tile.units.extend((
            Unit('space-token-a', 'infantry', sol.faction, sol.color_code,
                 UnitLocation(Region.TRANSPORT, carrier_id='carrier-a')),
            Unit('space-token-b', 'infantry', sol.faction, sol.color_code,
                 UnitLocation(Region.TRANSPORT, carrier_id='carrier-b')),
            Unit('space-token-c', 'infantry', sol.faction, sol.color_code,
                 UnitLocation(Region.SPACE_AREA)),
        ))
        sol.infantry_on_cards['inf2'] = 50

        self.assertEqual(infantry_count(sol, board), 3)
        self.assertEqual(len(infantry_area_counts(sol, board)), 1)
        self.assertEqual(infantry_remaining(sol, board), 11)

    def test_transferring_an_infantry_requires_a_new_anchor_only_if_source_stays_occupied(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        self.clear_infantry(board, sol)
        planets = [(tile, planet) for tile in board.values() for planet in tile.planets]
        for index, (tile, planet) in enumerate(planets[:INFANTRY_SUPPLY]):
            count = 2 if index == 0 else 1
            self.add_infantry(tile, sol, UnitLocation(Region.PLANET, planet.planet_id),
                              count, f'anchor-{index}')
        source, source_planet = planets[0]
        moving = next(unit for unit in source.units if unit.unit_id == f'{sol.faction}-anchor-0-0')
        destination, destination_planet = planets[INFANTRY_SUPPLY]
        transfer = (source, moving, destination,
                    UnitLocation(Region.PLANET, destination_planet.planet_id))

        self.assertFalse(can_transfer_infantry(sol, board, [transfer]))
        self.assertFalse(can_transfer_infantry(sol, board, [transfer, transfer]))
        remaining_at_source = next(unit for unit in source.units
                                   if unit.owner == sol.faction and unit.kind == 'infantry' and
                                   unit.location.planet_id == source_planet.planet_id and
                                   unit is not moving)
        whole_group_transfer = (
            source, remaining_at_source, destination,
            UnitLocation(Region.PLANET, destination_planet.planet_id))
        self.assertTrue(can_transfer_infantry(sol, board, [transfer, whole_group_transfer]))

    def test_special_placement_and_exploration_can_add_tokens_to_an_occupied_planet(self):
        board, players, movement = self.game(('sol', 'arborec', 'jolnar', 'hacan'))
        sol = self.player(players, 'sol')
        home, planet = self.home_planet(board, sol.faction)
        self.clear_infantry(board, sol)
        location = UnitLocation(Region.PLANET, planet.planet_id)
        self.add_infantry(home, sol, location, 100, 'existing')

        movement.orbital_drop(sol, planet.planet_id)
        arborec = self.player(players, 'arborec')
        arborec_home, arborec_planet = self.home_planet(board, arborec.faction)
        self.clear_infantry(board, arborec)
        self.add_infantry(arborec_home, arborec,
                          UnitLocation(Region.PLANET, arborec_planet.planet_id), 100, 'existing')
        movement.mitosis(arborec, arborec_planet.planet_id)
        exploration = ExplorationController(board, players)
        self.assertTrue(exploration._place_unit(sol, home, planet.planet_id, 'infantry'))

        self.assertEqual(infantry_count(sol, board), 103)
        self.assertEqual(infantry_count(arborec, board), 101)

    def test_freelancers_can_use_an_infantry_token_on_an_occupied_planet(self):
        board, players, movement = self.game()
        sol = self.player(players, 'sol')
        home, planet = self.home_planet(board, sol.faction)
        self.clear_infantry(board, sol)
        self.add_infantry(home, sol, UnitLocation(Region.PLANET, planet.planet_id), 100, 'existing')
        sol.trade_goods = 1
        exploration = ExplorationController(board, players)
        exploration.freelancer_offer = (sol.faction, home.position, planet.planet_id)

        outcome = exploration.produce_freelancers(
            sol, movement, 'infantry', planet_id=planet.planet_id, trade_goods=1)

        self.assertEqual(outcome['type'], 'unit_produced')
        self.assertEqual(infantry_count(sol, board), 101)

    def test_magen_places_infantry_tokens_for_multiple_structures_on_one_planet(self):
        board, players, movement = self.game()
        sol = self.player(players, 'sol')
        home, planet = self.home_planet(board, sol.faction)
        self.clear_infantry(board, sol)
        self.add_infantry(home, sol, UnitLocation(Region.PLANET, planet.planet_id), 100, 'existing')
        sol.technologies = frozenset({'md'})
        home.units.extend(Unit(f'magen-pds-{index}', 'pds', sol.faction, sol.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id))
                          for index in range(2))
        expected = 100 + sum(unit.owner == sol.faction and unit.kind in ('pds', 'spacedock')
                             for unit in home.units)

        movement._magen_activation(home)

        self.assertEqual(infantry_count(sol, board), expected)

    def test_infantry_ii_survivors_return_to_reinforcements_if_home_is_lost(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == sol.faction for planet in tile.planets))
        starting_count = infantry_count(sol, board)
        for planet in home.planets:
            if planet.faction_homeworld == sol.faction:
                home.planet_owners[planet.planet_id] = 'hacan'
        sol.infantry_on_cards.update({'inf2': 2, 'so2': 1})

        self.assertEqual(restore_infantry_on_cards(sol, board), 3)
        self.assertFalse(sol.infantry_on_cards)
        self.assertEqual(infantry_count(sol, board), starting_count)

    def test_infantry_ii_does_not_remain_on_card_if_no_home_planet_can_anchor_it(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        self.clear_infantry(board, sol)
        home_planets = {planet.planet_id for tile in board.values() for planet in tile.planets
                        if planet.faction_homeworld == sol.faction}
        candidates = [(tile, planet) for tile in board.values() for planet in tile.planets
                      if planet.planet_id not in home_planets]
        self.assertGreaterEqual(len(candidates), INFANTRY_SUPPLY)
        for index, (tile, planet) in enumerate(candidates[:INFANTRY_SUPPLY]):
            tile.planet_owners[planet.planet_id] = sol.faction
            self.add_infantry(tile, sol, UnitLocation(Region.PLANET, planet.planet_id),
                              1, f'anchor-{index}')
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == sol.faction for planet in tile.planets))
        self.assertTrue(any(planet.planet_id in home.planet_owners and
                            home.planet_owners[planet.planet_id] == sol.faction and
                            not any(unit.owner == sol.faction and unit.kind == 'infantry' and
                                    unit.location.planet_id == planet.planet_id
                                    for unit in home.units)
                            for planet in home.planets if planet.planet_id in home_planets))
        sol.infantry_on_cards['inf2'] = 3

        self.assertEqual(restore_infantry_on_cards(sol, board), 0)
        self.assertFalse(sol.infantry_on_cards)
        self.assertEqual(len(infantry_area_counts(sol, board)), INFANTRY_SUPPLY)

    def test_infantry_ii_card_units_restore_as_tokens_without_a_global_cap(self):
        board, players, _movement = self.game()
        sol = self.player(players, 'sol')
        home, planet = self.home_planet(board, sol.faction)
        sol.infantry_on_cards['inf2'] = 100
        starting_count = infantry_count(sol, board)

        self.assertEqual(restore_infantry_on_cards(sol, board), 100)
        self.assertFalse(sol.infantry_on_cards)
        self.assertEqual(infantry_count(sol, board), starting_count + 100)
        self.assertIn((home.position, 'planet', planet.planet_id), infantry_area_counts(sol, board))


if __name__ == '__main__':
    unittest.main()
