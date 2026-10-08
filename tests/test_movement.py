import unittest
from unittest.mock import patch

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

    def test_active_system_can_load_infantry_to_move_between_its_planets(self):
        target = next(tile for tile in self.controller.neighbors(self.home)
                      if len(tile.planets) >= 2 and not tile.command_tokens and
                      not any(unit.owner != self.player.faction and
                              unit.location.region == Region.SPACE for unit in tile.units))
        source_planet, destination_planet = target.planets[:2]
        carrier = Unit('local-planet-transfer-carrier', 'carrier', self.player.faction,
                       self.player.color_code, UnitLocation(Region.SPACE))
        infantry = Unit('local-planet-transfer-infantry', 'infantry', self.player.faction,
                        self.player.color_code,
                        UnitLocation(Region.PLANET, planet_id=source_planet.planet_id))
        target.units.extend((carrier, infantry))

        session = self.controller.activate(self.player, target.position)
        self.assertIn(target.position, session.sources)
        source = session.sources[target.position]
        self.assertIn(carrier, source.ships)
        self.assertIn(infantry, source.passengers)
        session.toggle(carrier.unit_id)
        session.toggle(infantry.unit_id)
        self.controller.confirm()

        self.assertEqual(session.stage, 'invasion')
        self.assertEqual(infantry.location.region, Region.TRANSPORT)
        self.controller.cycle_landing(infantry.unit_id)
        self.controller.cycle_landing(infantry.unit_id)
        self.assertEqual(session.landings[infantry.unit_id], destination_planet.planet_id)
        self.controller.establish_control()
        self.assertEqual(infantry.location,
                         UnitLocation(Region.PLANET, planet_id=destination_planet.planet_id))
        self.assertIsNone(self.controller.session)
        self.assertTrue(self.controller.undo())
        self.assertEqual(infantry.location,
                         UnitLocation(Region.PLANET, planet_id=source_planet.planet_id))
        self.assertIn(carrier, target.units)

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
        with self.assertRaises(MovementError):
            self.controller.adjust_production('mech', 1)
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
        with self.assertRaises(MovementError):
            self.controller.adjust_production('pds', 1)
        with self.assertRaises(MovementError):
            self.controller.adjust_production('spacedock', 1)
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

    def test_unupgraded_fighters_without_capacity_must_be_destroyed_after_movement(self):
        self.target.units.clear()
        fighters = [Unit(f'capacity-fighter-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(2)]
        self.target.units.extend(fighters)
        session = self.controller.activate(self.player, self.target.position)

        self.controller.confirm()

        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_required, 2)
        self.assertEqual({unit.unit_id for unit in self.controller.capacity_overflow_units(
            self.target, 'sol')}, {unit.unit_id for unit in fighters})
        for unit in fighters:
            self.controller.toggle_capacity_overflow_unit(unit.unit_id)
        self.controller.resolve_capacity_overflow()
        self.assertNotIn(session.stage, ('capacity_overflow', 'fleet_overflow'))
        self.assertFalse(any(unit.kind == 'fighter' and unit.owner == 'sol'
                             for unit in self.target.units))

    def test_transported_infantry_and_fighters_share_capacity_but_planet_forces_do_not(self):
        self.target.units.clear()
        carrier = Unit('capacity-test-carrier', 'carrier', 'sol', self.player.color_code,
                       UnitLocation(Region.SPACE))
        fighters = [Unit(f'capacity-test-fighter-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(7)]
        aboard = Unit('capacity-test-infantry-aboard', 'infantry', 'sol', self.player.color_code,
                      UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        planet_force = Unit('capacity-test-infantry-planet', 'infantry', 'sol', self.player.color_code,
                            UnitLocation(Region.PLANET, planet_id=self.home.planets[0].planet_id))
        self.target.units.extend([carrier, *fighters, aboard, planet_force])
        session = self.controller.activate(self.player, self.target.position)

        self.controller.confirm()

        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_required, 2)
        self.assertNotIn(planet_force, self.controller.capacity_overflow_units(self.target, 'sol'))
        self.controller.toggle_capacity_overflow_unit(aboard.unit_id)
        self.controller.toggle_capacity_overflow_unit(fighters[0].unit_id)
        self.controller.resolve_capacity_overflow()
        self.assertNotIn(aboard, self.target.units)
        self.assertNotIn(fighters[0], self.target.units)
        self.assertIn(planet_force, self.target.units)

    def test_departing_capacity_ship_triggers_check_in_the_origin_system(self):
        self.home.units.clear()
        self.target.units.clear()
        carrier = Unit('departing-capacity-carrier', 'carrier', 'sol', self.player.color_code,
                       UnitLocation(Region.SPACE))
        fighters = [Unit(f'origin-capacity-fighter-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(carrier.capacity)]
        self.home.units.extend([carrier, *fighters])
        session = self.controller.activate(self.player, self.target.position)
        session.toggle(carrier.unit_id)

        self.controller.confirm()

        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_position, self.home.position)
        self.assertEqual(session.capacity_required, len(fighters))
        self.controller.cancel()

    def test_fighter_ii_over_capacity_uses_fleet_supply_instead_of_capacity_overflow(self):
        self.target.units.clear()
        self.player.technologies = self.player.technologies | {'ff2'}
        self.player.command_pools['fleet'] = 4
        carrier = Unit('fighter-ii-carrier', 'carrier', 'sol', self.player.color_code,
                       UnitLocation(Region.SPACE))
        fighters = [Unit(f'fighter-ii-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(10)]
        self.target.units.extend([carrier, *fighters])
        session = self.controller.activate(self.player, self.target.position)

        self.controller.confirm()

        self.assertEqual(self.controller.capacity_overflow(self.target, 'sol'), 0)
        self.assertEqual(session.stage, 'fleet_overflow')
        self.assertEqual(session.overflow_required, 1)
        self.assertTrue(any(unit.kind == 'fighter' for unit in self.controller.fleet_ships(session)))
        fighter_to_remove = next(unit for unit in self.controller.fleet_ships(session)
                                 if unit.kind == 'fighter')
        self.controller.toggle_overflow_ship(fighter_to_remove.unit_id)
        self.controller.resolve_fleet_overflow()
        self.assertNotIn(fighter_to_remove, self.target.units)
        self.assertNotEqual(session.stage, 'capacity_overflow')

    def test_space_dock_provides_three_fighter_capacity_without_capacity_for_infantry(self):
        self.target.units.clear()
        dock = Unit('fighter-capacity-dock', 'spacedock', 'sol', self.player.color_code,
                    UnitLocation(Region.PLANET, planet_id=self.home.planets[0].planet_id))
        fighters = [Unit(f'dock-fighter-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(4)]
        self.target.units.extend([dock, *fighters])
        session = self.controller.activate(self.player, self.target.position)

        self.controller.confirm()

        self.assertEqual(self.controller.capacity_overflow(self.target, 'sol'), 1)
        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_required, 1)

    def test_fighter_production_checks_capacity_before_finishing_activation(self):
        self.home.units.clear()
        planet = self.home.planets[0]
        dock = Unit('production-capacity-dock', 'spacedock', 'sol', self.player.color_code,
                    UnitLocation(Region.PLANET, planet_id=planet.planet_id))
        self.home.units.append(dock)
        self.player.trade_goods = 2
        session = self.controller.activate(self.player, self.home.position)
        self.controller.confirm()
        self.controller.establish_control()
        self.assertEqual(session.stage, 'production')
        session.production_choices['fighter'] = 4
        self.controller.change_production_trade_goods(2)

        self.controller.produce()

        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_required, 1)
        fighter = self.controller.capacity_overflow_units(self.home, 'sol')[0]
        self.controller.toggle_capacity_overflow_unit(fighter.unit_id)
        self.controller.resolve_capacity_overflow()
        self.assertIsNone(self.controller.session)
        self.assertEqual(sum(unit.kind == 'fighter' and unit.owner == 'sol'
                             for unit in self.home.units), 3)

    def test_capacity_is_rechecked_after_space_combat_destroys_capacity_ship(self):
        self.target.units.clear()
        carrier = Unit('combat-capacity-carrier', 'carrier', 'sol', self.player.color_code,
                       UnitLocation(Region.SPACE))
        fighters = [Unit(f'combat-capacity-fighter-{index}', 'fighter', 'sol', self.player.color_code,
                         UnitLocation(Region.SPACE)) for index in range(2)]
        enemy = Unit('combat-capacity-enemy', 'cruiser', 'hacan', 'ylw', UnitLocation(Region.SPACE))
        self.target.units.extend([carrier, *fighters, enemy])
        session = self.controller.activate(self.player, self.target.position)
        session.combat_type = 'space'
        session.combat_factions = ('sol', 'hacan')
        session.stage = 'combat_end'
        self.target.units.remove(carrier)
        self.target.units.remove(enemy)

        self.controller._finish_combat_round(session)

        self.assertEqual(session.stage, 'capacity_overflow')
        self.assertEqual(session.capacity_required, 2)

    def test_space_combat_rolls_hits_sustains_damage_and_destroys_carrier_cargo(self):
        self.player.command_pools['fleet'] = 6
        defender = Unit('hacan-test-dread', 'dreadnought', 'hacan', 'ylw', UnitLocation(Region.SPACE))
        self.target.units.append(defender)
        session = self.controller.activate(self.player, self.target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = next(unit for unit in source.passengers if unit.kind == 'infantry')
        session.toggle(carrier.unit_id)
        session.toggle(infantry.unit_id)
        self.controller.confirm()
        self.assertEqual(session.stage, 'space_combat')

        with patch('movement.random.randint', side_effect=(10, 1)):
            self.controller.advance_combat()
        self.assertEqual(session.combat_hits, {'sol': 0, 'hacan': 1})
        self.controller.assign_combat_hit('hacan', 'dreadnought')
        self.assertTrue(self.controller.combat_assignments_complete(session))
        self.controller.advance_combat()
        self.assertTrue(defender.damaged)
        self.assertEqual(session.stage, 'space_combat')

        with patch('movement.random.randint', side_effect=(1, 10)):
            self.controller.advance_combat()
        self.controller.assign_combat_hit('sol', 'carrier')
        self.controller.advance_combat()
        self.assertEqual(session.stage, 'invasion')
        self.assertNotIn(carrier, self.target.units)
        self.assertNotIn(infantry, self.target.units)
        self.controller.establish_control()
        self.assertIsNone(self.controller.session)

        self.assertTrue(self.controller.undo())
        self.assertIs(self.controller.session, session)
        self.assertEqual(session.stage, 'invasion')
        self.assertIn(defender, self.target.units)
        self.assertTrue(defender.damaged)
        self.assertNotIn(carrier, self.home.units)
        self.assertNotIn(carrier, self.target.units)

    def test_undo_after_combat_and_production_returns_to_production_checkpoint(self):
        planet = self.home.planets[0]
        base = Unit('test-undo-production-base', 'spacedock', self.player.faction,
                    self.player.color_code, UnitLocation(Region.PLANET, planet_id=planet.planet_id))
        self.home.units.append(base)
        session = self.controller.activate(self.player, self.home.position)
        self.controller.confirm()
        session.rolled_any_dice = True  # Represents a combat roll earlier in this activation.
        self.controller.establish_control()
        self.assertEqual(session.stage, 'production')
        self.controller.adjust_production('infantry', 1)
        self.controller.toggle_production_planet(planet.planet_id)
        self.controller.produce()
        self.assertIsNone(self.controller.session)
        self.assertTrue(any(unit.kind == 'infantry' and unit.unit_id.startswith('sol-built-')
                            for unit in self.home.units))

        self.assertTrue(self.controller.undo())
        self.assertIs(self.controller.session, session)
        self.assertEqual(session.stage, 'production')
        self.assertFalse(any(unit.kind == 'infantry' and unit.unit_id.startswith('sol-built-')
                             for unit in self.home.units))
        self.assertEqual(session.production_choices, {})
        self.assertEqual(session.production_planets, set())
        self.assertFalse(self.player.planets[0].exhausted)

    def test_space_cannon_fires_before_combat_and_damages_entering_fleet(self):
        self.target = next(tile for tile in self.controller.neighbors(self.home)
                           if tile.planets and not tile.command_tokens and
                           any(self.controller.route(self.home, tile, unit, self.player)
                               for unit in self.home.units if unit.owner == 'sol' and unit.kind == 'carrier'))
        pds = Unit('hacan-space-cannon', 'pds', 'hacan', 'ylw',
                   UnitLocation(Region.PLANET, planet_id=self.target.planets[0].planet_id))
        self.target.units.append(pds)
        session = self.controller.activate(self.player, self.target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        session.toggle(carrier.unit_id)

        with patch('movement.random.randint', return_value=10):
            self.controller.confirm()

        self.assertTrue(session.cannon_checked)
        self.assertTrue(any('destroyed' in entry for entry in session.cannon_log))
        self.assertNotIn(carrier, self.target.units)
        self.assertEqual(session.stage, 'invasion')

    def test_anti_fighter_barrage_rolls_once_before_space_combat(self):
        destroyer = next(unit for unit in self.home.units if unit.owner == 'sol' and unit.kind == 'destroyer')
        enemy_carrier = Unit('hacan-afb-carrier', 'carrier', 'hacan', 'ylw', UnitLocation(Region.SPACE))
        enemy_fighters = [Unit(f'hacan-afb-fighter-{index}', 'fighter', 'hacan', 'ylw',
                               UnitLocation(Region.SPACE)) for index in range(2)]
        self.target.units.extend([enemy_carrier, *enemy_fighters])
        session = self.controller.activate(self.player, self.target.position)
        session.toggle(destroyer.unit_id)

        with patch('movement.random.randint', return_value=10):
            self.controller.confirm()

        self.assertEqual(len(session.afb_rolls['sol']), 2)
        self.assertTrue(all(unit not in self.target.units for unit in enemy_fighters))
        self.assertTrue(session.afb_resolved)
        self.assertFalse(session.space_combat_resolved)
        self.assertEqual(session.stage, 'space_combat')

    def test_retreat_can_use_system_with_own_token_without_spending_another(self):
        destroyer = next(unit for unit in self.home.units if unit.owner == 'sol' and unit.kind == 'destroyer')
        defender = Unit('hacan-retreat-cruiser', 'cruiser', 'hacan', 'ylw', UnitLocation(Region.SPACE))
        self.target.units.append(defender)
        tactical = self.player.command_pools['tactical']
        session = self.controller.activate(self.player, self.target.position)
        session.toggle(destroyer.unit_id)
        self.controller.confirm()
        self.assertEqual(session.stage, 'space_combat')
        self.controller.announce_retreat()
        with patch('movement.random.randint', return_value=1):
            self.controller.advance_combat()
        self.controller.advance_combat()
        self.assertEqual(session.stage, 'retreat_selection')

        self.home.command_tokens.add('sol')
        self.assertIn(self.home, self.controller.retreat_options(session, 'sol'))
        self.controller.resolve_retreat(self.home.position)
        self.assertEqual(session.stage, 'invasion')
        self.assertIn('sol', self.home.command_tokens)
        self.assertEqual(self.player.command_pools['tactical'], tactical - 1)
        self.assertTrue(session.space_combat_resolved)

    def test_bombardment_assigns_planet_and_removes_enemy_ground_force(self):
        planet = self.target.planets[0]
        planet_owner = 'hacan'
        self.target.planet_owners[planet.planet_id] = planet_owner
        defender = Unit('hacan-bombard-infantry', 'infantry', planet_owner, 'ylw',
                        UnitLocation(Region.PLANET, planet_id=planet.planet_id))
        dreadnought = Unit('sol-bombard-dread', 'dreadnought', 'sol', 'blu', UnitLocation(Region.SPACE))
        self.target.units.extend((defender, dreadnought))
        session = self.controller.activate(self.player, self.target.position)
        self.controller.confirm()
        self.assertEqual(session.stage, 'bombardment')
        self.controller.cycle_bombardment_target(dreadnought.unit_id)
        self.assertEqual(session.bombard_targets[dreadnought.unit_id], planet.planet_id)

        with patch('movement.random.randint', return_value=10):
            self.controller.resolve_bombardment()

        self.assertNotIn(defender, self.target.units)
        self.assertTrue(session.bombard_rolls)
        self.assertEqual(session.stage, 'invasion')

    def test_planetary_shield_cancels_bombardment_and_local_pds_defends_landing_planet(self):
        target = next(tile for tile in self.controller.neighbors(self.home)
                      if tile.planets and any(self.controller.route(self.home, tile, unit, self.player)
                                             for unit in self.home.units if unit.owner == 'sol' and unit.kind == 'carrier'))
        planet = target.planets[0]
        target.planet_owners[planet.planet_id] = 'hacan'
        pds = Unit('hacan-planetary-shield-pds', 'pds', 'hacan', 'ylw',
                   UnitLocation(Region.PLANET, planet_id=planet.planet_id))
        dreadnought = Unit('sol-shield-dread', 'dreadnought', 'sol', 'blu', UnitLocation(Region.SPACE))
        target.units.extend((pds, dreadnought))
        session = self.controller.activate(self.player, target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = [unit for unit in source.passengers if unit.kind == 'infantry'][:2]
        session.toggle(carrier.unit_id)
        for unit in infantry:
            session.toggle(unit.unit_id)

        with patch('movement.random.randint', return_value=1):
            self.controller.confirm()
        self.assertEqual(session.stage, 'invasion')
        self.assertTrue(session.bombardment_cancelled)
        for unit in infantry:
            self.controller.cycle_landing(unit.unit_id)
        with patch('movement.random.randint', return_value=10):
            self.controller.establish_control()

        self.assertTrue(session.defense_log)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                             unit.location.region == Region.PLANET and
                             unit.location.planet_id == planet.planet_id for unit in target.units), 1)
        self.assertEqual(target.planet_owners[planet.planet_id], 'sol')

    def test_ground_battles_resolve_landed_planets_in_order_then_capture_them(self):
        target = next(tile for tile in self.board.neighbors(self.home.position)
                      if len(tile.planets) >= 2 and not tile.command_tokens)
        hacan = next(player for player in self.players if player.faction == 'hacan')
        planets = target.planets[:2]
        defenders = []
        for index, planet in enumerate(planets):
            target.planet_owners[planet.planet_id] = 'hacan'
            defender = Unit(f'hacan-defender-{planet.planet_id}', 'infantry', 'hacan', hacan.color_code,
                            UnitLocation(Region.PLANET, planet_id=planet.planet_id))
            target.units.append(defender)
            defenders.append(defender)
            hacan.planets.append(PlanetCard(planet))
        sol_cards = list(self.player.planets)
        hacan_cards = list(hacan.planets)

        session = self.controller.activate(self.player, target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = [unit for unit in source.passengers if unit.kind == 'infantry'][:2]
        session.toggle(carrier.unit_id)
        for unit in infantry:
            session.toggle(unit.unit_id)
        self.controller.confirm()
        for unit, planet in zip(infantry, planets):
            cycles = 1 + planets.index(planet)
            for _ in range(cycles):
                self.controller.cycle_landing(unit.unit_id)
        self.controller.establish_control()

        self.assertEqual(session.stage, 'ground_combat')
        self.assertEqual(session.combat_planet_id, planets[0].planet_id)
        with patch('movement.random.randint', side_effect=(10, 1)):
            self.controller.advance_combat()
        self.controller.assign_combat_hit('hacan', 'infantry')
        self.controller.advance_combat()
        self.assertEqual(session.stage, 'ground_combat')
        self.assertEqual(session.combat_planet_id, planets[1].planet_id)
        self.assertEqual(target.planet_owners[planets[0].planet_id], 'sol')

        with patch('movement.random.randint', side_effect=(10, 1)):
            self.controller.advance_combat()
        self.controller.assign_combat_hit('hacan', 'infantry')
        self.controller.advance_combat()
        self.assertEqual(session.stage, 'complete')
        self.assertIsNone(self.controller.session)
        self.assertTrue(all(target.planet_owners[p.planet_id] == 'sol' for p in planets))
        self.assertTrue(all(next(card for card in self.player.planets if card.planet.planet_id == p.planet_id).exhausted
                            for p in planets))

        self.assertTrue(self.controller.undo())
        self.assertEqual(self.player.planets, sol_cards)
        self.assertEqual(hacan.planets, hacan_cards)
        self.assertTrue(all(target.planet_owners[p.planet_id] == 'hacan' for p in planets))
        self.assertTrue(all(defender in target.units for defender in defenders))

    def test_ground_defender_win_keeps_planet_control(self):
        target = next(tile for tile in self.board.neighbors(self.home.position)
                      if tile.planets and not tile.command_tokens)
        planet = target.planets[0]
        hacan = next(player for player in self.players if player.faction == 'hacan')
        defender = Unit('hacan-ground-defender', 'infantry', 'hacan', hacan.color_code,
                        UnitLocation(Region.PLANET, planet_id=planet.planet_id))
        target.units.append(defender)
        target.planet_owners[planet.planet_id] = 'hacan'
        hacan.planets.append(PlanetCard(planet))

        session = self.controller.activate(self.player, target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = next(unit for unit in source.passengers if unit.kind == 'infantry')
        session.toggle(carrier.unit_id)
        session.toggle(infantry.unit_id)
        self.controller.confirm()
        self.controller.cycle_landing(infantry.unit_id)
        self.controller.establish_control()
        with patch('movement.random.randint', side_effect=(1, 10)):
            self.controller.advance_combat()
        self.controller.assign_combat_hit('sol', 'infantry')
        self.controller.advance_combat()

        self.assertIsNone(self.controller.session)
        self.assertNotIn(infantry, target.units)
        self.assertIn(defender, target.units)
        self.assertEqual(target.planet_owners[planet.planet_id], 'hacan')
        self.assertNotIn(planet.planet_id, {card.planet.planet_id for card in self.player.planets})

    def test_loaded_infantry_can_capture_one_planet_and_fight_on_another(self):
        target = next(tile for tile in self.board.neighbors(self.home.position)
                      if len(tile.planets) >= 2 and not tile.command_tokens)
        first_planet, contested_planet = target.planets[:2]
        hacan = next(player for player in self.players if player.faction == 'hacan')
        defender = Unit('hacan-second-planet-defender', 'infantry', 'hacan', hacan.color_code,
                        UnitLocation(Region.PLANET, planet_id=contested_planet.planet_id))
        target.units.append(defender)
        target.planet_owners[contested_planet.planet_id] = 'hacan'
        hacan.planets.append(PlanetCard(contested_planet))
        original_sol_cards = list(self.player.planets)

        session = self.controller.activate(self.player, target.position)
        source = session.sources[self.home.position]
        carrier = next(unit for unit in source.ships if unit.kind == 'carrier')
        infantry = [unit for unit in source.passengers if unit.kind == 'infantry'][:2]
        session.toggle(carrier.unit_id)
        for unit in infantry:
            session.toggle(unit.unit_id)
        self.assertEqual(session.cargo_values(source), (2, carrier.capacity))
        self.controller.confirm()
        self.controller.cycle_landing(infantry[0].unit_id)
        self.controller.cycle_landing(infantry[1].unit_id)
        self.controller.cycle_landing(infantry[1].unit_id)
        self.controller.establish_control()

        self.assertEqual(target.planet_owners[first_planet.planet_id], 'sol')
        self.assertEqual(session.stage, 'ground_combat')
        self.assertEqual(session.combat_planet_id, contested_planet.planet_id)
        with patch('movement.random.randint', side_effect=(10, 1)):
            self.controller.advance_combat()
        self.controller.assign_combat_hit('hacan', 'infantry')
        self.controller.advance_combat()

        self.assertIsNone(self.controller.session)
        self.assertEqual(target.planet_owners[first_planet.planet_id], 'sol')
        self.assertEqual(target.planet_owners[contested_planet.planet_id], 'sol')
        self.assertEqual(infantry[0].location.planet_id, first_planet.planet_id)
        self.assertEqual(infantry[1].location.planet_id, contested_planet.planet_id)
        self.assertTrue(next(card for card in self.player.planets
                             if card.planet.planet_id == contested_planet.planet_id).exhausted)

        self.assertTrue(self.controller.undo())
        self.assertEqual(self.player.planets, original_sol_cards)
        self.assertEqual(target.planet_owners[contested_planet.planet_id], 'hacan')
        self.assertIn(defender, target.units)

    def test_debug_command_token_uses_tactical_reserve(self):
        tactical = self.player.command_pools['tactical']
        self.controller.add_command_token(self.player, self.target.position)
        self.assertIn(self.player.faction, self.target.command_tokens)
        self.assertEqual(self.player.command_pools['tactical'], tactical - 1)
        with self.assertRaises(MovementError):
            self.controller.add_command_token(self.player, self.target.position)

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
