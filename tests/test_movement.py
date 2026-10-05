import unittest

from board import load_board
from movement import MovementController, MovementError
from player import PlanetCard, create_players
from units import Region, Unit, UnitLocation


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
        self.assertIsNone(self.controller.session)
        self.assertEqual(len(self.controller.history), 1)
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

        self.assertIsNone(self.controller.session)
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

    def test_production_uses_planet_payment_pair_builds_and_checks_fleet_supply(self):
        planet = self.home.planets[0]
        self.home.units.append(Unit('test-production-base', 'spacedock', self.player.faction,
                                    self.player.color_code,
                                    UnitLocation(Region.PLANET, planet_id=planet.planet_id)))
        self.player.command_pools['fleet'] = 3
        session = self.controller.activate(self.player, self.home.position)
        self.controller.confirm()
        self.controller.establish_control()
        self.assertEqual(session.stage, 'production')
        self.assertEqual(session.production_limit,
                         sum(value for _, value in self.controller.production_sites(session)))
        self.assertEqual(self.controller.unit_cost('mech', self.player), 2)
        production_planet_id = session.production_sites[0][0]
        production_planet = next(planet for planet in self.home.planets
                                 if planet.planet_id == production_planet_id)
        initial_infantry = sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                               unit.location.planet_id == production_planet_id for unit in self.home.units)

        original_limit = session.production_limit
        session.production_limit = 5
        session.production_choices['infantry'] = 1
        self.assertEqual(self.controller.production_cost(session), 1)
        session.production_choices.clear()
        self.controller.adjust_production('infantry', 1)
        self.controller.adjust_production('infantry', 1)
        self.assertEqual(session.production_choices['infantry'], 4)
        self.controller.adjust_production('infantry', -1)
        self.assertEqual(session.production_choices['infantry'], 3)
        self.assertEqual(self.controller.production_cost(session), 2)
        self.controller.adjust_production('infantry', 1)
        self.controller.adjust_production('infantry', 1)
        self.assertEqual(session.production_choices['infantry'], 5)
        session.production_choices.clear()
        session.production_limit = original_limit

        self.controller.adjust_production('dreadnought', 1)
        self.controller.adjust_production('infantry', 1)
        self.assertEqual(self.controller.production_cost(session), 5)
        card = next(card for card in self.player.planets if card.planet.planet_id == production_planet_id)
        self.controller.toggle_production_planet(production_planet_id)
        self.assertTrue(card.exhausted)
        self.player.trade_goods = 5
        self.controller.change_production_trade_goods(5)
        self.assertEqual(self.controller.production_payment(session), production_planet.resources + 5)
        self.assertGreaterEqual(self.controller.production_payment(session), 5)

        self.controller.produce()
        self.assertEqual(session.stage, 'fleet_overflow')
        self.assertEqual(session.overflow_required, 1)
        dreadnought = next(unit for unit in self.home.units if unit.kind == 'dreadnought' and
                           unit.unit_id.startswith('sol-built-'))
        self.controller.toggle_overflow_ship(dreadnought.unit_id)
        self.controller.resolve_fleet_overflow()
        self.assertIsNone(self.controller.session)
        self.assertEqual(sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                             unit.location.planet_id == production_planet_id for unit in self.home.units),
                         initial_infantry + 2)
        self.assertFalse(any(unit.unit_id == dreadnought.unit_id for unit in self.home.units))
        self.assertTrue(card.exhausted)
        self.assertEqual(self.player.trade_goods, 0)
        self.assertTrue(self.controller.undo())
        self.assertEqual(self.home.units.count(dreadnought), 0)
        self.assertEqual(sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                             unit.location.planet_id == production_planet_id for unit in self.home.units),
                         initial_infantry)
        self.assertFalse(card.exhausted)
        self.assertEqual(self.player.trade_goods, 0)

    def test_fleet_limit_after_movement_requires_destroying_exact_excess(self):
        self.player.command_pools['fleet'] = 1
        original_home_units = list(self.home.units)
        session = self.controller.activate(self.player, self.target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        destroyer = next(unit for unit in source.ships if unit.kind == 'destroyer')
        infantry = next(unit for unit in source.passengers if unit.kind == 'infantry')
        session.toggle(carrier.unit_id)
        session.toggle(destroyer.unit_id)
        session.toggle(infantry.unit_id)
        self.controller.confirm()
        self.assertEqual(session.stage, 'fleet_overflow')
        self.assertEqual(session.overflow_required, 1)
        with self.assertRaises(MovementError):
            self.controller.resolve_fleet_overflow()
        self.controller.toggle_overflow_ship(carrier.unit_id)
        self.controller.resolve_fleet_overflow()
        self.assertEqual(session.stage, 'invasion')
        self.assertNotIn(carrier, self.target.units)
        self.assertNotIn(infantry, self.target.units)
        self.controller.establish_control()
        self.assertIsNone(self.controller.session)
        self.assertTrue(self.controller.undo())
        self.assertEqual(self.home.units, original_home_units)
        self.assertIn(carrier, self.home.units)
        self.assertIn(infantry, self.home.units)
        self.assertNotIn('sol', self.target.command_tokens)

    def test_ship_production_goes_to_space_and_skipping_refunds_exhausted_planets(self):
        self.player.command_pools['fleet'] = 4
        planet = self.home.planets[0]
        card = next(card for card in self.player.planets if card.planet.planet_id == planet.planet_id)
        before_ships = sum(unit.kind == 'cruiser' for unit in self.home.units)
        self.player.trade_goods = 2
        session = self.controller.activate(self.player, self.home.position)
        self.controller.confirm()
        self.controller.establish_control()
        self.assertEqual(session.stage, 'production')
        self.controller.adjust_production('cruiser', 1)
        self.controller.change_production_trade_goods(2)
        self.controller.produce()
        produced = [unit for unit in self.home.units if unit.kind == 'cruiser']
        self.assertEqual(len(produced), before_ships + 1)
        self.assertEqual(produced[-1].location.region, Region.SPACE)
        self.assertEqual(self.player.trade_goods, 0)
        self.assertTrue(self.controller.undo())
        self.assertEqual(sum(unit.kind == 'cruiser' for unit in self.home.units), before_ships)
        self.assertEqual(self.player.trade_goods, 2)

        session = self.controller.activate(self.player, self.home.position)
        self.controller.confirm()
        self.controller.establish_control()
        self.controller.toggle_production_planet(planet.planet_id)
        self.assertTrue(card.exhausted)
        self.controller.skip_production()
        self.assertFalse(card.exhausted)
        self.assertIsNone(self.controller.session)


if __name__ == '__main__':
    unittest.main()
