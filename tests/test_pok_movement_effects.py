"""PoK technology and faction effects at tactical-action timing windows."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from board import ROOT, load_board
from exploration import ExplorationController
from leaders import leader_available
from movement import MovementController, MovementError, Session, Snapshot
from player import PlanetCard, create_players
from units import Region, Unit, UnitLocation, layout_units, unit_profile
from unit_supply import unit_supply_remaining


def game(faction='sol'):
    others = tuple(alias for alias in ('sol', 'jolnar', 'hacan', 'argent')
                   if alias != faction)
    config, board = load_board(ROOT / 'maps/four_player.json', (faction, *others[:3]))
    players = create_players(board, config)
    return board, players, MovementController(board, players)


class PokMovementEffectsTests(unittest.TestCase):
    def test_ghost_slipstream_applies_from_home_and_alpha_beta_but_not_gate(self):
        board, players, movement = game('ghost')
        ghost = players[0]
        origin, middle, target = list(board.values())[:3]
        origin.command_tokens.clear()
        middle.command_tokens.clear()
        target.command_tokens.clear()
        unit = Unit('ghost-slipstream-carrier', 'carrier', 'ghost', ghost.color_code,
                    UnitLocation(Region.SPACE))
        route_map = {origin.position: [middle], middle.position: [target],
                     target.position: []}
        movement.neighbors = lambda tile, faction=None: route_map[tile.position]

        origin.definition = replace(origin.definition, number=17, wormholes=('ALPHA',))
        self.assertIsNone(movement.route(origin, target, unit, ghost))
        origin.definition = replace(origin.definition, number=52, wormholes=('GAMMA',))
        self.assertIsNone(movement.route(origin, target, unit, ghost))
        origin.definition = replace(origin.definition, number=51, wormholes=())
        self.assertEqual((origin.position, middle.position, target.position),
                         movement.route(origin, target, unit, ghost))
        origin.definition = replace(origin.definition, number=51, wormholes=('ALPHA',))
        self.assertEqual((origin.position, middle.position, target.position),
                         movement.route(origin, target, unit, ghost))
        origin.definition = replace(origin.definition, number=52, wormholes=('ALPHA',))
        self.assertEqual((origin.position, middle.position, target.position),
                         movement.route(origin, target, unit, ghost))

        flagship = Unit('ghost-slipstream-flagship', 'flagship', 'ghost',
                        ghost.color_code, UnitLocation(Region.SPACE))
        self.assertIsNone(movement.route(origin, target, flagship, ghost))

    def test_ghost_agent_rebuilds_routes_between_wormhole_systems(self):
        board, players, movement = game('ghost')
        ghost = players[0]
        origin, target = list(board.values())[:2]
        origin.command_tokens.clear()
        target.command_tokens.clear()
        origin.definition = replace(origin.definition, wormholes=('ALPHA',))
        target.definition = replace(target.definition, wormholes=('GAMMA',))
        carrier = Unit('ghost-agent-carrier', 'carrier', 'ghost', ghost.color_code,
                       UnitLocation(Region.SPACE))
        origin.units.append(carrier)
        movement.neighbors = lambda tile, faction=None: []
        session = Session(ghost, target, {}, Snapshot.capture(board, ghost, players))
        movement.session = session

        movement.refresh_activation_routes(session)
        self.assertNotIn(origin.position, session.sources)
        session.ghost_wormhole_adjacency = True
        movement.refresh_activation_routes(session)

        self.assertEqual((origin.position, target.position),
                         session.sources[origin.position].routes[carrier.unit_id])

    def test_pre_fab_arcologies_readies_explored_planet(self):
        board, players, movement = game('naaz')
        naaz = players[0]
        naaz.technologies |= {'pfa', 'sdn'}
        tile = next(tile for tile in board.values() if tile.planets and
                    tile.planets[0].planet_type.lower() in
                    ('cultural', 'hazardous', 'industrial') and
                    'naaz' not in tile.command_tokens)
        planet = tile.planets[0]
        tile.planet_owners[planet.planet_id] = 'naaz'
        tile.units.append(Unit('naaz-scanlink-infantry', 'infantry', 'naaz', naaz.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id)))
        card = PlanetCard(planet, exhausted=True)
        naaz.planets.append(card)
        trait = planet.planet_type.lower()
        decks = {'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []}
        decks[trait] = [{'cultural': 'crf1', 'hazardous': 'hrf1',
                         'industrial': 'irf1'}[trait]]
        movement.exploration = ExplorationController(board, players, decks=decks,
                                                     relic_deck=[])
        movement.activate(naaz, tile.position)
        movement.explore_with_scanlink(planet.planet_id)
        self.assertFalse(card.exhausted)
        movement.cancel()
        self.assertTrue(card.exhausted)

    def test_supercharge_uses_exhausted_technology_for_one_combat_round(self):
        board, players, movement = game('naaz')
        naaz, sol = players[:2]
        naaz.technologies |= {'sc'}
        target = next(tile for tile in board.values() if not tile.units)
        target.units.extend((
            Unit('naaz-fighter', 'fighter', 'naaz', naaz.color_code, UnitLocation(Region.SPACE)),
            Unit('sol-fighter', 'fighter', sol.faction, sol.color_code,
                 UnitLocation(Region.SPACE)),
        ))
        session = Session(naaz, target, {}, Snapshot.capture(board, naaz, players))
        session.stage = 'space_combat'
        session.combat_factions = ('naaz', sol.faction)
        movement.session = session
        self.assertTrue(movement.supercharge_available('naaz'))
        movement.use_supercharge('naaz')
        self.assertIn('sc', naaz.exhausted_technologies)
        self.assertFalse(movement.supercharge_available('naaz'))
        with patch('movement.random.randint', return_value=8):
            movement.advance_combat()
        self.assertEqual(1, session.combat_round)
        self.assertEqual(9, session.combat_rolls['naaz'][0]['value'])
        self.assertEqual(1, session.combat_rolls['naaz'][0]['modifier'])
        self.assertTrue(session.combat_rolls['naaz'][0]['hit'])
        self.assertFalse(session.combat_rolls[sol.faction][0]['hit'])
        with self.assertRaises(MovementError):
            movement.use_supercharge('naaz')

    def test_letnev_agent_adds_one_die_to_the_selected_ship(self):
        board, players, movement = game('letnev')
        letnev, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('letnev-agent-carrier', 'carrier', 'letnev', letnev.color_code,
                       UnitLocation(Region.SPACE))
        dreadnought = Unit('letnev-agent-dreadnought', 'dreadnought', 'letnev',
                           letnev.color_code, UnitLocation(Region.SPACE))
        enemy = Unit('sol-agent-opponent', 'carrier', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((carrier, dreadnought, enemy))
        session = Session(letnev, target, {}, Snapshot.capture(board, letnev, players))
        session.stage = 'space_combat'
        session.combat_factions = ('letnev', 'sol')
        movement.session = session

        movement.use_combat_agent_die('letnev', dreadnought.unit_id)
        with patch('movement.random.randint', return_value=5):
            movement.advance_combat()

        rolls = session.combat_rolls['letnev']
        self.assertEqual(3, len(rolls))
        bonus = [roll for roll in rolls if roll.get('bonus_source') == 'letnevagent']
        self.assertEqual(1, len(bonus))
        self.assertEqual(dreadnought.unit_id, bonus[0]['unit_id'])
        self.assertFalse(leader_available(letnev, 'letnevagent'))

    def test_sol_agent_adds_one_die_to_the_selected_ground_force(self):
        board, players, movement = game('sol')
        sol, jolnar = players[:2]
        target = next(tile for tile in board.values() if tile.planets)
        planet_id = target.planets[0].planet_id
        infantry = [Unit(f'sol-agent-infantry-{index}', 'infantry', 'sol', sol.color_code,
                          UnitLocation(Region.PLANET, planet_id)) for index in range(2)]
        enemy = Unit('jolnar-agent-opponent', 'infantry', 'jolnar', jolnar.color_code,
                     UnitLocation(Region.PLANET, planet_id))
        target.units.extend((*infantry, enemy))
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players))
        session.stage = 'ground_combat'
        session.combat_type = 'ground'
        session.combat_planet_id = planet_id
        session.combat_factions = ('sol', 'jolnar')
        movement.session = session

        movement.use_combat_agent_die('sol', infantry[1].unit_id)
        with patch('movement.random.randint', return_value=1):
            movement.advance_combat()

        rolls = session.combat_rolls['sol']
        self.assertEqual(3, len(rolls))
        self.assertEqual(1, sum(roll.get('bonus_source') == 'solagent' for roll in rolls))
        self.assertEqual(infantry[1].unit_id,
                         next(roll['unit_id'] for roll in rolls
                              if roll.get('bonus_source') == 'solagent'))
        self.assertFalse(leader_available(sol, 'solagent'))

    def test_scanlink_explores_planet_once_and_undo_restores_fragment(self):
        board, players, movement = game()
        sol = players[0]
        sol.technologies |= {'sdn'}
        target = next(tile for tile in board.values() if tile.planets and
                      tile.planets[0].planet_type.lower() in
                      ('cultural', 'hazardous', 'industrial') and
                      sol.faction not in tile.command_tokens)
        planet = target.planets[0]
        unit = Unit('scanlink-infantry', 'infantry', sol.faction, sol.color_code,
                    UnitLocation(Region.PLANET, planet.planet_id))
        target.units.append(unit)
        target.planet_owners[planet.planet_id] = sol.faction
        fragment = {'cultural': 'crf1', 'hazardous': 'hrf1',
                    'industrial': 'irf1'}[planet.planet_type.lower()]
        decks = {'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []}
        decks[planet.planet_type.lower()] = [fragment]
        movement.exploration = ExplorationController(board, players, decks=decks,
                                                     relic_deck=[])
        movement.activate(sol, target.position)
        self.assertEqual((planet.planet_id,), movement.scanlink_planets())
        outcome = movement.explore_with_scanlink(planet.planet_id)
        self.assertEqual(fragment, outcome.card.alias)
        self.assertEqual([fragment], movement.exploration.fragments[sol.faction])
        with self.assertRaises(MovementError):
            movement.explore_with_scanlink(planet.planet_id)
        movement.cancel()
        self.assertEqual([], movement.exploration.fragments[sol.faction])

    def test_titans_scanlink_places_visible_sleeper_after_exploration(self):
        board, players, movement = game('titans')
        titans = players[0]
        titans.technologies |= {'sdn'}
        traits = {'cultural': 'crf1', 'hazardous': 'hrf1', 'industrial': 'irf1'}
        target, planet = next((tile, planet) for tile in board.values()
                              for planet in tile.planets
                              if planet.planet_type and planet.planet_type.lower() in traits and
                              not planet.faction_homeworld and
                              tile.planet_owners.get(planet.planet_id) is None and
                              titans.faction not in tile.command_tokens)
        target.planet_owners[planet.planet_id] = titans.faction
        titans.planets.append(PlanetCard(planet))
        target.units.append(Unit('titans-scanlink-infantry', 'infantry', titans.faction,
                                 titans.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        decks = {'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []}
        decks[planet.planet_type.lower()] = [traits[planet.planet_type.lower()]]
        movement.exploration = ExplorationController(board, players, decks=decks,
                                                     relic_deck=[])

        movement.activate(titans, target.position)
        movement.explore_with_scanlink(planet.planet_id)

        self.assertIn(planet.planet_id, target.sleeper_tokens)
        self.assertIsNone(movement.exploration.terragenesis_pending)

    def test_titans_awaken_replaces_only_sleepers_present_at_activation(self):
        board, players, movement = game('titans')
        titans = players[0]
        target = next(tile for tile in board.values()
                      if len(tile.planets) >= 2 and
                      titans.faction not in tile.command_tokens and
                      not tile.sleeper_tokens)
        original, added_later = target.planets[:2]
        target.sleeper_tokens.add(original.planet_id)

        session = movement.activate(titans, target.position)
        target.sleeper_tokens.add(added_later.planet_id)
        placed = movement.awaken_titans_sleepers()

        self.assertEqual([original.planet_id], [unit.location.planet_id for unit in placed])
        self.assertNotIn(original.planet_id, target.sleeper_tokens)
        self.assertIn(added_later.planet_id, target.sleeper_tokens)
        self.assertEqual({original.planet_id}, set(session.titans_awakened_planets))

    def test_titans_awaken_can_relocate_pds_from_an_unactivated_system(self):
        board, players, movement = game('titans')
        titans = players[0]
        target = next(tile for tile in board.values()
                      if tile.planets and titans.faction not in tile.command_tokens)
        planet = target.planets[0]
        target.sleeper_tokens.add(planet.planet_id)
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == titans.faction and unit.kind == 'pds')]
        sources = [tile for tile in board.values()
                   if tile is not target and tile.planets and
                   titans.faction not in tile.command_tokens]
        pds_planets = [(tile, p) for tile in sources for p in tile.planets]
        for index, (source, source_planet) in enumerate(pds_planets[:6]):
            source.units.append(Unit(f'titans-reserve-pds-{index}', 'pds', titans.faction,
                                     titans.color_code,
                                     UnitLocation(Region.PLANET, source_planet.planet_id)))
        source_tile, source_pds = next(
            (tile, unit) for tile in sources for unit in tile.units
            if unit.owner == titans.faction and unit.kind == 'pds')
        movement.activate(titans, target.position)

        placed = movement.awaken_titans_sleepers()

        self.assertEqual(1, len(placed))
        self.assertIs(placed[0], source_pds)
        self.assertNotIn(source_pds, source_tile.units)
        self.assertIn(source_pds, target.units)
        self.assertEqual(6, sum(unit.owner == titans.faction and unit.kind == 'pds'
                                for tile in board.values() for unit in tile.units))

    def test_titans_awaken_triggers_ground_combat_on_enemy_planet(self):
        board, players, movement = game('titans')
        titans, defender = players[:2]
        target = next(tile for tile in board.values()
                      if tile.planets and not tile.units and
                      titans.faction not in tile.command_tokens)
        planet = target.planets[0]
        target.sleeper_tokens.add(planet.planet_id)
        target.planet_owners[planet.planet_id] = defender.faction
        target.units.append(Unit('enemy-planet-infantry', 'infantry', defender.faction,
                                 defender.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        session = movement.activate(titans, target.position)

        movement.awaken_titans_sleepers()
        session.stage = 'invasion'
        movement.establish_control()

        self.assertEqual('ground_combat', session.stage)
        self.assertEqual(planet.planet_id, session.combat_planet_id)
        self.assertEqual(1, len(movement.combat_units(session, titans.faction)))

    def test_titans_awaken_captures_enemy_planet_without_ground_forces(self):
        board, players, movement = game('titans')
        titans, defender = players[:2]
        target = next(tile for tile in board.values()
                      if tile.planets and not tile.units and
                      titans.faction not in tile.command_tokens)
        planet = target.planets[0]
        target.sleeper_tokens.add(planet.planet_id)
        target.planet_owners[planet.planet_id] = defender.faction
        defender.planets.append(PlanetCard(planet))
        enemy_pds = Unit('enemy-planet-pds', 'pds', defender.faction,
                         defender.color_code, UnitLocation(Region.PLANET, planet.planet_id))
        target.units.append(enemy_pds)
        session = movement.activate(titans, target.position)

        movement.awaken_titans_sleepers()

        self.assertEqual(titans.faction, target.planet_owners[planet.planet_id])
        self.assertNotIn(enemy_pds, target.units)
        self.assertIn(planet.planet_id, session.titans_awakened_captured)
        self.assertTrue(any(card.planet.planet_id == planet.planet_id
                            for card in titans.planets))

    def test_titans_terragenesis_prompts_to_move_a_sleeper_when_supply_is_full(self):
        board, players, movement = game('titans')
        titans = players[0]
        titans.technologies |= {'sdn'}
        traits = {'cultural': 'crf1', 'hazardous': 'hrf1', 'industrial': 'irf1'}
        target, planet = next((tile, planet) for tile in board.values()
                              for planet in tile.planets
                              if planet.planet_type and planet.planet_type.lower() in traits and
                              not planet.faction_homeworld and
                              tile.planet_owners.get(planet.planet_id) is None and
                              titans.faction not in tile.command_tokens)
        target.planet_owners[planet.planet_id] = titans.faction
        titans.planets.append(PlanetCard(planet))
        target.units.append(Unit('titans-full-supply-infantry', 'infantry', titans.faction,
                                 titans.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        existing_sleepers = [candidate.planet_id for tile in board.values()
                             for candidate in tile.planets
                             if candidate.planet_id != planet.planet_id][:5]
        for tile in board.values():
            tile.sleeper_tokens.update(candidate.planet_id for candidate in tile.planets
                                       if candidate.planet_id in existing_sleepers)
        decks = {'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []}
        decks[planet.planet_type.lower()] = [traits[planet.planet_type.lower()]]
        movement.exploration = ExplorationController(board, players, decks=decks,
                                                     relic_deck=[])

        movement.activate(titans, target.position)
        movement.explore_with_scanlink(planet.planet_id)

        pending = movement.exploration.terragenesis_pending
        self.assertIsNotNone(pending)
        self.assertEqual((titans.faction, target.position, planet.planet_id),
                         (pending.faction, pending.position, pending.planet_id))
        source = existing_sleepers[0]
        movement.resolve_terragenesis(source)
        self.assertIn(planet.planet_id, target.sleeper_tokens)
        self.assertFalse(any(source in tile.sleeper_tokens for tile in board.values()))
        self.assertIsNone(movement.exploration.terragenesis_pending)

    def test_self_assembly_and_ai_development_production_are_optional(self):
        board, players, movement = game()
        sol = players[0]
        sol.technologies |= {'sar', 'aida', 'ff2'}
        home = next(tile for tile in board.values()
                    if sol.faction in tile.planet_owners.values())
        dock = next(unit for unit in home.units if unit.owner == sol.faction and
                    unit.kind == 'spacedock')
        planet_id = dock.location.planet_id
        session = movement.start_strategy_production(sol, home, dock)
        movement.adjust_production('carrier', 1)
        self.assertEqual(3, movement.production_cost(session))
        self.assertTrue(movement.toggle_ai_development_discount())
        self.assertEqual(2, movement.production_cost(session))
        self.assertIn(planet_id, movement.self_assembly_planets())
        movement.choose_self_assembly_planet(planet_id)
        sol.trade_goods = 3
        movement.change_production_trade_goods(2)
        movement.produce()
        self.assertIn('sar', sol.exhausted_technologies)
        self.assertIn('aida', sol.exhausted_technologies)
        built_mech = next(unit for unit in home.units if unit.unit_id.startswith('sol-self-assembly'))
        self.assertEqual(planet_id, built_mech.location.planet_id)
        before = sol.trade_goods
        movement.destroy_unit(home, built_mech)
        self.assertEqual(before + 1, sol.trade_goods)

    def test_argent_afb_rolls_without_fighters_and_hits_infantry_and_sustain_ships(self):
        board, players, movement = game('argent')
        argent, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        destroyer = Unit('argent-wing-ii', 'destroyer', 'argent', argent.color_code,
                         UnitLocation(Region.SPACE), profile_id='argent_destroyer2')
        dread = Unit('sol-dread', 'dreadnought', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        infantry = Unit('sol-passenger', 'infantry', sol.faction, sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=dread.unit_id))
        target.units.extend((destroyer, dread, infantry))
        session = Session(argent, target, {}, Snapshot.capture(board, argent, players))
        session.stage = 'space_combat'
        session.combat_type = 'space'
        session.combat_factions = ('argent', sol.faction)
        with patch('movement.random.randint', return_value=9):
            movement.resolve_anti_fighter_barrage(session)
        self.assertEqual(3, len(session.afb_rolls['argent']))
        self.assertTrue(dread.damaged)
        self.assertNotIn(infantry, target.units)

    def test_cabal_dock_supports_six_fighters_without_capacity(self):
        board, players, movement = game('cabal')
        cabal = players[0]
        home = next(tile for tile in board.values() if any(
            unit.owner == 'cabal' and unit.kind == 'spacedock' for unit in tile.units))
        home.units[:] = [unit for unit in home.units if unit.kind == 'spacedock']
        for index in range(6):
            home.units.append(Unit(f'cabal-fighter-{index}', 'fighter', 'cabal',
                                   cabal.color_code, UnitLocation(Region.SPACE)))
        self.assertEqual(0, movement.capacity_overflow(home, 'cabal'))
        home.units.append(Unit('cabal-fighter-7', 'fighter', 'cabal',
                               cabal.color_code, UnitLocation(Region.SPACE)))
        self.assertEqual(1, movement.capacity_overflow(home, 'cabal'))

    def test_argent_mech_needs_a_capacity_ship_only_when_left_in_space(self):
        board, players, movement = game('argent')
        argent = players[0]
        target = next(tile for tile in board.values() if not tile.units)
        mech = Unit('argent-mech', 'mech', 'argent', argent.color_code,
                    UnitLocation(Region.TRANSPORT, carrier_id='old-carrier'),
                    profile_id='argent_mech')
        mech.location = UnitLocation(Region.SPACE)
        target.units.append(mech)
        self.assertEqual(1, movement.capacity_overflow(target, 'argent'))
        carrier = Unit('argent-carrier', 'carrier', 'argent', argent.color_code,
                       UnitLocation(Region.SPACE))
        target.units.append(carrier)
        self.assertEqual(0, movement.capacity_overflow(target, 'argent'))

    def test_cabal_amalgamation_spends_matching_capture_for_free_production(self):
        board, players, movement = game('cabal')
        cabal = players[0]
        home = next(tile for tile in board.values() if cabal.faction in tile.planet_owners.values())
        dock = next(unit for unit in home.units if unit.owner == 'cabal' and
                    unit.kind == 'spacedock')
        cabal.captured_units['carrier'] = 1
        session = movement.start_strategy_production(cabal, home, dock)
        movement.adjust_production('carrier', 1)
        self.assertEqual(3, movement.production_cost(session))
        self.assertEqual(1, movement.adjust_amalgamation('carrier', 1))
        self.assertEqual(0, movement.production_cost(session))
        movement.produce()
        self.assertNotIn('carrier', cabal.captured_units)
        self.assertEqual(1, sum(unit.owner == 'cabal' and unit.kind == 'carrier' and
                                unit.unit_id.startswith('cabal-built-') for unit in home.units))

    def test_cabal_can_amalgamate_a_captured_unit_when_own_supply_is_empty(self):
        board, players, movement = game('cabal')
        cabal = players[0]
        home = next(tile for tile in board.values()
                    if cabal.faction in tile.planet_owners.values())
        dock = next(unit for unit in home.units if unit.owner == 'cabal' and
                    unit.kind == 'spacedock')
        other_systems = [tile for tile in board.values() if tile is not home]
        for index in range(7):
            tile = other_systems[index]
            tile.units.append(Unit(f'cabal-cruiser-reserve-{index}', 'cruiser', 'cabal',
                                   cabal.color_code, UnitLocation(Region.SPACE)))
        cabal.captured_units['cruiser'] = 1
        self.assertEqual(0, unit_supply_remaining(cabal, board, 'cruiser'))

        movement.start_strategy_production(cabal, home, dock)
        movement.adjust_production('cruiser', 1)
        self.assertEqual(2, movement.production_cost(movement.session))
        self.assertEqual(1, movement.adjust_amalgamation('cruiser', 1))
        self.assertEqual(0, movement.production_cost(movement.session))
        movement.produce()

        self.assertNotIn('cruiser', cabal.captured_units)
        self.assertEqual(9, sum(unit.owner == 'cabal' and unit.kind == 'cruiser'
                                for tile in board.values() for unit in tile.units))

    def test_cabal_flagship_captures_own_destroyed_ship(self):
        board, players, movement = game('cabal')
        cabal = players[0]
        tile = next(tile for tile in board.values() if not tile.units)
        flagship = Unit('cabal-flagship', 'flagship', 'cabal', cabal.color_code,
                        UnitLocation(Region.SPACE))
        cruiser = Unit('cabal-cruiser', 'cruiser', 'cabal', cabal.color_code,
                       UnitLocation(Region.SPACE))
        tile.units.extend((flagship, cruiser))
        movement.destroy_unit(tile, cruiser)
        self.assertEqual(1, cabal.captured_units['cruiser'])

    def test_cabal_dimensional_tear_grants_move_and_rolls_only_for_other_players(self):
        board, players, movement = game('cabal')
        cabal, sol = players[:2]
        home = next(tile for tile in board.values() if cabal.faction in tile.planet_owners.values())
        carrier = next(unit for unit in home.units if unit.owner == 'cabal' and
                       unit.kind == 'carrier')
        neighbors = {tile.position for tile in board.neighbors(home.position)}
        distant = next(tile for adjacent in board.neighbors(home.position)
                       for tile in board.neighbors(adjacent.position)
                       if tile is not home and tile.position not in neighbors and
                       not tile.anomalies and not tile.command_tokens and
                       movement.route(home, tile, carrier, cabal))
        self.assertEqual(3, len(movement.route(home, distant, carrier, cabal)))
        target = next(tile for tile in board.neighbors(home.position)
                      if not tile.anomalies and not tile.command_tokens)
        intruder = Unit('sol-in-cabal-rift', 'carrier', sol.faction, sol.color_code,
                        UnitLocation(Region.SPACE))
        passenger = Unit('sol-rift-passenger', 'infantry', sol.faction, sol.color_code,
                         UnitLocation(Region.TRANSPORT, carrier_id=intruder.unit_id))
        home.units.extend((intruder, passenger))
        session = movement.activate(sol, target.position)
        session.toggle(intruder.unit_id)
        with patch('movement.random.randint', return_value=2):
            movement.confirm()
        self.assertNotIn(intruder, home.units)
        self.assertNotIn(intruder, target.units)
        self.assertNotIn(passenger, home.units)
        self.assertNotIn(passenger, target.units)
        self.assertIn('removed', session.gravity_rift_log[0])
        self.assertTrue(movement.undo())
        self.assertIn(intruder, home.units)
        self.assertIn(passenger, home.units)
        self.assertEqual('movement', movement.session.stage)

    def test_naaz_mech_fights_in_space_then_returns_to_ground_face(self):
        board, players, movement = game('naaz')
        naaz, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('naaz-carrier', 'carrier', 'naaz', naaz.color_code,
                       UnitLocation(Region.SPACE))
        mech = Unit('naaz-mech', 'mech', 'naaz', naaz.color_code,
                    UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        enemy = Unit('sol-carrier', 'carrier', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((carrier, mech, enemy))
        session = Session(naaz, target, {}, Snapshot.capture(board, naaz, players))
        movement.start_combat(session)
        self.assertEqual('naaz_mech_space', mech.profile_id)
        self.assertIn(mech, movement.combat_units(session, 'naaz'))
        movement._reset_naaz_space_mechs(session)
        self.assertEqual('naaz_mech', mech.profile_id)
        self.assertEqual(Region.SPACE_AREA, mech.location.region)
        self.assertTrue(any(unit.unit_id == mech.unit_id
                            for placement in layout_units(target, True)
                            for unit in placement.units))
        session.snapshot.restore()
        self.assertIsNone(mech.profile_id)
        self.assertEqual(Region.TRANSPORT, mech.location.region)

    def test_naaz_mech_in_space_area_flips_for_combat_then_remains_renderable(self):
        board, players, movement = game('naaz')
        naaz, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        mech = Unit('naaz-ground-side', 'mech', 'naaz', naaz.color_code,
                    UnitLocation(Region.SPACE_AREA))
        enemy = Unit('sol-carrier', 'carrier', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((mech, enemy))
        session = Session(naaz, target, {}, Snapshot.capture(board, naaz, players))

        movement.start_combat(session)

        self.assertEqual(Region.SPACE, mech.location.region)
        self.assertEqual('naaz_mech_space', mech.profile_id)
        self.assertIn(mech, movement.combat_units(session, 'naaz'))
        movement._reset_naaz_space_mechs(session)
        self.assertEqual(Region.SPACE_AREA, mech.location.region)
        self.assertEqual('naaz_mech', mech.profile_id)
        self.assertIn(mech, movement.landing_forces(session))
        self.assertTrue(any(unit.unit_id == mech.unit_id
                            for placement in layout_units(target, True)
                            for unit in placement.units))

    def test_naaz_flagship_adds_one_combat_die_to_each_mech(self):
        board, players, movement = game('naaz')
        naaz, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        flagship = Unit('naaz-flagship', 'flagship', 'naaz', naaz.color_code,
                        UnitLocation(Region.SPACE))
        mech = Unit('naaz-space-mech', 'mech', 'naaz', naaz.color_code,
                    UnitLocation(Region.SPACE), profile_id='naaz_mech_space')
        enemy = Unit('sol-ship', 'carrier', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((flagship, mech, enemy))
        session = Session(naaz, target, {}, Snapshot.capture(board, naaz, players))
        session.stage = 'space_combat'
        session.combat_type = 'space'
        session.combat_factions = ('naaz', sol.faction)
        movement.session = session
        with patch('movement.random.randint', return_value=1):
            movement.advance_combat()
        self.assertEqual(3, sum(roll['unit_id'] == mech.unit_id
                                for roll in session.combat_rolls['naaz']))

    def test_argent_flagship_blocks_enemy_space_cannon(self):
        board, players, movement = game('argent')
        argent, sol = players[:2]
        target = next(tile for tile in board.values() if tile.planets)
        flagship = Unit('argent-flagship', 'flagship', 'argent', argent.color_code,
                        UnitLocation(Region.SPACE))
        cannon = Unit('sol-cannon', 'pds', sol.faction, sol.color_code,
                      UnitLocation(Region.PLANET, target.planets[0].planet_id))
        target.units.extend((flagship, cannon))
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players))
        with patch('movement.random.randint') as die:
            movement._roll_space_cannon(session, [(target, cannon, unit_profile(cannon))],
                                        'argent')
        die.assert_not_called()
        self.assertFalse(session.space_cannon_events)

    def test_mahact_flagship_gets_bonus_without_enemy_fleet_token(self):
        board, players, movement = game('mahact')
        mahact, sol = players[:2]
        target = next(tile for tile in board.values() if not tile.units)
        flagship = Unit('mahact-flagship', 'flagship', 'mahact', mahact.color_code,
                        UnitLocation(Region.SPACE))
        enemy = Unit('sol-ship', 'carrier', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((flagship, enemy))
        session = Session(mahact, target, {}, Snapshot.capture(board, mahact, players))
        session.stage = 'space_combat'
        session.combat_type = 'space'
        session.combat_factions = ('mahact', sol.faction)
        movement.session = session
        with patch('movement.random.randint', return_value=4):
            movement.advance_combat()
        self.assertTrue(all(roll['modifier'] == 2 and roll['hit']
                            for roll in session.combat_rolls['mahact']))

    def test_xxcha_mech_can_fire_space_cannon_into_adjacent_system(self):
        board, players, movement = game('xxcha')
        xxcha, sol = players[:2]
        home = next(tile for tile in board.values() if xxcha.faction in tile.planet_owners.values())
        target = next(tile for tile in board.neighbors(home.position) if not tile.command_tokens)
        mech = Unit('xxcha-mech', 'mech', 'xxcha', xxcha.color_code,
                    UnitLocation(Region.PLANET, home.planets[0].planet_id))
        home.units.append(mech)
        target.units.append(Unit('sol-target', 'carrier', sol.faction, sol.color_code,
                                 UnitLocation(Region.SPACE)))
        session = Session(xxcha, target, {}, Snapshot.capture(board, xxcha, players))
        self.assertIn(mech, [unit for _, unit, _ in movement._space_cannon_shooters(session)])

    def test_l1z1x_assimilate_does_not_exceed_structure_supply(self):
        for kind, limit in (('pds', 6), ('spacedock', 3)):
            with self.subTest(kind=kind):
                board, players, movement = game('l1z1x')
                l1z1x, sol = players[:2]
                target = next(tile for tile in board.values()
                              if tile.planets and not tile.custodians_present and
                              not any(planet.faction_homeworld for planet in tile.planets))
                planet = target.planets[0]
                target.units.append(Unit(f'enemy-{kind}', kind, sol.faction,
                                         sol.color_code,
                                         UnitLocation(Region.PLANET, planet.planet_id)))

                reserve_planets = [candidate for tile in board.values() if tile is not target
                                   for candidate in tile.planets]
                placed = sum(unit.owner == l1z1x.faction and unit.kind == kind
                             for tile in board.values() for unit in tile.units)
                per_planet = 2 if kind == 'pds' else 1
                for reserve_planet in reserve_planets:
                    board_tile = next(tile for tile in board.values()
                                      if reserve_planet in tile.planets)
                    on_planet = sum(unit.owner == l1z1x.faction and unit.kind == kind and
                                    unit.location.region == Region.PLANET and
                                    unit.location.planet_id == reserve_planet.planet_id
                                    for unit in board_tile.units)
                    for _ in range(min(per_planet - on_planet, limit - placed)):
                        board_tile.units.append(Unit(
                            f'l1z1x-{kind}-{placed}', kind, l1z1x.faction,
                            l1z1x.color_code,
                            UnitLocation(Region.PLANET, reserve_planet.planet_id)))
                        placed += 1
                    if placed == limit:
                        break
                self.assertEqual(limit, placed)

                session = Session(l1z1x, target, {},
                                  Snapshot.capture(board, l1z1x, players))
                self.assertTrue(movement.capture_planet(session, planet.planet_id))
                self.assertFalse(any(unit.owner == 'sol' and unit.kind == kind and
                                     unit.location.planet_id == planet.planet_id
                                     for unit in target.units))
                self.assertFalse(any(unit.owner == 'l1z1x' and unit.kind == kind and
                                     unit.location.planet_id == planet.planet_id
                                     for unit in target.units))
                self.assertEqual(limit, sum(unit.owner == 'l1z1x' and unit.kind == kind
                                            for tile in board.values() for unit in tile.units))

    def test_l1z1x_mech_bombards_from_other_planet(self):
        board, players, movement = game('l1z1x')
        l1z1x, sol = players[:2]
        target = next(tile for tile in board.values() if len(tile.planets) >= 2 and
                      not tile.units)
        own, enemy = target.planets[:2]
        mech = Unit('l1z1x-mech', 'mech', 'l1z1x', l1z1x.color_code,
                    UnitLocation(Region.PLANET, own.planet_id))
        target.units.extend((mech, Unit('sol-ground', 'infantry', sol.faction,
                                        sol.color_code, UnitLocation(Region.PLANET,
                                                                     enemy.planet_id))))
        session = Session(l1z1x, target, {}, Snapshot.capture(board, l1z1x, players))
        movement.continue_invasion_start(session)
        self.assertEqual('bombardment', session.stage)
        self.assertIn(mech.unit_id, session.bombard_targets)
        movement.session = session
        movement.cycle_bombardment_target(mech.unit_id)
        self.assertEqual(enemy.planet_id, session.bombard_targets[mech.unit_id])

    def test_titans_pds_is_ground_combatant_but_cannot_be_loaded(self):
        board, players, movement = game('titans')
        titans = players[0]
        home = next(tile for tile in board.values() if titans.faction in tile.planet_owners.values())
        pds = Unit('titans-pds', 'pds', titans.faction, titans.color_code,
                   UnitLocation(Region.PLANET, home.planets[0].planet_id))
        home.units.append(pds)
        session = Session(titans, home, {}, Snapshot.capture(board, titans, players))
        session.combat_type = 'ground'
        session.combat_planet_id = pds.location.planet_id
        self.assertIn(pds, movement.combat_units(session, titans.faction))
        self.assertNotIn(pds, movement._movement_passengers(home, titans))

    def test_mentak_mech_prevents_opponent_mech_sustain(self):
        board, players, movement = game('mentak')
        mentak, sol = players[:2]
        target = next(tile for tile in board.values() if tile.planets)
        planet_id = target.planets[0].planet_id
        guard = Unit('mentak-guard', 'mech', 'mentak', mentak.color_code,
                     UnitLocation(Region.PLANET, planet_id))
        invader = Unit('sol-mech', 'mech', sol.faction, sol.color_code,
                       UnitLocation(Region.PLANET, planet_id))
        target.units.extend((guard, invader))
        movement.apply_hit(target, [invader])
        self.assertNotIn(invader, target.units)

    def test_shard_transfers_when_enemy_homeworld_is_captured(self):
        board, players, movement = game()
        sol, jolnar = players[:2]
        movement.exploration = ExplorationController(board, players, relic_deck=['shard'])
        movement.exploration.relics.draw(jolnar.faction)
        home = next(tile for tile in board.values() if any(
            planet.faction_homeworld == jolnar.faction for planet in tile.planets))
        planet = next(planet for planet in home.planets
                      if planet.faction_homeworld == jolnar.faction)
        session = Session(sol, home, {}, Snapshot.capture(board, sol, players,
                                                          exploration=movement.exploration))
        movement.capture_planet(session, planet.planet_id)
        self.assertEqual(sol.faction, movement.exploration.relics.owner('shard'))
        self.assertEqual(1, movement.exploration.relics.victory_points[sol.faction])
        self.assertEqual(0, movement.exploration.relics.victory_points[jolnar.faction])

    def test_nomad_flagship_can_jump_to_a_system_with_own_mech(self):
        board, players, movement = game('nomad')
        nomad = players[0]
        home = next(tile for tile in board.values() if nomad.faction in tile.planet_owners.values())
        flagship = next(unit for unit in home.units if unit.owner == 'nomad' and
                        unit.kind == 'flagship')
        neighbors = {tile.position for tile in board.neighbors(home.position)}
        distant = next(tile for tile in board.values() if tile.position not in neighbors and
                       tile is not home and not tile.anomalies and not tile.command_tokens)
        distant.units.append(Unit('nomad-path-mech', 'mech', 'nomad', nomad.color_code,
                                  UnitLocation(Region.PLANET,
                                               (distant.planets[0].planet_id if distant.planets else
                                                home.planets[0].planet_id))))
        self.assertEqual((home.position, distant.position),
                         movement.route(home, distant, flagship, nomad))

    def test_keleres_patronage_uses_dynamis_commodity_value(self):
        from pok_factions import keleres_council_patronage
        board, players, _ = game('keleresm')
        keleres = players[0]
        exploration = ExplorationController(board, players, relic_deck=['dynamiscore'])
        exploration.relics.draw(keleres.faction)
        keleres.commodities = 0
        keleres_council_patronage(keleres, exploration)
        self.assertEqual(keleres.commodity_limit + 2, keleres.commodities)

    def test_sardakk_mech_sustain_produces_assignable_ground_hit(self):
        board, players, movement = game('sardakk')
        sardakk, sol = players[:2]
        target = next(tile for tile in board.values() if tile.planets)
        planet_id = target.planets[0].planet_id
        mech = Unit('sardakk-mech', 'mech', 'sardakk', sardakk.color_code,
                    UnitLocation(Region.PLANET, planet_id))
        enemy = Unit('sol-infantry', 'infantry', sol.faction, sol.color_code,
                     UnitLocation(Region.PLANET, planet_id))
        target.units.extend((mech, enemy))
        session = Session(sardakk, target, {}, Snapshot.capture(board, sardakk, players))
        session.stage = 'ground_combat'
        session.combat_type = 'ground'
        session.combat_planet_id = planet_id
        session.combat_factions = ('sardakk', sol.faction)
        session.combat_hits = {'sardakk': 1, sol.faction: 0}
        session.combat_needs_resolution = True
        movement.session = session
        movement.assign_combat_hit('sardakk', 'mech')
        movement.advance_combat()
        self.assertTrue(mech.damaged)
        self.assertEqual(1, session.combat_hits[sol.faction])
        self.assertTrue(session.combat_needs_resolution)
        movement.assign_combat_hit(sol.faction, 'infantry')
        movement.advance_combat()
        self.assertNotIn(enemy, target.units)

    def test_muaat_can_produce_prototype_war_sun_and_unlock_commander(self):
        from leaders import leader_available
        board, players, movement = game('muaat')
        muaat = players[0]
        home = next(tile for tile in board.values() if muaat.faction in tile.planet_owners.values())
        dock = next(unit for unit in home.units if unit.owner == 'muaat' and
                    unit.kind == 'spacedock')
        self.assertFalse(leader_available(muaat, 'muaatcommander'))
        movement.start_strategy_production(muaat, home, dock)
        movement.adjust_production('warsun', 1)
        muaat.trade_goods = 12
        movement.change_production_trade_goods(12)
        movement.produce()
        self.assertTrue(leader_available(muaat, 'muaatcommander'))
        self.assertEqual(2, sum(unit.owner == 'muaat' and unit.kind == 'warsun'
                                for unit in home.units))

    def test_winnu_commander_unlocks_on_entering_mecatol_combat(self):
        from leaders import leader_available
        board, players, movement = game('winnu')
        winnu, sol = players[:2]
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        mecatol.units.extend((
            Unit('winnu-combat', 'carrier', 'winnu', winnu.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('sol-combat', 'carrier', sol.faction, sol.color_code,
                 UnitLocation(Region.SPACE)),
        ))
        session = Session(winnu, mecatol, {}, Snapshot.capture(board, winnu, players))
        self.assertFalse(leader_available(winnu, 'winnucommander'))
        movement.start_combat(session)
        self.assertTrue(leader_available(winnu, 'winnucommander'))

    def test_yin_indoctrination_unlocks_commander(self):
        from leaders import leader_available
        board, players, movement = game('yin')
        yin, sol = players[:2]
        home = next(tile for tile in board.values() if yin.faction in tile.planet_owners.values())
        planet_id = home.planets[0].planet_id
        enemy = Unit('sol-ground', 'infantry', sol.faction, sol.color_code,
                     UnitLocation(Region.PLANET, planet_id))
        home.units.append(enemy)
        session = Session(sol, home, {}, Snapshot.capture(board, sol, players))
        self.assertFalse(leader_available(yin, 'yincommander'))
        movement.start_ground_combat(session, planet_id)
        self.assertTrue(leader_available(yin, 'yincommander'))


if __name__ == '__main__':
    unittest.main()
