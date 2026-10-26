"""Executable PoK leader and faction abilities."""

import random
import unittest
from dataclasses import replace
from types import SimpleNamespace

from board import ROOT, load_board
from leader_effects import (use_arborec_agent, use_cabal_agent, use_cabal_hero, use_empyrean_agent,
                            use_empyrean_hero, use_nomad_artuno, use_nomad_hero,
                            use_ghost_agent, use_l1z1x_agent, use_saar_agent,
                            cabal_agent_options, cabal_hero_targets,
                            l1z1x_agent_mech_available,
                            xxcha_agent_remove_options,
                            use_xxcha_agent,
                            clear_round_hero_effects, use_mahact_hero, use_titans_agent,
                            use_titans_hero)
from leaders import leader_available, ready_leaders, unlock_leader
from movement import MovementController, Session, Snapshot
from player import PlanetCard, create_players
from pok_factions import (cabal_capture_destroyed, cabal_return_captured,
                          cabal_capture_unit,
                          keleres_council_patronage, mahact_gain_fleet_token,
                          mahact_fleet_limit, naaz_explore_planet, titans_awaken,
                          titans_hero_space_cannon, titans_place_sleeper)
from units import Region, Unit, UnitLocation
from unit_supply import infantry_area_counts


class FixedDie:
    def randint(self, low, high):
        return 2


