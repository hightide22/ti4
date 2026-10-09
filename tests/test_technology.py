import unittest
import json
from pathlib import Path
from unittest.mock import patch

from board import RESOURCES, load_board
from movement import MovementController, MovementError, Session, Snapshot
from player import create_players
from strategic_action import StrategyController
from technology import (available_technologies, missing_prerequisites, research_technology,
                        restore_infantry_on_cards, technology_catalog, technology_image,
                        unit_stats, unit_upgrade)
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation, unit_profile, unit_profiles


class TechnologyTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.by_faction = {player.faction: player for player in self.players}
        self.movement = MovementController(self.board, self.players)

    def test_every_available_card_has_a_bundled_scan(self):
        catalog = technology_catalog()
        self.assertEqual(len(catalog), 43)
        self.assertTrue(all(technology_image(card).is_file() for card in catalog.values()))
        self.assertTrue(all(card['source'] == 'base' for card in catalog.values()))
        self.assertNotIn('aida', catalog)
        self.assertNotIn('bs', catalog)
        self.assertEqual(len(available_technologies(self.by_faction['sol'])), 25)
        for faction in ('arborec', 'saar', 'muaat', 'l1z1x', 'ghost'):
            player = type(self.by_faction['sol'])(faction, faction, 'blu', 3)
            self.assertTrue(available_technologies(player), faction)
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'assets/resources.lock.json')
                              .read_text(encoding='utf-8'))
        pinned = {entry['path'] for entry in manifest['files']}
        self.assertTrue({technology_image(card).relative_to(RESOURCES).as_posix()
                         for card in catalog.values()} <= pinned)
        definitions, _ = unit_profiles()
        self.assertTrue(all(profile.get('source') == 'base' for profile in definitions.values()))

    def test_research_replaces_existing_and_future_sol_carriers(self):
        sol = self.by_faction['sol']
        self.assertEqual(unit_upgrade(sol, 'carrier')['alias'], 'ac2')
        self.assertNotIn('cv2', {tech['alias'] for tech in available_technologies(sol)})
        carrier = next(unit for tile in self.board.values() for unit in tile.units
                       if unit.owner == 'sol' and unit.kind == 'carrier')
        self.assertEqual(carrier.capacity, 6)
        research_technology(sol, 'ac2', self.board)
        self.assertEqual(carrier.capacity, 8)
        self.assertEqual(carrier.move_value, 2)
        self.assertEqual(unit_profile(carrier)['name'], 'Advanced Carrier II')
        self.assertEqual(unit_stats(sol, 'carrier')['name'], 'Advanced Carrier II')

    def test_jol_nar_analytical_and_planet_specialties_satisfy_prerequisites(self):
        catalog = technology_catalog()
        sol, jolnar = self.by_faction['sol'], self.by_faction['jolnar']
        self.assertEqual(missing_prerequisites(sol, catalog['fl']), 1)
        self.assertEqual(missing_prerequisites(jolnar, catalog['fl']), 0)
        self.assertEqual(missing_prerequisites(sol, catalog['cv2']), 1)

    def test_technology_primary_free_then_six_resource_purchase(self):
        sol = self.by_faction['sol']
        sol.trade_goods = 2
        turn = TurnOrder(self.players)
        turn.strategy_assignments['sol'] = [7]
        controller = StrategyController(self.board, turn, self.movement)
        controller.start(7)
        self.assertEqual(controller.technology_cost(), 0)
        controller.select_technology('gd')
        controller.research_selected()
        self.assertIn('gd', sol.technologies)
        self.assertEqual(controller.technology_cost(), 6)
        controller.select_technology('st')
        controller.toggle_technology_planet('jord')
        controller.change_goods(2)
        controller.research_selected()
        self.assertIn('st', sol.technologies)
        self.assertEqual(sol.trade_goods, 0)
        self.assertTrue(sol.planets[0].exhausted)
        self.assertEqual(controller.session.player.faction, 'jolnar')
        self.assertEqual(controller.session.stage, 'offer')

    def test_jol_nar_brilliant_uses_primary_effect_as_secondary(self):
        turn = TurnOrder(self.players)
        turn.strategy_assignments['sol'] = [7]
        controller = StrategyController(self.board, turn, self.movement)
        controller.start(7)
        controller.continue_stage()
        self.assertEqual(controller.session.player.faction, 'jolnar')
        before = self.by_faction['jolnar'].command_pools['strategic']
        controller.accept_secondary(brilliant=True)
        self.assertEqual(self.by_faction['jolnar'].command_pools['strategic'], before - 1)
        self.assertEqual(controller.technology_cost(), 0)
        controller.select_technology('gd')
        controller.research_selected()
        self.assertEqual(controller.technology_cost(), 6)

    def test_war_sun_requires_research_before_production(self):
        sol = self.by_faction['sol']
        home = next(tile for tile in self.board.values() if tile.player and 'Sol' in tile.player)
        self.movement.activate(sol, home.position)
        self.movement.confirm()
        self.movement.establish_control()
        self.assertEqual(self.movement.session.stage, 'production')
        with self.assertRaisesRegex(MovementError, 'Research War Sun'):
            self.movement.adjust_production('warsun', 1)
        self.movement.cancel()

    def test_spec_ops_survivor_returns_to_controlled_home_at_next_turn(self):
        sol = self.by_faction['sol']
        research_technology(sol, 'so2', self.board)
        home = next(tile for tile in self.board.values()
                    if any(p.faction_homeworld == 'sol' for p in tile.planets))
        infantry = next(unit for unit in home.units if unit.owner == 'sol' and unit.kind == 'infantry')
        home.units.remove(infantry)
        sol.infantry_on_cards['so2'] = 1
        self.assertEqual(restore_infantry_on_cards(sol, self.board), 1)
        self.assertFalse(sol.infantry_on_cards)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'infantry'
                             for unit in home.units), 5)
        self.assertTrue(all(unit.profile_id == 'sol_infantry2' for unit in home.units
                            if unit.owner == 'sol' and unit.kind == 'infantry'))

    def test_sarween_tools_reduce_base_game_production_cost(self):
        sol = self.by_faction['sol']
        sol.technologies |= {'st'}
        home = next(tile for tile in self.board.values()
                    if any(p.faction_homeworld == 'sol' for p in tile.planets))
        self.movement.activate(sol, home.position)
        self.movement.confirm()
        self.movement.establish_control()
        self.movement.adjust_production('cruiser', 1)
        self.assertEqual(self.movement.production_cost(self.movement.session), 1)

    def test_non_euclidean_shielding_cancels_two_space_cannon_hits(self):
        sol = self.by_faction['sol']
        sol.technologies |= {'nes'}
        hacan = self.by_faction['hacan']
        home = next(tile for tile in self.board.values()
                    if any(p.faction_homeworld == 'sol' for p in tile.planets))
        dread = Unit('tech-test-dread', 'dreadnought', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE))
        home.units.append(dread)
        for index in range(2):
            home.units.append(Unit(f'tech-test-pds-{index}', 'pds', 'hacan', hacan.color_code,
                                   UnitLocation(Region.PLANET, planet_id='jord')))
        session = Session(sol, home, {}, Snapshot.capture(self.board, sol, self.players))
        with patch('movement.random.randint', return_value=10):
            self.movement.resolve_space_cannon(session)
        self.assertIn(dread, home.units)
        self.assertTrue(dread.damaged)
        self.assertTrue(any('Non-Euclidean' in line for line in session.cannon_log))

    def test_assault_cannon_defender_chooses_ship_to_destroy(self):
        sol = self.by_faction['sol']
        sol.technologies |= {'asc'}
        hacan = self.by_faction['hacan']
        target = next(tile for tile in self.board.values() if not tile.units)
        target.units.extend(Unit(f'test-sol-{index}', 'cruiser', 'sol', sol.color_code,
                                 UnitLocation(Region.SPACE)) for index in range(3))
        cruiser = Unit('test-hacan-cruiser', 'cruiser', 'hacan', hacan.color_code,
                       UnitLocation(Region.SPACE))
        dread = Unit('test-hacan-dread', 'dreadnought', 'hacan', hacan.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((cruiser, dread))
        session = Session(sol, target, {}, Snapshot.capture(self.board, sol, self.players))
        self.movement.session = session
        self.movement.start_combat(session)
        self.assertEqual(session.stage, 'assault_choice')
        self.assertEqual({unit.unit_id for unit in self.movement.assault_victims(session)},
                         {cruiser.unit_id, dread.unit_id})
        self.movement.choose_assault_victim(dread.unit_id)
        self.assertNotIn(dread, target.units)
        self.assertIn(cruiser, target.units)
        self.assertEqual(session.stage, 'space_combat')


if __name__ == '__main__':
    unittest.main()
