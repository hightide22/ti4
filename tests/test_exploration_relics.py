from __future__ import annotations

import random
import unittest

from board import Board, ROOT, TILES, load_board
from exploration import ExplorationController, exploration_catalog
from leaders import leader_available
from movement import MovementController, Session, Snapshot
try:
    from exploration_panel import ExplorationPanel
except ModuleNotFoundError as exc:
    if exc.name != 'arcade':
        raise
    ExplorationPanel = None
from player import PlanetCard, PlayerState, create_players
from relics import RelicController, relic_catalog
from units import Region, Unit, UnitLocation, layout_units, system_inventory


def make_game(planet_tile=30):
    tile = TILES.create(planet_tile, (0, 0))
    board = Board([tile])
    player = PlayerState('sol', 'The Federation of Sol', 'blu', 4,
                         [PlanetCard(planet) for planet in tile.planets])
    for planet in tile.planets:
        tile.planet_owners[planet.planet_id] = player.faction
    return board, tile, player


class ExplorationDeckTests(unittest.TestCase):
    def _freelancers_offer(self):
        board, tile, player = make_game()
        engine = ExplorationController(board, [player], decks={
            'cultural': ['frln1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        movement = MovementController(board, [player])
        engine.explore_planet(player, tile, 'mellon')
        result = engine.resolve_pending('produce_one_unit')
        self.assertEqual('production', result.events[0]['type'])
        return board, tile, player, engine, movement

    def test_freelancers_war_sun_production_unlocks_muaat_commander(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('muaat', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        muaat = next(player for player in players if player.faction == 'muaat')
        home = next(tile for tile in board.values()
                    if 'muaat' in tile.planet_owners.values())
        planet_id = next(planet_id for planet_id, owner in home.planet_owners.items()
                         if owner == 'muaat')
        engine = ExplorationController(board, players, relic_deck=[], decks={
            'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []})
        movement = MovementController(board, players)
        engine.freelancer_offer = ('muaat', home.position, planet_id)
        muaat.trade_goods = 12

        self.assertFalse(leader_available(muaat, 'muaatcommander'))
        engine.produce_freelancers(muaat, movement, 'warsun',
                                   planet_id=planet_id, trade_goods=12)

        self.assertTrue(leader_available(muaat, 'muaatcommander'))

    def test_freelancers_produces_one_ship_using_influence(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        event = engine.produce_freelancers(player, movement, 'cruiser',
                                           influence_planets=('mellon',))

        self.assertEqual('unit_produced', event['type'])
        self.assertTrue(event['check_capacity_and_fleet'])
        self.assertEqual(1, sum(unit.kind == 'cruiser' for unit in tile.units))
        self.assertTrue(next(card for card in player.planets
                             if card.planet.planet_id == 'mellon').exhausted)
        self.assertIsNone(engine.freelancer_offer)
        with self.assertRaises(ValueError):
            engine.produce_freelancers(player, movement, 'cruiser', trade_goods=2)

    def test_freelancers_cannot_exceed_finite_ship_supply(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        tile.units.extend(Unit(f'freelancers-carrier-{index}', 'carrier', player.faction,
                               player.color_code, UnitLocation(Region.SPACE))
                          for index in range(4))
        player.trade_goods = 10

        with self.assertRaisesRegex(ValueError, 'No unit of this type remains'):
            engine.produce_freelancers(player, movement, 'carrier', trade_goods=3)

        self.assertEqual(4, sum(unit.owner == player.faction and unit.kind == 'carrier'
                                for unit in tile.units))
        self.assertEqual(10, player.trade_goods)
        self.assertIsNotNone(engine.freelancer_offer)

    def test_freelancers_payment_validation_is_atomic(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        player.trade_goods = 2
        with self.assertRaises(ValueError):
            engine.produce_freelancers(player, movement, 'dreadnought',
                                       resource_planets=('mellon',), trade_goods=1)
        with self.assertRaises(ValueError):
            engine.produce_freelancers(player, movement, 'dreadnought',
                                       resource_planets=('zohbat',),
                                       influence_planets=('zohbat',))
        self.assertEqual(2, player.trade_goods)
        self.assertFalse(any(card.exhausted for card in player.planets))
        self.assertFalse(tile.units)

        engine.produce_freelancers(player, movement, 'dreadnought',
                                   resource_planets=('zohbat',), trade_goods=1)
        self.assertEqual(1, player.trade_goods)
        self.assertEqual(1, sum(unit.kind == 'dreadnought' for unit in tile.units))

    def test_freelancers_can_decline_after_choosing_production(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        self.assertEqual({'type': 'production_declined'},
                         engine.produce_freelancers(player, movement, None))
        self.assertIsNone(engine.freelancer_offer)
        self.assertFalse(tile.units)

    def test_freelancers_hero_planet_bonus_is_counted_once(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        player.hero_attachments.add('mellon')
        movement.exploration = engine
        with self.assertRaises(ValueError):
            engine.produce_freelancers(player, movement, 'flagship',
                                       influence_planets=('mellon',))
        self.assertEqual(5, movement.planet_influence(player.planets[0].planet))
        self.assertFalse(any(card.exhausted for card in player.planets))
        engine.produce_freelancers(player, movement, 'flagship',
                                   influence_planets=('mellon',),
                                   resource_planets=('zohbat',))
        self.assertEqual(1, sum(unit.kind == 'flagship' for unit in tile.units))

    def test_freelancers_does_not_use_production_technology_discounts(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        player.technologies = player.technologies | {'aida', 'ff2', 'st'}
        player.trade_goods = 2
        self.assertEqual(2, engine.freelancers_cost(player, movement, 'cruiser'))
        event = engine.produce_freelancers(player, movement, 'cruiser',
                                           trade_goods=2)
        self.assertEqual(2, event['cost'])
        self.assertNotIn('aida', player.exhausted_technologies)
        self.assertEqual(0, player.trade_goods)

    def test_freelancers_capacity_check_resumes_without_stalling(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        movement.exploration = engine
        snapshot = Snapshot.capture(board, player, [player], exploration=engine)
        event = engine.produce_freelancers(player, movement, 'fighter',
                                           influence_planets=('mellon',))
        movement.check_freelancers_limits(player, tile.position, snapshot)
        self.assertEqual('capacity_overflow', movement.session.stage)
        movement.toggle_capacity_overflow_unit(event['unit_id'])
        movement.resolve_capacity_overflow()
        self.assertIsNone(movement.session)
        self.assertFalse(tile.units)
        self.assertEqual(1, len(movement.history))
        self.assertTrue(movement.undo())
        self.assertIsNotNone(engine.freelancer_offer)
        self.assertFalse(next(card for card in player.planets
                              if card.planet.planet_id == 'mellon').exhausted)

    def test_freelancers_restores_existing_action_stage(self):
        board, tile, player, engine, movement = self._freelancers_offer()
        movement.exploration = engine
        snapshot = Snapshot.capture(board, player, [player], exploration=engine)
        session = Session(player, tile, {}, snapshot)
        session.stage = 'production'
        movement.session = session
        engine.produce_freelancers(player, movement, 'cruiser',
                                   influence_planets=('mellon',))
        movement.check_freelancers_limits(player, tile.position, snapshot)
        self.assertIs(movement.session, session)
        self.assertEqual('production', session.stage)
        self.assertIsNone(session.freelancer_resume_stage)

    def test_saar_freelancers_can_place_infantry_with_floating_factory(self):
        tile = TILES.create(30, (0, 0))
        board = Board([tile])
        saar = PlayerState('saar', 'The Clan of Saar', 'ylw', 3,
                           [PlanetCard(planet) for planet in tile.planets])
        for planet in tile.planets:
            tile.planet_owners[planet.planet_id] = saar.faction
        dock = Unit('floating-factory', 'spacedock', saar.faction,
                    saar.color_code, UnitLocation(Region.SPACE))
        tile.units.append(dock)
        engine = ExplorationController(board, [saar], decks={
            'cultural': ['frln1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        movement = MovementController(board, [saar])
        engine.explore_planet(saar, tile, 'mellon')
        engine.resolve_pending('produce_one_unit')
        engine.produce_freelancers(saar, movement, 'infantry', planet_id=None,
                                   influence_planets=('mellon',))
        infantry = next(unit for unit in tile.units if unit.kind == 'infantry')
        self.assertEqual(Region.TRANSPORT, infantry.location.region)
        self.assertEqual(dock.unit_id, infantry.location.carrier_id)

    def test_catalog_has_distinct_pok_and_codex_cards(self):
        catalog = exploration_catalog()
        self.assertEqual(74 + 6, len(catalog))
        self.assertEqual('cultural', catalog['ds'].trait)
        self.assertEqual('frontier', catalog['ent'].trait)

    def test_attachment_changes_values_and_stays_out_of_discard(self):
        board, tile, player = make_game()
        planet = tile.planets[0]
        engine = ExplorationController(board, [player], decks={
            'cultural': ['ds'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        result = engine.explore_planet(player, tile, planet.planet_id)
        self.assertEqual('ds', result.card.alias)
        self.assertEqual(['ds'], engine.attachments[planet.planet_id])
        self.assertEqual((planet.resources + 2, planet.influence + 1),
                         engine.planet_values(planet))
        self.assertEqual([], engine.discards['cultural'])

    def test_research_facility_uses_existing_specialty_for_resource_bonus(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if any(planet.planet_type == 'INDUSTRIAL' and
                             not planet.tech_specialties for planet in definition.planets))
        board, tile, player = make_game(number)
        planet = next(planet for planet in tile.planets
                      if planet.planet_type == 'INDUSTRIAL' and not planet.tech_specialties)
        engine = ExplorationController(board, [player], relic_deck=[])
        engine.attachments[planet.planet_id] = ['biotic', 'cybernetic']
        self.assertEqual(('BIOTIC',), engine.planet_specialties(planet))
        self.assertEqual((planet.resources + 1, planet.influence + 1),
                         engine.planet_values(planet))

    def test_choice_is_not_spent_until_valid_resolution(self):
        board, tile, player = make_game()
        planet = tile.planets[0]
        engine = ExplorationController(board, [player], decks={
            'cultural': ['mo1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        result = engine.explore_planet(player, tile, planet.planet_id)
        self.assertFalse(result.resolved)
        self.assertIn('place_infantry', result.choices)
        with self.assertRaises(ValueError):
            engine.resolve_pending('invalid')
        self.assertEqual([], engine.discards['cultural'])
        result = engine.resolve_pending('place_infantry')
        self.assertTrue(result.resolved)
        self.assertEqual(1, len(tile.units))
        self.assertEqual(Region.PLANET, tile.units[0].location.region)
        self.assertEqual(['mo1'], engine.discards['cultural'])

    def test_industrial_choice_only_offers_payments_that_exist(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if any(planet.planet_type == 'INDUSTRIAL'
                             for planet in definition.planets))
        board, tile, player = make_game(number)
        planet = next(planet for planet in tile.planets
                      if planet.planet_type == 'INDUSTRIAL')
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': ['fb1'], 'frontier': []},
            relic_deck=[])
        result = engine.explore_planet(player, tile, planet.planet_id)
        self.assertEqual(('gain_commodity',), result.choices)
        engine.resolve_pending('gain_commodity')
        self.assertEqual(1, player.commodities)

    def test_reshuffles_only_discarded_cards(self):
        board, tile, player = make_game()
        engine = ExplorationController(board, [player], rng=random.Random(2), decks={
            'cultural': ['mo1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        planet = tile.planets[0]
        engine.explore_planet(player, tile, planet.planet_id, auto_resolve=True)
        self.assertEqual(['mo1'], engine.discards['cultural'])
        engine.explore_planet(player, tile, planet.planet_id, auto_resolve=True)
        self.assertEqual(['mo1'], engine.discards['cultural'])
        self.assertEqual(2, len(tile.units))

    def test_distant_suns_chooses_one_of_two_drawn_cards(self):
        board, tile, player = make_game()
        planet = tile.planets[0]
        engine = ExplorationController(board, [player], decks={
            'cultural': ['mo1', 'crf1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        offered = engine.explore_planet(player, tile, planet.planet_id, draw_count=2)
        self.assertEqual({'mo1', 'crf1'}, {card.alias for card in offered.offered_cards})
        selected = engine.resolve_pending('crf1')
        self.assertTrue(selected.resolved)
        self.assertEqual(['crf1'], engine.fragments['sol'])
        self.assertEqual(['mo1'], engine.discards['cultural'])

    def test_distant_suns_selected_card_can_still_require_choice(self):
        board, tile, player = make_game()
        planet = tile.planets[0]
        engine = ExplorationController(board, [player], decks={
            'cultural': ['crf1', 'mo1'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        engine.explore_planet(player, tile, planet.planet_id, draw_count=2)
        selected = engine.resolve_pending('mo1')
        self.assertFalse(selected.resolved)
        self.assertEqual(('place_infantry', 'decline'), selected.choices)
        engine.resolve_pending('place_infantry')
        self.assertEqual(1, len(tile.units))

    def test_hazardous_choice_removes_one_infantry_then_readies_planet(self):
        tile = next(TILES.create(number, (0, 0)) for number, definition in TILES.by_number.items()
                    if any(planet.planet_type == 'HAZARDOUS' for planet in definition.planets))
        board = Board([tile])
        planet = next(planet for planet in tile.planets if planet.planet_type == 'HAZARDOUS')
        player = PlayerState('sol', 'Sol', 'blu', 4, [PlanetCard(planet, exhausted=True)])
        tile.planet_owners[planet.planet_id] = 'sol'
        tile.units.append(Unit('test-infantry', 'infantry', 'sol', 'blu',
                               UnitLocation(Region.PLANET, planet.planet_id)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': ['exp1'], 'industrial': [], 'frontier': []},
            relic_deck=[])
        outcome = engine.explore_planet(player, tile, planet.planet_id)
        self.assertEqual(('remove_infantry', 'decline'), outcome.choices)
        engine.resolve_pending('remove_infantry')
        self.assertFalse(player.planets[0].exhausted)
        self.assertFalse(tile.units)

    def test_declining_hazardous_offer_keeps_infantry_and_discards_card(self):
        tile = next(TILES.create(number, (0, 0)) for number, definition in TILES.by_number.items()
                    if any(planet.planet_type == 'HAZARDOUS' for planet in definition.planets))
        board = Board([tile])
        planet = next(planet for planet in tile.planets if planet.planet_type == 'HAZARDOUS')
        player = PlayerState('sol', 'Sol', 'blu', 4, [PlanetCard(planet, exhausted=True)])
        tile.planet_owners[planet.planet_id] = 'sol'
        infantry = Unit('test-infantry', 'infantry', 'sol', 'blu',
                        UnitLocation(Region.PLANET, planet.planet_id))
        tile.units.append(infantry)
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': ['exp1'], 'industrial': [], 'frontier': []},
            relic_deck=[])
        outcome = engine.explore_planet(player, tile, planet.planet_id)

        self.assertEqual(('remove_infantry', 'decline'), outcome.choices)
        declined = engine.resolve_pending('decline')

        self.assertEqual(({'type': 'declined'},), declined.events)
        self.assertEqual([infantry], tile.units)
        self.assertTrue(player.planets[0].exhausted)
        self.assertIsNone(engine.pending)
        self.assertIn('exp1', engine.discards['hazardous'])

    def test_demilitarized_zone_moves_ground_forces_to_space_area(self):
        board, tile, player = make_game()
        planet = tile.planets[0]
        infantry = Unit('dmz-infantry', 'infantry', 'sol', 'blu',
                        UnitLocation(Region.PLANET, planet.planet_id))
        mech = Unit('dmz-mech', 'mech', 'sol', 'blu',
                    UnitLocation(Region.PLANET, planet.planet_id))
        structure = Unit('dmz-pds', 'pds', 'sol', 'blu',
                         UnitLocation(Region.PLANET, planet.planet_id))
        tile.units.extend((infantry, mech, structure))
        engine = ExplorationController(board, [player], decks={
            'cultural': ['dmz'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])

        outcome = engine.explore_planet(player, tile, planet.planet_id)

        self.assertTrue(outcome.resolved)
        self.assertEqual(Region.SPACE_AREA, infantry.location.region)
        self.assertEqual(Region.SPACE_AREA, mech.location.region)
        self.assertNotIn(structure, tile.units)
        placements = layout_units(tile, detailed=True)
        self.assertEqual({'dmz-infantry', 'dmz-mech'},
                         {unit.unit_id for placement in placements for unit in placement.units})
        self.assertEqual({'infantry', 'mech'},
                         set(system_inventory(tile)['fleet']))

    def test_frontier_token_is_consumed_and_card_discarded(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if not definition.planets)
        board, _, player = make_game()
        tile = TILES.create(number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('test-carrier', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': ['lc1']},
            relic_deck=[])
        result = engine.explore_frontier(player, tile)
        self.assertEqual('lc1', result.card.alias)
        self.assertEqual([], engine.space_tokens[tile.position])
        self.assertEqual(['lc1'], engine.discards['frontier'])
        with self.assertRaises(ValueError):
            engine.explore_frontier(player, tile)

    def test_codex_frontier_encounters_gain_printed_commands_and_trade_goods(self):
        empty_number = next(number for number, definition in TILES.by_number.items()
                            if not definition.planets)
        for alias, commands, goods in (
                ('minent', 1, 1), ('ent', 1, 2), ('majent', 1, 3),
                ('kel1', 2, 0), ('kel2', 2, 0)):
            with self.subTest(alias=alias):
                board, _, player = make_game()
                tile = TILES.create(empty_number, (1, 0))
                board.add(tile)
                tile.units.append(Unit('explorer', 'carrier', 'sol', 'blu',
                                       UnitLocation(Region.SPACE)))
                engine = ExplorationController(board, [player], decks={
                    'cultural': [], 'hazardous': [], 'industrial': [],
                    'frontier': [alias]}, relic_deck=[])
                outcome = engine.explore_frontier(player, tile)
                self.assertEqual(commands, player.pending_commands)
                self.assertEqual(goods, player.trade_goods)
                self.assertEqual(alias, outcome.card.alias)
                self.assertEqual(alias, engine.discards['frontier'][0])
                self.assertEqual(commands,
                                 sum(event['count'] for event in outcome.events
                                     if event['type'] == 'allocate_command_token'))

    def test_frontier_command_gain_respects_physical_token_supply(self):
        empty_number = next(number for number, definition in TILES.by_number.items()
                            if not definition.planets)
        board, _, player = make_game()
        player.command_pools.update(tactical=14, fleet=1, strategic=0)
        tile = TILES.create(empty_number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('explorer', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [],
            'frontier': ['kel1']}, relic_deck=[])
        outcome = engine.explore_frontier(player, tile)
        self.assertEqual(1, player.pending_commands)
        self.assertEqual(({'type': 'allocate_command_token', 'count': 1},),
                         outcome.events)

    def test_frontier_deepwater_draws_relic_and_applies_gain_effect(self):
        empty_number = next(number for number, definition in TILES.by_number.items()
                            if not definition.planets)
        board, _, player = make_game()
        tile = TILES.create(empty_number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('explorer', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [],
            'frontier': ['dw']}, relic_deck=['shard'])
        outcome = engine.explore_frontier(player, tile)
        self.assertEqual(['shard'], engine.relics.owned['sol'])
        self.assertEqual(1, engine.relics.victory_points['sol'])
        self.assertEqual('shard', outcome.events[0]['alias'])

    def test_frontier_deepwater_reports_empty_relic_deck(self):
        empty_number = next(number for number, definition in TILES.by_number.items()
                            if not definition.planets)
        board, _, player = make_game()
        tile = TILES.create(empty_number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('explorer', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [],
            'frontier': ['dw']}, relic_deck=[])
        outcome = engine.explore_frontier(player, tile)
        self.assertEqual(({'type': 'relic_deck_empty'},), outcome.events)
        self.assertEqual([], engine.relics.owned['sol'])

    def test_commodity_exploration_respects_dynamis_core_limit(self):
        industrial_number = next(number for number, definition in TILES.by_number.items()
                                 if any(planet.planet_type == 'INDUSTRIAL'
                                        for planet in definition.planets))
        board, tile, player = make_game(industrial_number)
        planet = next(planet for planet in tile.planets
                      if planet.planet_type == 'INDUSTRIAL')
        player.commodities = player.commodity_limit
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': ['aw1'],
            'frontier': []}, relic_deck=['dynamiscore'])
        engine.relics.draw('sol')
        engine.explore_planet(player, tile, planet.planet_id)
        engine.resolve_pending('gain_commodities')
        self.assertEqual(player.commodity_limit + 2, player.commodities)

    def test_mirage_adds_controlled_planet_and_purges_card(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if not definition.planets)
        board, _, player = make_game()
        tile = TILES.create(number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('test-carrier', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': ['mirage']},
            relic_deck=[])
        engine.explore_frontier(player, tile)
        self.assertEqual('sol', tile.planet_owners['mirage'])
        self.assertIn('mirage', [card.planet.planet_id for card in player.planets])
        self.assertIn('mirage', engine.purged)

    def test_enigmatic_device_stays_in_play_until_purged(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if not definition.planets)
        board, _, player = make_game()
        tile = TILES.create(number, (1, 0))
        board.add(tile)
        tile.units.append(Unit('test-carrier', 'carrier', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        engine = ExplorationController(board, [player], decks={
            'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': ['ed1']},
            relic_deck=[])
        engine.explore_frontier(player, tile)
        self.assertEqual(['ed1'], engine.kept_cards['sol'])
        self.assertEqual([], engine.discards['frontier'])

    def test_enigmatic_device_requires_payment_before_research(self):
        board, _, player = make_game()
        engine = ExplorationController(board, [player], relic_deck=[])
        engine.kept_cards['sol'].append('ed1')
        with self.assertRaisesRegex(ValueError, '6 resources'):
            engine.use_enigmatic_device(player, 'ed1', 'amd', (), 0)
        self.assertIn('ed1', engine.kept_cards['sol'])
        player.trade_goods = 6
        engine.use_enigmatic_device(player, 'ed1', 'amd', (), 6)
        self.assertIn('amd', player.technologies)
        self.assertIn('ed1', engine.purged)
        self.assertEqual(0, player.trade_goods)

    def test_nano_forge_attaches_and_adds_legendary_values(self):
        board, tile, player = make_game()
        engine = ExplorationController(board, [player], relic_deck=['nanoforge'])
        engine.relics.draw('sol')
        planet = tile.planets[0]
        engine.use_nano_forge(player, planet.planet_id)
        self.assertEqual((planet.resources + 2, planet.influence + 2),
                         engine.planet_values(planet))
        self.assertTrue(engine.planet_is_legendary(planet))
        self.assertEqual('nanoforge', engine.attached_relics[planet.planet_id])

    def test_stellar_converter_requires_adjacent_bombardment_and_destroys_planet(self):
        board, target, player = make_game()
        target.planet_owners.clear()
        player.planets.clear()
        adjacent = TILES.create(30, (1, 0))
        board.add(adjacent)
        engine = ExplorationController(board, [player], relic_deck=['stellarconverter'])
        engine.relics.draw('sol')
        planet = target.planets[0]
        target.units.append(Unit('target-infantry', 'infantry', 'hacan', 'ylw',
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        with self.assertRaisesRegex(ValueError, 'Bombardment'):
            engine.use_stellar_converter(player, planet.planet_id)
        adjacent.units.append(Unit('test-dreadnought', 'dreadnought', 'sol', 'blu',
                                   UnitLocation(Region.SPACE)))
        engine.use_stellar_converter(player, planet.planet_id)
        self.assertEqual([], target.units)
        self.assertIn(planet.planet_id, engine.destroyed_planets)
        self.assertIn('stellarconverter', engine.relics.purged)

    def test_three_matching_fragments_gain_relic_and_are_purged(self):
        board, tile, player = make_game()
        engine = ExplorationController(board, [player], decks={
            'cultural': ['crf1', 'crf2'], 'hazardous': [], 'industrial': [],
            'frontier': ['urf1']}, relic_deck=['emelpar'])
        engine.fragments['sol'] = ['crf1', 'crf2', 'urf1']
        outcome = engine.purge_fragments_for_relic('sol', ('crf1', 'crf2', 'urf1'))
        self.assertEqual('emelpar', outcome.alias)
        self.assertEqual([], engine.fragments['sol'])
        self.assertEqual({'crf1', 'crf2', 'urf1'}, engine.purged)
        self.assertEqual(['emelpar'], engine.relics.owned['sol'])

    def test_mixed_nonwild_fragments_cannot_gain_relic(self):
        board, _, player = make_game()
        engine = ExplorationController(board, [player], relic_deck=['emelpar'])
        engine.fragments['sol'] = ['crf1', 'hrf1', 'urf1']
        with self.assertRaises(ValueError):
            engine.purge_fragments_for_relic('sol', ('crf1', 'hrf1', 'urf1'))
        self.assertEqual(['crf1', 'hrf1', 'urf1'], engine.fragments['sol'])

    def test_fragments_can_be_transferred_but_relics_cannot(self):
        board, _, sol = make_game()
        hacan = PlayerState('hacan', 'Hacan', 'ylw', 6)
        engine = ExplorationController(board, [sol, hacan], relic_deck=['emelpar'])
        engine.fragments['sol'].append('crf1')
        engine.transfer_fragment('sol', 'hacan', 'crf1')
        self.assertEqual(['crf1'], engine.fragments['hacan'])
        engine.relics.draw('sol')
        self.assertEqual(['emelpar'], engine.relics.owned['sol'])
        self.assertEqual([], engine.relics.owned['hacan'])

    @unittest.skipIf(ExplorationPanel is None, 'Arcade is unavailable')
    def test_panel_offers_legal_fragment_combinations(self):
        board, _, sol = make_game()
        engine = ExplorationController(board, [sol], relic_deck=[])
        engine.fragments['sol'] = ['crf1', 'crf2', 'urf1', 'hrf1']
        choices = ExplorationPanel.available_fragment_sets(engine, 'sol')
        self.assertEqual([('cultural', ('crf1', 'crf2', 'urf1'), 3)], choices)

    def test_titan_prototype_pay_or_decline(self):
        board, tile, sol = make_game()
        hacan = PlayerState('hacan', 'Hacan', 'ylw', 6)
        engine = ExplorationController(board, [sol, hacan], relic_deck=['titanprototype'])
        engine.relics.draw('sol')
        engine.use_titan_prototype(sol, hacan)
        self.assertEqual(1, hacan.trade_goods)
        self.assertFalse(engine.relics.is_ready('sol', 'titanprototype'))
        engine.relics.ready_all()
        sol.trade_goods = 3
        planet = tile.planets[0]
        engine.use_titan_prototype(sol, sol, planet_id=planet.planet_id,
                                   kind='pds', trade_goods=3)
        self.assertEqual(0, sol.trade_goods)
        self.assertIn('pds', [unit.kind for unit in tile.units])


class RelicTests(unittest.TestCase):
    def test_catalog_contains_pok_and_codex_two(self):
        catalog = relic_catalog()
        self.assertEqual(13, len(catalog))
        self.assertIn('dynamiscore', catalog)
        self.assertIn('dominusorb', catalog)

    def test_shard_transfers_its_point_with_owner(self):
        _, _, sol = make_game()
        hacan = PlayerState('hacan', 'Hacan', 'ylw', 6)
        relics = RelicController([sol, hacan], deck=['shard'])
        relics.draw('sol')
        self.assertEqual(1, relics.victory_points['sol'])
        self.assertTrue(relics.transfer_shard('sol', 'hacan'))
        self.assertEqual(0, relics.victory_points['sol'])
        self.assertEqual(1, relics.victory_points['hacan'])

    def test_dynamis_core_action_uses_increased_commodity_value(self):
        _, _, sol = make_game()
        relics = RelicController([sol], deck=['dynamiscore'])
        relics.draw('sol')
        relics.use_dynamis_core(sol)
        self.assertEqual(sol.commodity_limit + 2, sol.trade_goods)
        self.assertIn('dynamiscore', relics.purged)

    def test_snapshot_restores_deck_and_ownership(self):
        board, tile, sol = make_game()
        engine = ExplorationController(board, [sol], decks={
            'cultural': ['ds'], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=['shard'])
        saved = engine.snapshot()
        engine.explore_planet(sol, tile, tile.planets[0].planet_id)
        engine.relics.draw('sol')
        engine.restore(saved)
        self.assertFalse(engine.attachments)
        self.assertEqual(['ds'], engine.decks['cultural'])
        self.assertEqual(['shard'], engine.relics.deck)

    def test_prophets_tears_invalid_draw_keeps_relic_ready(self):
        _, _, sol = make_game()
        relics = RelicController([sol], deck=['prophetstears'])
        relics.draw('sol')
        with self.assertRaises(ValueError):
            relics.use_prophets_tears(sol, 'draw_action_card')
        self.assertTrue(relics.is_ready('sol', 'prophetstears'))

    def test_codex_recovers_up_to_three_cards_from_discard(self):
        _, _, sol = make_game()
        relics = RelicController([sol], deck=['codex'])
        relics.draw('sol')
        class Cards:
            discard = ['fs1', 'mb1', 'fs2']
        cards = Cards()
        relics.use_codex(sol, cards, ('fs1', 'mb1'))
        self.assertEqual(['fs2'], cards.discard)
        self.assertEqual(['fs1', 'mb1'], sol.action_cards)
        self.assertIn('codex', relics.purged)


if __name__ == '__main__':
    unittest.main()