class PokEffectsTests(unittest.TestCase):
    def game(self, faction):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   (faction, 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        return board, players[0], players[1]

    def test_sardakk_agent_offers_two_infantry_at_tactical_action_end(self):
        board, sardakk, sol = self.game('sardakk')
        movement = MovementController(board, (sardakk, sol))
        target = next(tile for tile in board.values()
                      if any(owner == sol.faction for owner in tile.planet_owners.values()))
        planet_id = next(planet_id for planet_id, owner in target.planet_owners.items()
                         if owner == sol.faction)
        before = sum(unit.owner == sol.faction and unit.kind == 'infantry' and
                     unit.location.region == Region.PLANET and
                     unit.location.planet_id == planet_id for unit in target.units)

        session = movement.activate(sol, target.position)
        session.stage = 'production'  # all other tactical steps are already resolved
        movement.skip_production()
        self.assertEqual(session.stage, 'sardakk_agent')
        self.assertEqual(session.sardakk_agent_owner, sardakk.faction)

        self.assertTrue(movement.resolve_sardakk_agent(planet_id))
        after = sum(unit.owner == sol.faction and unit.kind == 'infantry' and
                    unit.location.region == Region.PLANET and
                    unit.location.planet_id == planet_id for unit in target.units)
        self.assertEqual(after, before + 2)
        self.assertFalse(leader_available(sardakk, 'sardakkagent'))
        self.assertIsNone(movement.session)
        self.assertEqual(len(movement.history), 1)
        self.assertTrue(movement.undo())
        self.assertTrue(leader_available(sardakk, 'sardakkagent'))
        self.assertEqual(sum(unit.owner == sol.faction and unit.kind == 'infantry' and
                             unit.location.region == Region.PLANET and
                             unit.location.planet_id == planet_id for unit in target.units),
                         before)

    def test_sardakk_agent_can_be_declined_without_exhausting_it(self):
        board, sardakk, sol = self.game('sardakk')
        movement = MovementController(board, (sardakk, sol))
        target = next(tile for tile in board.values()
                      if any(owner == sol.faction for owner in tile.planet_owners.values()))
        session = movement.activate(sol, target.position)
        session.stage = 'production'
        movement.skip_production()
        self.assertEqual(session.stage, 'sardakk_agent')

        self.assertFalse(movement.resolve_sardakk_agent())
        self.assertTrue(leader_available(sardakk, 'sardakkagent'))
        self.assertIsNone(movement.session)

    def test_sardakk_agent_is_not_offered_when_active_player_controls_no_planet(self):
        board, sardakk, sol = self.game('sardakk')
        movement = MovementController(board, (sardakk, sol))
        target = next(tile for tile in board.values() if not tile.planets)
        session = movement.activate(sol, target.position)
        session.stage = 'production'

        movement.skip_production()

        self.assertIsNone(movement.session)
        self.assertTrue(leader_available(sardakk, 'sardakkagent'))

    def test_cabal_agent_and_devour_capture_without_touching_structures(self):
        board, cabal, sol = self.game('cabal')
        sol.commodities = sol.commodity_limit
        use_cabal_agent(cabal, sol, 'carrier', board)
        self.assertEqual(sol.commodities, 0)
        self.assertEqual(sol.trade_goods, sol.commodity_limit)
        self.assertEqual(cabal.captured_units['carrier'], 1)
        self.assertEqual(cabal.captured_unit_sources[('carrier', 'sol')], 1)
        self.assertFalse(leader_available(cabal, 'cabalagent'))
        ready_leaders(cabal)
        carrier = Unit('enemy-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        self.assertTrue(cabal_capture_destroyed(cabal, carrier, during_combat=True))
        self.assertFalse(cabal_capture_destroyed(cabal, carrier, during_combat=False))
        self.assertEqual(cabal.captured_units['carrier'], 2)
        self.assertTrue(cabal_return_captured(cabal, 'carrier'))
        self.assertEqual(cabal.captured_units['carrier'], 1)
        self.assertEqual(cabal.captured_unit_sources[('carrier', 'sol')], 1)

    def test_cabal_agent_cannot_recapture_a_plastic_unit_already_captured(self):
        board, cabal, sol = self.game('cabal')
        sol_carriers = sum(unit.owner == sol.faction and unit.kind == 'carrier'
                           for tile in board.values() for unit in tile.units)
        for _ in range(4 - sol_carriers):
            cabal_capture_unit(cabal, 'carrier', sol.faction)

        self.assertNotIn('carrier', cabal_agent_options(
            sol, board, captured_by=cabal))

        self.assertTrue(cabal_return_captured(cabal, 'carrier', sol.faction))
        self.assertEqual(4 - sol_carriers - 1,
                         cabal.captured_unit_sources[('carrier', sol.faction)])
        self.assertIn('carrier', cabal_agent_options(
            sol, board, captured_by=cabal))

    def test_cabal_agent_uses_replenishment_value_even_if_commodities_are_gone(self):
        board, cabal, sol = self.game('cabal')
        home = next(tile for tile in board.values()
                    if any(unit.owner == 'sol' and unit.kind == 'carrier'
                           for unit in tile.units))
        sol.commodities = 0
        self.assertIn('dreadnought', cabal_agent_options(
            sol, board, commodity_value=4))

        use_cabal_agent(cabal, sol, 'dreadnought', board, commodity_value=4)

        self.assertEqual(sol.trade_goods, 0)
        self.assertEqual(sol.commodities, 0)
        self.assertEqual(cabal.captured_units['dreadnought'], 1)

    def test_cabal_agent_cannot_capture_plastic_units_not_in_reinforcements(self):
        board, cabal, sol = self.game('cabal')
        carrier = next(unit for tile in board.values() for unit in tile.units
                       if unit.owner == 'sol' and unit.kind == 'carrier')
        home = next(tile for tile in board.values() if carrier in tile.units)
        for index in range(4 - sum(unit.owner == 'sol' and unit.kind == 'carrier'
                                   for tile in board.values() for unit in tile.units)):
            home.units.append(Unit(f'extra-sol-carrier-{index}', 'carrier', 'sol',
                                   sol.color_code, UnitLocation(Region.SPACE)))
        self.assertNotIn('carrier', cabal_agent_options(sol, board))
        sol.commodities = sol.commodity_limit

        with self.assertRaises(ValueError):
            use_cabal_agent(cabal, sol, 'carrier', board)

        self.assertTrue(leader_available(cabal, 'cabalagent'))

    def test_cabal_hero_rolls_for_enemy_ship_and_captures_its_cargo(self):
        board, cabal, sol = self.game('cabal')
        home = next(tile for tile in board.values() if tile.system_id == '54')
        carrier = Unit('carrier-to-capture', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('infantry-to-capture', 'infantry', 'sol', sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        home.units.extend((carrier, infantry))
        self.assertTrue(unlock_leader(cabal, 'cabalhero'))
        self.assertIn((carrier.unit_id, 2), use_cabal_hero(cabal, board, FixedDie()))
        self.assertNotIn(carrier, home.units)
        self.assertNotIn(infantry, home.units)
        self.assertEqual(cabal.captured_units['carrier'], 1)
        self.assertEqual(cabal.captured_units['infantry'], 1)
        self.assertFalse(leader_available(cabal, 'cabalhero'))

    def test_cabal_hero_targets_ships_across_matching_wormholes(self):
        board, cabal, sol = self.game('cabal')
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == cabal.faction and
                                     unit.kind == 'spacedock')]
        tear = next(tile for tile in board.values()
                    if tile.wormholes == ('ALPHA',) and tile.planets)
        remote = next(tile for tile in board.values()
                      if tile.wormholes == ('ALPHA',) and tile is not tear and
                      tile.position not in {n.position for n in board.neighbors(tear.position)})
        planet_id = tear.planets[0].planet_id
        tear.units.append(Unit('cabal-alpha-dimensional-tear', 'spacedock', cabal.faction,
                               cabal.color_code, UnitLocation(Region.PLANET, planet_id)))
        enemy = Unit('enemy-across-alpha-wormhole', 'cruiser', sol.faction,
                     sol.color_code, UnitLocation(Region.SPACE))
        remote.units.append(enemy)
        movement = MovementController(board, [cabal, sol])

        targets = cabal_hero_targets(board, cabal, movement=movement)
        self.assertIn((remote, enemy), targets)

    def test_empyrean_agent_grants_token_only_for_planetless_destination(self):
        board, empyrean, sol = self.game('empyrean')
        empty = next(tile for tile in board.values() if not tile.planets)
        ship = Unit('sol-moved', 'carrier', 'sol', sol.color_code,
                    UnitLocation(Region.SPACE))
        empty.units.append(ship)
        before = sol.command_pools['tactical']
        use_empyrean_agent(empyrean, sol, board, empty, 'tactical')
        self.assertEqual(sol.command_pools['tactical'], before + 1)
        self.assertFalse(leader_available(empyrean, 'empyreanagent'))

    def test_empyrean_agent_treats_a_stellar_converter_wreck_as_planetless(self):
        board, empyrean, sol = self.game('empyrean')
        tile = next(tile for tile in board.values() if len(tile.planets) == 1)
        destroyed_planet = tile.planets[0]
        tile.units.append(Unit('sol-in-wreck-system', 'carrier', 'sol', sol.color_code,
                               UnitLocation(Region.SPACE)))
        before = sol.command_pools['fleet']

        use_empyrean_agent(
            empyrean, sol, board, tile, 'fleet',
            exploration=SimpleNamespace(
                destroyed_planets={destroyed_planet.planet_id}))

        self.assertEqual(sol.command_pools['fleet'], before + 1)
        self.assertFalse(leader_available(empyrean, 'empyreanagent'))

    def test_empyrean_agent_opens_after_a_ship_reaches_a_planetless_system(self):
        board, empyrean, sol = self.game('empyrean')
        movement = MovementController(board, (empyrean, sol))
        target = next(tile for tile in board.values() if tile.number == 77)
        session = movement.activate(sol, target.position)
        ship = next(ship for source in session.sources.values()
                    for ship in source.ships if ship.kind == 'carrier')
        session.toggle(ship.unit_id)

        movement.confirm()

        self.assertEqual(session.stage, 'empyrean_agent')
        self.assertEqual(session.empyrean_agent_owner, empyrean.faction)
        before = dict(sol.command_pools)
        movement.resolve_empyrean_agent('fleet')
        self.assertEqual(sol.command_pools['fleet'], before['fleet'] + 1)
        self.assertFalse(leader_available(empyrean, 'empyreanagent'))
        self.assertNotEqual(session.stage, 'empyrean_agent')

    def test_empyrean_agent_can_be_declined_and_is_not_offered_without_supply(self):
        board, empyrean, sol = self.game('empyrean')
        movement = MovementController(board, (empyrean, sol))
        target = next(tile for tile in board.values() if tile.number == 77)
        session = movement.activate(sol, target.position)
        ship = next(ship for source in session.sources.values()
                    for ship in source.ships if ship.kind == 'carrier')
        session.toggle(ship.unit_id)
        movement.confirm()
        movement.resolve_empyrean_agent()
        self.assertTrue(leader_available(empyrean, 'empyreanagent'))

        board, empyrean, sol = self.game('empyrean')
        sol.command_pools.update(tactical=11, fleet=3, strategic=2)
        movement = MovementController(board, (empyrean, sol))
        target = next(tile for tile in board.values() if tile.number == 77)
        session = movement.activate(sol, target.position)
        ship = next(ship for source in session.sources.values()
                    for ship in source.ships if ship.kind == 'carrier')
        session.toggle(ship.unit_id)
        movement.confirm()
        self.assertNotEqual(session.stage, 'empyrean_agent')
        self.assertTrue(leader_available(empyrean, 'empyreanagent'))

    def _produce_sol_infantry_with_argent_in_game(self, *, count=1,
                                                 fill_infantry_areas=False):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('argent', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        argent, sol = players[:2]
        controller = MovementController(board, players)
        home = next(tile for tile in board.values()
                    if any(unit.owner == 'sol' and unit.kind == 'spacedock'
                           for unit in tile.units))
        dock = next(unit for unit in home.units
                    if unit.owner == 'sol' and unit.kind == 'spacedock')
        adjacent = next(tile for tile in controller.neighbors(home)
                        if tile.planets and not any(p.faction_homeworld for p in tile.planets))
        destination = adjacent.planets[0]
        adjacent.planet_owners[destination.planet_id] = sol.faction
        sol.planets.append(PlanetCard(destination))
        if fill_infantry_areas:
            source_planet = dock.location.planet_id
            for tile in board.values():
                tile.units[:] = [unit for unit in tile.units
                                 if not (unit.owner == sol.faction and
                                         unit.kind == 'infantry')]
            home.units.append(Unit('sol-home-infantry-anchor', 'infantry', sol.faction,
                                   sol.color_code,
                                   UnitLocation(Region.PLANET, source_planet)))
            anchor_index = 0
            for tile in board.values():
                if tile.position in (home.position, adjacent.position):
                    continue
                planet = next((planet for planet in tile.planets
                               if planet.planet_id != destination.planet_id), None)
                if planet is None:
                    continue
                tile.units.append(Unit(f'sol-area-anchor-{anchor_index}', 'infantry',
                                       sol.faction, sol.color_code,
                                       UnitLocation(Region.PLANET, planet.planet_id)))
                anchor_index += 1
                if anchor_index == 11:
                    break
            if anchor_index != 11:
                raise AssertionError('Four-player map has too few distinct planet areas')
        sol.trade_goods = 3
        session = controller.start_strategy_production(sol, home, dock)
        session.production_choices = {'infantry': count}
        session.trade_goods_to_spend = 1
        controller.produce()
        return controller, argent, sol, home, adjacent, destination, session

    def test_argent_agent_relocates_new_ground_force_to_adjacent_controlled_planet(self):
        controller, argent, sol, home, adjacent, destination, session = \
            self._produce_sol_infantry_with_argent_in_game()

        self.assertEqual(session.stage, 'argent_agent')
        unit_id = session.argent_agent_produced_ids[0]
        options = controller.argent_agent_destination_options(session, unit_id)
        self.assertIn(destination.planet_id, [option[0] for option in options])

        controller.resolve_argent_agent({unit_id: destination.planet_id})

        relocated = next(unit for unit in adjacent.units if unit.unit_id == unit_id)
        self.assertEqual(relocated.location, UnitLocation(Region.PLANET, destination.planet_id))
        self.assertFalse(any(unit.unit_id == unit_id for unit in home.units))
        self.assertFalse(leader_available(argent, 'argentagent'))
        self.assertIsNone(controller.session)

    def test_argent_agent_decline_ignores_staged_destinations(self):
        controller, argent, _sol, home, _adjacent, destination, session = \
            self._produce_sol_infantry_with_argent_in_game()
        unit_id = session.argent_agent_produced_ids[0]
        session.argent_agent_destinations[unit_id] = destination.planet_id

        controller.resolve_argent_agent({})

        self.assertTrue(leader_available(argent, 'argentagent'))
        self.assertTrue(any(unit.unit_id == unit_id and
                            unit.location.planet_id == home.planets[0].planet_id
                            for unit in home.units))
        self.assertIsNone(controller.session)

    def test_argent_agent_reaction_also_follows_umbat_agent_production(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('argent', 'muaat', 'sol', 'hacan'))
        players = create_players(board, config)
        argent, muaat = players[:2]
        controller = MovementController(board, players)
        home = next(tile for tile in board.values()
                    if any(unit.owner == muaat.faction for unit in tile.units) and
                    any(planet.faction_homeworld == muaat.faction for planet in tile.planets))
        home.units.append(Unit('muaat-agent-production-war-sun', 'warsun', muaat.faction,
                               muaat.color_code, UnitLocation(Region.SPACE)))
        adjacent = next(tile for tile in controller.neighbors(home)
                        if tile.planets and not any(p.faction_homeworld for p in tile.planets))
        destination = adjacent.planets[0]
        adjacent.planet_owners[destination.planet_id] = muaat.faction
        muaat.planets.append(PlanetCard(destination))
        muaat.trade_goods = 1
        snapshot = Snapshot.capture(board, muaat, players)
        session = controller.start_agent_production(muaat, home, snapshot)
        session.production_choices = {'infantry': 1}
        session.trade_goods_to_spend = 1

        controller.produce()

        self.assertEqual(session.stage, 'argent_agent')
        self.assertTrue(session.argent_agent_produced_ids)
        self.assertEqual(session.argent_agent_owner, argent.faction)
        self.assertTrue(leader_available(argent, 'argentagent'))

    def test_argent_agent_cannot_exceed_the_infantry_area_anchor_supply(self):
        controller, argent, sol, home, adjacent, destination, session = \
            self._produce_sol_infantry_with_argent_in_game(
                count=2, fill_infantry_areas=True)

        self.assertEqual(12, len(infantry_area_counts(sol, controller.board)))
        self.assertNotEqual(session.stage, 'argent_agent')
        self.assertTrue(leader_available(argent, 'argentagent'))

    def test_l1z1x_agent_replaces_active_system_infantry_with_mech(self):
        board, l1z1x, sol = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        mech = use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)
        self.assertNotIn(infantry, tile.units)
        self.assertIn(mech, tile.units)
        self.assertEqual('mech', mech.kind)
        self.assertEqual(infantry.location, mech.location)
        self.assertFalse(leader_available(l1z1x, 'l1z1xagent'))

    def test_saar_agent_matches_the_highest_ship_move_value(self):
        board, saar, sol = self.game('saar')
        tile = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('sol-agent-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        hacan_color = next(unit.color_code for system in board.values()
                           for unit in system.units if unit.owner == 'hacan')
        fast_cruiser = Unit('hacan-agent-cruiser', 'cruiser', 'hacan',
                            hacan_color,
                            UnitLocation(Region.SPACE))
        tile.units.extend((carrier, fast_cruiser))

        granted_move = use_saar_agent(saar, sol, board, carrier.unit_id)

        self.assertEqual(fast_cruiser.move_value, granted_move)
        self.assertFalse(leader_available(saar, 'saaragent'))

    def test_ghost_agent_requires_non_delta_wormhole_and_exhausts_on_use(self):
        board, ghost, _ = self.game('ghost')
        tile = next(tile for tile in board.values())
        tile.definition = replace(tile.definition, wormholes=('DELTA',))
        with self.assertRaises(ValueError):
            use_ghost_agent(ghost, tile, board)
        self.assertTrue(leader_available(ghost, 'ghostagent'))

        tile.definition = replace(tile.definition, wormholes=('GAMMA',))
        use_ghost_agent(ghost, tile, board)
        self.assertFalse(leader_available(ghost, 'ghostagent'))

    def test_l1z1x_agent_does_not_exhaust_without_mech_in_reinforcements(self):
        board, l1z1x, _ = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        other = next(candidate for candidate in board.values()
                     if candidate is not tile and candidate.planets)
        other.command_tokens.add('l1z1x')
        for index in range(4):
            other.units.append(Unit(f'l1z1x-reserve-mech-{index}', 'mech', 'l1z1x',
                                    l1z1x.color_code,
                                    UnitLocation(Region.PLANET, other.planets[0].planet_id)))
        with self.assertRaises(ValueError):
            use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)
        self.assertTrue(leader_available(l1z1x, 'l1z1xagent'))

    def test_l1z1x_agent_redeploys_mech_from_unlocked_system_when_supply_is_empty(self):
        board, l1z1x, _ = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        other = next(candidate for candidate in board.values()
                     if candidate is not tile and candidate.planets)
        mechs = []
        for index in range(4):
            mech = Unit(f'l1z1x-deploy-mech-{index}', 'mech', 'l1z1x',
                        l1z1x.color_code,
                        UnitLocation(Region.PLANET, other.planets[0].planet_id))
            other.units.append(mech)
            mechs.append(mech)

        self.assertTrue(l1z1x_agent_mech_available(l1z1x, tile, board))
        replacement = use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)

        self.assertIn(replacement, tile.units)
        self.assertNotIn(infantry, tile.units)
        self.assertNotIn(mechs[0], other.units)
        self.assertEqual(4, sum(unit.owner == 'l1z1x' and unit.kind == 'mech'
                                 for system in board.values() for unit in system.units))

    def test_arborec_agent_replaces_ship_and_keeps_legal_cargo_attached(self):
        board, arborec, sol = self.game('arborec')
        tile = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('sol-agent-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('sol-agent-infantry', 'infantry', 'sol', sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        tile.units.extend((carrier, infantry))
        replacement = use_arborec_agent(arborec, sol, board, carrier.unit_id,
                                        'dreadnought')
        self.assertNotIn(carrier, tile.units)
        self.assertIn(replacement, tile.units)
        self.assertEqual('dreadnought', replacement.kind)
        self.assertEqual(replacement.unit_id, infantry.location.carrier_id)
        self.assertFalse(leader_available(arborec, 'arborecagent'))

    def test_xxcha_agent_readies_planet_and_optionally_returns_infantry(self):
        board, xxcha, _ = self.game('xxcha')
        card = xxcha.planets[0]
        home = next(tile for tile in board.values()
                    if card.planet.planet_id in tile.planet_owners)
        neighbor = next(tile for tile in board.neighbors(home.position) if tile.planets)
        neighbor.planet_owners[neighbor.planets[0].planet_id] = 'xxcha'
        card.exhausted = True
        infantry = Unit('xxcha-agent-infantry', 'infantry', 'xxcha', xxcha.color_code,
                        UnitLocation(Region.PLANET, card.planet.planet_id))
        home.units.append(infantry)
        self.assertIs(use_xxcha_agent(xxcha, board, card.planet.planet_id,
                                      remove_infantry_id=infantry.unit_id), infantry)
        self.assertFalse(card.exhausted)
        self.assertNotIn(infantry, home.units)
        self.assertFalse(leader_available(xxcha, 'xxchaagent'))

    def test_xxcha_agent_counts_a_planet_connected_by_matching_wormholes(self):
        from board import Planet

        board, xxcha, _ = self.game('xxcha')
        for tile in board.values():
            tile.planet_owners.clear()
        controlled = next(tile for tile in board.values()
                          if tile.wormholes == ('ALPHA',) and tile.planets)
        target = next(tile for tile in board.values()
                      if tile.wormholes == ('ALPHA',) and tile is not controlled and
                      tile.position not in {n.position for n in board.neighbors(controlled.position)})
        planet = Planet('xxcha-wormhole-agent-target', 'Wormhole Target', 'cultural', 1, 1)
        target.definition = replace(target.definition,
                                     planets=(*target.planets, planet))
        target.planet_owners[planet.planet_id] = xxcha.faction
        card = PlanetCard(planet, exhausted=True)
        xxcha.planets.append(card)
        infantry = Unit('xxcha-wormhole-agent-infantry', 'infantry', xxcha.faction,
                        xxcha.color_code, UnitLocation(Region.PLANET, planet.planet_id))
        target.units.append(infantry)
        controlled.planet_owners[controlled.planets[0].planet_id] = xxcha.faction
        movement = MovementController(board, [xxcha])

        self.assertEqual((infantry,), xxcha_agent_remove_options(
            xxcha, board, planet.planet_id, movement=movement))
        self.assertIs(use_xxcha_agent(xxcha, board, planet.planet_id,
                                      remove_infantry_id=infantry.unit_id,
                                      movement=movement), infantry)

    def test_empyrean_hero_restores_frontiers_and_returns_systems_to_explore(self):
        from exploration import ExplorationController
        board, empyrean, _ = self.game('empyrean')
        empty = next(tile for tile in board.values() if not tile.planets)
        empty.units.append(Unit('empyrean-explorer', 'carrier', 'empyrean',
                                empyrean.color_code, UnitLocation(Region.SPACE)))
        exploration = ExplorationController(board, [empyrean],
                                            rng=random.Random(1), relic_deck=[])
        exploration.space_tokens[empty.position].clear()
        unlock_leader(empyrean, 'empyreanhero')
        eligible = use_empyrean_hero(empyrean, exploration, board)
        self.assertIn(empty, eligible)
        self.assertIn('frontier', exploration.space_tokens[empty.position])
        self.assertFalse(leader_available(empyrean, 'empyreanhero'))

    def test_naaz_mech_offers_two_exploration_cards(self):
        from exploration import ExplorationController
        board, naaz, _ = self.game('naaz')
        tile = next(tile for tile in board.values() if tile.number == 30)
        planet = tile.planets[0]
        tile.planet_owners[planet.planet_id] = naaz.faction
        tile.units.append(Unit('naaz-explorer-mech', 'mech', 'naaz', naaz.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id)))
        exploration = ExplorationController(board, [naaz],
                                            rng=random.Random(1), relic_deck=[])
        outcome = naaz_explore_planet(naaz, exploration, tile, planet.planet_id)
        self.assertEqual(len(outcome.offered_cards), 2)

    def test_nomad_artuno_collects_stored_trade_goods_when_readied(self):
        board, nomad, _ = self.game('nomad')
        use_nomad_artuno(nomad, 3)
        self.assertEqual(nomad.trade_goods, 0)
        ready_leaders(nomad)
        self.assertEqual(nomad.trade_goods, 3)

    def test_nomad_hero_stays_on_board_until_end_of_round(self):
        _, nomad, _ = self.game('nomad')
        unlock_leader(nomad, 'nomadhero')
        use_nomad_hero(nomad)
        self.assertIn('nomad_command_token_exit', nomad.hero_round_flags)
        self.assertFalse(nomad.leaders['nomadhero'].purged)
        self.assertFalse(leader_available(nomad, 'nomadhero'))
        clear_round_hero_effects(nomad)
        self.assertTrue(nomad.leaders['nomadhero'].purged)
        self.assertFalse(nomad.hero_round_flags)

    def test_titans_sleeper_awaken_and_hero_attachment(self):
        board, titans, _ = self.game('titans')
        home = next(tile for tile in board.values() if tile.system_id == '55')
        planet = home.planets[0]
        titans_place_sleeper(titans, board, home, planet.planet_id)
        self.assertIn(planet.planet_id, home.sleeper_tokens)
        placed = titans_awaken(titans, board, home, planet_ids=(planet.planet_id,))
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].kind, 'pds')
        self.assertNotIn(planet.planet_id, home.sleeper_tokens)
        self.assertEqual(use_titans_agent(titans, 3), 2)
        self.assertTrue(unlock_leader(titans, 'titanshero'))
        titans.planets[0].exhausted = True
        use_titans_hero(titans)
        self.assertFalse(titans.planets[0].exhausted)
        self.assertIn('elysium', titans.hero_attachments)
        self.assertEqual(titans_hero_space_cannon(titans, home), (5, 3))

    def test_tellurian_cancels_one_combat_hit_before_assignment(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('titans', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        titans, sol = by_faction['titans'], by_faction['sol']
        movement = MovementController(board, players)
        target = next(iter(board.values()))
        target.units[:] = [
            Unit(f'tellurian-dread-{index}', 'dreadnought', 'titans', titans.color_code,
                 UnitLocation(Region.SPACE)) for index in range(2)]
        session = Session(titans, target, {}, Snapshot.capture(board, titans, players),
                          stage='space_combat', combat_factions=('titans', 'sol'),
                          combat_hits={'titans': 2, 'sol': 0},
                          combat_assignments={'titans': [], 'sol': []},
                          combat_needs_resolution=True)
        movement.session = session

        self.assertTrue(movement.titans_agent_can_cancel_combat_hit('titans'))
        movement.cancel_titans_combat_hit('titans')
        self.assertEqual(session.combat_hits['titans'], 1)
        self.assertFalse(leader_available(titans, 'titansagent'))
        self.assertFalse(movement.combat_assignments_complete(session))

        movement.assign_combat_hit('titans', 'dreadnought')
        self.assertTrue(movement.combat_assignments_complete(session))
        with self.assertRaises(ValueError):
            movement.cancel_titans_combat_hit('titans')
        self.assertFalse(leader_available(titans, 'titansagent'))
        movement.advance_combat()
        titan_dreadnoughts = [unit for unit in target.units if unit.owner == 'titans']
        self.assertEqual(len(titan_dreadnoughts), 2)
        self.assertEqual(sum(unit.damaged for unit in titan_dreadnoughts), 1)

    def test_tellurian_cancels_space_cannon_hit_before_assignment(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('titans', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        titans, sol = by_faction['titans'], by_faction['sol']
        movement = MovementController(board, players)
        target = next(iter(board.values()))
        ship = Unit('tellurian-cannon-target', 'carrier', 'titans', titans.color_code,
                    UnitLocation(Region.SPACE))
        target.units[:] = [ship]
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players),
                          stage='space_cannon_assign', space_cannon_next_stage='invasion',
                          space_cannon_events=[{'owner': 'sol', 'target': 'titans',
                                                'candidates': [ship.unit_id],
                                                'graviton': False}])
        movement.session = session

        self.assertTrue(movement.titans_agent_can_cancel_space_cannon_hit())
        movement.cancel_titans_space_cannon_hit()
        self.assertEqual(session.space_cannon_events, [])
        self.assertIn(ship, target.units)
        self.assertFalse(ship.damaged)
        self.assertFalse(leader_available(titans, 'titansagent'))

    def test_titans_pds_limit_is_per_player_and_cannot_be_exceeded(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('titans', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        titans, sol, jolnar = (by_faction[faction] for faction in
                               ('titans', 'sol', 'jolnar'))
        target = next(tile for tile in board.values()
                      if any(planet.faction_homeworld == 'titans'
                             for planet in tile.planets))
        planet = target.planets[0]
        titans_place_sleeper(titans, board, target, planet.planet_id)
        target.units.extend((
            Unit('sol-pds-on-titans-planet', 'pds', sol.faction, sol.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
            Unit('jolnar-pds-on-titans-planet', 'pds', jolnar.faction, jolnar.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
        ))

        placed = titans_awaken(titans, board, target, planet_ids=(planet.planet_id,))

        self.assertEqual(1, len(placed), 'foreign PDS do not count against Titans limit')
        self.assertNotIn(planet.planet_id, target.sleeper_tokens)
        titans_place_sleeper(titans, board, target, planet.planet_id)
        target.units.append(Unit('titans-second-pds', 'pds', titans.faction,
                                 titans.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        placed = titans_awaken(titans, board, target, planet_ids=(planet.planet_id,))
        self.assertEqual((), placed)
        self.assertIn(planet.planet_id, target.sleeper_tokens)
        self.assertEqual(2, sum(unit.kind == 'pds' and unit.owner == titans.faction and
                                unit.location.region == Region.PLANET and
                                unit.location.planet_id == planet.planet_id
                                for unit in target.units))

    def test_mahact_foreign_token_changes_fleet_supply_and_enemy_reserve(self):
        board, mahact, sol = self.game('mahact')
        before = mahact_fleet_limit(mahact)
        self.assertTrue(mahact_gain_fleet_token(mahact, sol, board))
        self.assertEqual(mahact_fleet_limit(mahact), before + 1)
        self.assertIn('mahact', sol.foreign_command_tokens_out)
        self.assertFalse(mahact_gain_fleet_token(mahact, sol, board))

    def test_mahact_hero_moves_space_fleet_and_carried_units_into_combat(self):
        board, mahact, sol = self.game('mahact')
        source = next(tile for tile in board.values() if tile.system_id == '52')
        target = board.neighbors(source.position)[0]
        enemy = Unit('mahact-hero-enemy', 'carrier', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.append(enemy)
        carrier = next(unit for unit in source.units if unit.kind == 'carrier')
        cargo = Unit('mahact-hero-cargo', 'infantry', 'mahact', mahact.color_code,
                     UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        source.units.append(cargo)
        moving = tuple(unit for unit in source.units if unit.location.region != Region.PLANET)
        unlock_leader(mahact, 'mahacthero')
        self.assertEqual(use_mahact_hero(mahact, board, source, target), ('mahact', 'sol'))
        self.assertTrue(all(unit in target.units for unit in moving))
        self.assertTrue(all(unit not in source.units for unit in moving))
        self.assertFalse(leader_available(mahact, 'mahacthero'))

    def test_keleres_patronage_replenishes_and_gains_trade_good(self):
        board, keleres, _ = self.game('keleresm')
        keleres.commodities = 0
        keleres_council_patronage(keleres)
        self.assertEqual(keleres.commodities, keleres.commodity_limit)
        self.assertEqual(keleres.trade_goods, 1)


if __name__ == '__main__':
    unittest.main()
