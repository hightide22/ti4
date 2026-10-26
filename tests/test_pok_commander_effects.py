"""Commander passives used by tactical combat and production."""

import unittest
from unittest.mock import patch

from action_cards import ActionCardController
from board import ROOT, TILES, load_board
from exploration import ExplorationController
from leader_effects import use_nomad_hero, use_titans_hero
from movement import MovementController, Session, Snapshot
from player import PlanetCard, create_players
from units import Region, Unit, UnitLocation, unit_profile


def game(faction):
    others = tuple(alias for alias in ('sol', 'jolnar', 'hacan', 'argent') if alias != faction)
    config, board = load_board(ROOT / 'maps/four_player.json', (faction, *others[:3]))
    players = create_players(board, config)
    return board, players, MovementController(board, players)


class CommanderEffectTests(unittest.TestCase):
    def test_exploration_queue_pauses_for_freelancers_offer(self):
        board, players, movement = game('sol')
        sol = players[0]
        cultural = [(tile, planet) for tile in board.values() for planet in tile.planets
                    if planet.planet_type == 'CULTURAL' and
                    planet.faction_homeworld is None][:2]
        self.assertEqual(2, len(cultural))
        for tile, planet in cultural:
            tile.planet_owners[planet.planet_id] = 'sol'
            sol.planets.append(PlanetCard(planet, exhausted=True))
        decks = {'cultural': ['crf1', 'frln1'], 'hazardous': [],
                 'industrial': [], 'frontier': []}
        movement.exploration = ExplorationController(board, players, decks=decks,
                                                     relic_deck=[])
        movement.exploration_is_bot = lambda _: True
        movement.exploration_queue = [(sol, tile, planet.planet_id)
                                      for tile, planet in cultural]
        movement.resolve_exploration_queue()
        self.assertEqual(1, len(movement.exploration_queue))
        self.assertIsNotNone(movement.exploration.freelancer_offer)
        movement.exploration.produce_freelancers(sol, movement, None)
        movement.resolve_exploration_queue()
        self.assertEqual([], movement.exploration_queue)
        self.assertIsNone(movement.exploration.freelancer_offer)

    def test_argent_commander_adds_one_die_to_each_unit_ability_roll(self):
        board, players, movement = game('argent')
        argent, sol = players[:2]
        argent.leaders['argentcommander'].unlocked = True
        tile = next(tile for tile in board.values() if tile.planets and not tile.units)
        planet_id = tile.planets[0].planet_id
        destroyer = Unit('argent-destroyer', 'destroyer', 'argent', argent.color_code,
                         UnitLocation(Region.SPACE))
        pds = Unit('argent-pds', 'pds', 'argent', argent.color_code,
                   UnitLocation(Region.PLANET, planet_id))
        dreadnought = Unit('argent-dread', 'dreadnought', 'argent', argent.color_code,
                           UnitLocation(Region.SPACE))
        enemy_fighter = Unit('sol-fighter', 'fighter', sol.faction, sol.color_code,
                             UnitLocation(Region.SPACE))
        enemy_infantry = Unit('sol-infantry', 'infantry', sol.faction, sol.color_code,
                              UnitLocation(Region.PLANET, planet_id))
        tile.units.extend((destroyer, pds, dreadnought, enemy_fighter, enemy_infantry))
        session = Session(argent, tile, {}, Snapshot.capture(board, argent, players))
        session.combat_factions = ('argent', sol.faction)
        movement.session = session
        with patch('movement.random.randint', return_value=1):
            movement.resolve_anti_fighter_barrage(session)
        self.assertEqual(3, len(session.afb_rolls['argent']))
        self.assertEqual(1, sum(roll.get('bonus_source') == 'argentcommander'
                                for roll in session.afb_rolls['argent']))
        self.assertIn('Argent commander +1 die', session.afb_log[0])
        tile.units.remove(destroyer)
        session.space_cannon_events.clear()
        with patch('movement.random.randint', return_value=10):
            movement._roll_space_cannon(session, [(tile, pds, unit_profile(pds))],
                                        sol.faction)
        # Plasma Scoring and the commander each contribute one extra die.
        self.assertEqual(3, len(session.space_cannon_events))
        self.assertIn('Plasma Scoring +1 die', session.cannon_log[0])
        self.assertIn('Argent commander +1 die', session.cannon_log[0])
        session.space_cannon_events.clear()
        session.stage = 'bombardment'
        session.bombard_targets = {dreadnought.unit_id: planet_id}
        with patch('movement.random.randint', return_value=1):
            movement.resolve_bombardment()
        self.assertEqual(3, len(session.bombard_rolls))
        self.assertIn('Plasma Scoring +1 die', session.bombard_log[0])
        self.assertIn('Argent commander +1 die', session.bombard_log[0])

    def test_argent_commander_and_plasma_scoring_add_planetary_defense_dice(self):
        board, players, movement = game('argent')
        argent, sol = players[:2]
        argent.technologies = frozenset({'ps'})
        argent.leaders['argentcommander'].unlocked = True
        tile = next(tile for tile in board.values() if tile.planets and not tile.units)
        planet_id = tile.planets[0].planet_id
        tile.units.extend((
            Unit('argent-defense-pds', 'pds', 'argent', argent.color_code,
                 UnitLocation(Region.PLANET, planet_id)),
            Unit('sol-landing-infantry', 'infantry', 'sol', sol.color_code,
                 UnitLocation(Region.PLANET, planet_id))))
        session = Session(sol, tile, {}, Snapshot.capture(board, sol, players))
        session.landed_planets = [planet_id]
        movement.session = session
        with patch('movement.random.randint', return_value=10):
            movement.resolve_planetary_cannon_defense(session)
        self.assertIn('Plasma Scoring +1 die', session.defense_log[0])
        self.assertIn('Argent commander +1 die', session.defense_log[0])
        self.assertEqual(3, session.defense_log[0].count('HIT'))

    def test_winnu_commander_adds_two_on_mecatol_and_home_but_not_elsewhere(self):
        board, players, movement = game('winnu')
        winnu, sol = players[:2]
        winnu.leaders['winnucommander'].unlocked = True
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        home = next(tile for tile in board.values() if any(
            planet.faction_homeworld == 'winnu' for planet in tile.planets))
        other = next(tile for tile in board.values() if tile is not home and
                     tile is not mecatol and not tile.planets)
        self.assertEqual(2, movement.winnu_commander_bonus(mecatol))
        self.assertEqual(2, movement.winnu_commander_bonus(home))
        self.assertEqual(0, movement.winnu_commander_bonus(other))
        mecatol.units.extend((
            Unit('winnu-fighter', 'fighter', 'winnu', winnu.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('sol-fighter', 'fighter', sol.faction, sol.color_code,
                 UnitLocation(Region.SPACE))))
        session = Session(winnu, mecatol, {}, Snapshot.capture(board, winnu, players))
        session.stage = 'space_combat'
        session.combat_factions = ('winnu', sol.faction)
        movement.session = session
        with patch('movement.random.randint', return_value=7):
            movement.advance_combat()
        self.assertEqual(9, session.combat_rolls['winnu'][0]['value'])
        self.assertTrue(session.combat_rolls['winnu'][0]['hit'])
        self.assertFalse(session.combat_rolls[sol.faction][0]['hit'])

    def test_l1z1x_commander_bypasses_enemy_planetary_shield(self):
        board, players, movement = game('l1z1x')
        l1z1x, sol = players[:2]
        tile = next(tile for tile in board.values() if tile.planets and not tile.units)
        planet_id = tile.planets[0].planet_id
        tile.units.extend((
            Unit('l1z1x-bomber', 'dreadnought', 'l1z1x', l1z1x.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('sol-pds', 'pds', sol.faction, sol.color_code,
                 UnitLocation(Region.PLANET, planet_id)),
            Unit('sol-infantry', 'infantry', sol.faction, sol.color_code,
                 UnitLocation(Region.PLANET, planet_id))))
        session = Session(l1z1x, tile, {}, Snapshot.capture(board, l1z1x, players))
        movement.session = session
        movement.continue_invasion_start(session)
        self.assertTrue(session.bombardment_cancelled)
        l1z1x.leaders['l1z1xcommander'].unlocked = True
        movement.continue_invasion_start(session)
        self.assertEqual('bombardment', session.stage)
        self.assertFalse(session.bombardment_cancelled)

    def test_letnev_commander_gains_trade_good_for_each_sustain(self):
        board, players, movement = game('letnev')
        letnev = players[0]
        tile = next(tile for tile in board.values() if not tile.units)
        unit = Unit('letnev-dread', 'dreadnought', 'letnev', letnev.color_code,
                    UnitLocation(Region.SPACE))
        tile.units.append(unit)
        movement.apply_hit(tile, [unit])
        self.assertEqual(0, letnev.trade_goods)
        unit.damaged = False
        letnev.leaders['letnevcommander'].unlocked = True
        movement.apply_hit(tile, [unit])
        self.assertTrue(unit.damaged)
        self.assertEqual(1, letnev.trade_goods)

    def test_cabal_commander_excludes_first_two_small_units_from_limit(self):
        board, players, movement = game('cabal')
        cabal = players[0]
        tile = next(tile for tile in board.values() if not tile.units)
        session = Session(cabal, tile, {}, Snapshot.capture(board, cabal, players))
        session.stage = 'production'
        session.production_limit = 1
        movement.session = session
        cabal.leaders['cabalcommander'].unlocked = True
        movement.adjust_production('fighter', 1)
        self.assertEqual(2, session.production_choices['fighter'])
        self.assertEqual(0, movement.production_total(session))
        movement.adjust_production('destroyer', 1)
        self.assertEqual(1, movement.production_total(session))
        movement.adjust_production('infantry', 1)
        self.assertNotIn('infantry', session.production_choices)

    def test_nomad_commander_makes_one_flagship_free(self):
        board, players, movement = game('nomad')
        nomad = players[0]
        tile = next(tile for tile in board.values() if not tile.units)
        session = Session(nomad, tile, {}, Snapshot.capture(board, nomad, players))
        session.production_choices = {'flagship': 1, 'carrier': 1}
        self.assertEqual(11, movement.production_cost(session))
        nomad.leaders['nomadcommander'].unlocked = True
        self.assertEqual(3, movement.production_cost(session))

    def test_titans_commander_gains_one_trade_good_after_production(self):
        board, players, movement = game('titans')
        titans = players[0]
        titans.leaders['titanscommander'].unlocked = True
        home = next(tile for tile in board.values() if 'titans' in tile.planet_owners.values())
        dock = next(unit for unit in home.units if unit.owner == 'titans' and
                    unit.kind == 'spacedock')
        movement.start_strategy_production(titans, home, dock)
        movement.adjust_production('destroyer', 1)
        titans.trade_goods = 1
        movement.change_production_trade_goods(1)
        movement.produce()
        self.assertEqual(1, titans.trade_goods)
        self.assertTrue(any(unit.owner == 'titans' and unit.kind == 'destroyer'
                            for unit in home.units))

    def test_nomad_hero_allows_flagship_to_leave_token_system(self):
        board, players, movement = game('nomad')
        nomad = players[0]
        home = next(tile for tile in board.values() if 'nomad' in tile.planet_owners.values())
        flagship = next(unit for unit in home.units if unit.owner == 'nomad' and
                        unit.kind == 'flagship')
        neighbor = next(tile for tile in board.neighbors(home.position)
                        if not tile.anomalies)
        home.command_tokens.add('nomad')
        self.assertIsNone(movement.route(home, neighbor, flagship, nomad))
        nomad.leaders['nomadhero'].unlocked = True
        use_nomad_hero(nomad)
        self.assertIsNotNone(movement.route(home, neighbor, flagship, nomad))
        carrier = next(unit for unit in home.units if unit.owner == 'nomad' and
                       unit.kind == 'carrier')
        self.assertIsNone(movement.route(home, neighbor, carrier, nomad))

    def test_titans_hero_enhances_elysium_and_fires_three_cannon_dice(self):
        board, players, movement = game('titans')
        titans, sol = players[:2]
        titans.leaders['titanshero'].unlocked = True
        home = next(tile for tile in board.values() if 'elysium' in tile.planet_owners)
        elysium = next(planet for planet in home.planets if planet.planet_id == 'elysium')
        before_resources = movement.planet_resources(elysium)
        before_influence = movement.planet_influence(elysium)
        use_titans_hero(titans)
        self.assertEqual(before_resources + 3, movement.planet_resources(elysium))
        self.assertEqual(before_influence + 3, movement.planet_influence(elysium))
        home.units.append(Unit('sol-invader', 'cruiser', sol.faction, sol.color_code,
                               UnitLocation(Region.SPACE)))
        session = Session(sol, home, {}, Snapshot.capture(board, sol, players))
        movement.session = session
        shooters = movement._space_cannon_shooters(session)
        heroic = [(tile, unit, profile) for tile, unit, profile in shooters
                  if unit.unit_id == 'titans-elysium-hero-cannon']
        self.assertEqual(1, len(heroic))
        self.assertEqual((5, 3), (heroic[0][2]['spaceCannonHitsOn'],
                                  heroic[0][2]['spaceCannonDieCount']))
        with patch('movement.random.randint', return_value=6):
            movement._roll_space_cannon(session, heroic, None)
        self.assertEqual(3, len(session.space_cannon_events))

    def test_primor_places_infantry_on_any_controlled_planet_and_undoes(self):
        board, players, movement = game('sol')
        sol = players[0]
        source = next(tile for tile in board.values() if not tile.planets)
        source.definition = TILES[65]
        source.planet_owners['primor'] = 'sol'
        card = PlanetCard(source.planets[0])
        sol.planets.append(card)
        home = next(tile for tile in board.values()
                    if tile is not source and 'sol' in tile.planet_owners.values())
        target_id = home.planets[0].planet_id
        self.assertIn(target_id, movement.legendary_planet_options(sol)['primor']['infantry'])
        movement.use_legendary_planet(sol, 'primor', destination_planet_id=target_id,
                                      count=2)
        placed = [unit for unit in home.units if unit.unit_id.startswith('sol-primor-infantry-')]
        self.assertEqual(2, len(placed))
        self.assertFalse(card.exhausted)
        self.assertIn('primor', sol.exhausted_legendary_abilities)
        self.assertNotIn('primor', movement.legendary_planet_options(sol))
        movement.undo()
        self.assertFalse(card.exhausted)
        self.assertNotIn('primor', sol.exhausted_legendary_abilities)
        self.assertTrue(all(unit not in home.units for unit in placed))

    def test_hopes_end_places_mech_or_draws_card_with_undo(self):
        board, players, movement = game('sol')
        sol = players[0]
        source = next(tile for tile in board.values() if not tile.planets)
        source.definition = TILES[66]
        source.planet_owners['hopesend'] = 'sol'
        card = PlanetCard(source.planets[0])
        sol.planets.append(card)
        home = next(tile for tile in board.values()
                    if tile is not source and 'sol' in tile.planet_owners.values())
        target_id = home.planets[0].planet_id
        ActionCardController(players, movement, deck=['fs1'])
        options = movement.legendary_planet_options(sol)['hopesend']
        self.assertIn(target_id, options['mech'])
        self.assertTrue(options['card'])
        movement.use_legendary_planet(sol, 'hopesend', choice='mech',
                                      destination_planet_id=target_id)
        self.assertTrue(any(unit.unit_id.startswith('sol-hopesend-mech-')
                            for unit in home.units))
        self.assertFalse(card.exhausted)
        self.assertIn('hopesend', sol.exhausted_legendary_abilities)
        movement.undo()
        self.assertFalse(card.exhausted)
        self.assertNotIn('hopesend', sol.exhausted_legendary_abilities)
        self.assertFalse(any(unit.unit_id.startswith('sol-hopesend-mech-')
                             for unit in home.units))
        movement.use_legendary_planet(sol, 'hopesend', choice='card')
        self.assertEqual(['fs1'], sol.action_cards)
        self.assertFalse(card.exhausted)
        self.assertIn('hopesend', sol.exhausted_legendary_abilities)
        movement.undo()
        self.assertEqual([], sol.action_cards)
        self.assertFalse(card.exhausted)
        self.assertNotIn('hopesend', sol.exhausted_legendary_abilities)
        self.assertEqual(['fs1'], movement.action_cards.deck)

    def test_newly_captured_legendary_ability_is_ready_while_planet_card_is_exhausted(self):
        board, players, movement = game('sol')
        sol = players[0]
        source = next(tile for tile in board.values() if not tile.planets)
        source.definition = TILES[65]
        planet = source.planets[0]
        movement.exploration = ExplorationController(
            board, players,
            decks={'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        session = Session(sol, source, {}, Snapshot.capture(
            board, sol, players, exploration=movement.exploration))

        self.assertTrue(movement.capture_planet(session, 'primor'))

        planet_card = next(card for card in sol.planets if card.planet.planet_id == 'primor')
        self.assertTrue(planet_card.exhausted)
        self.assertIn('primor', movement.legendary_planet_options(sol))
        destination = next(card.planet.planet_id for card in sol.planets
                           if card.planet.planet_id != 'primor')
        movement.use_legendary_planet(sol, 'primor', destination_planet_id=destination)
        self.assertTrue(planet_card.exhausted)
        self.assertIn('primor', sol.exhausted_legendary_abilities)

    def test_exhausted_legendary_ability_transfers_with_the_planet_and_undoes(self):
        board, players, movement = game('sol')
        sol, hacan = players[:2]
        source = next(tile for tile in board.values() if not tile.planets)
        source.definition = TILES[65]
        source.planet_owners['primor'] = sol.faction
        sol.planets.append(PlanetCard(source.planets[0], exhausted=True))
        sol.exhausted_legendary_abilities.add('primor')
        movement.exploration = ExplorationController(
            board, players,
            decks={'cultural': [], 'hazardous': [], 'industrial': [], 'frontier': []},
            relic_deck=[])
        session = Session(hacan, source, {}, Snapshot.capture(
            board, hacan, players, exploration=movement.exploration))

        self.assertTrue(movement.capture_planet(session, 'primor'))

        self.assertNotIn('primor', sol.exhausted_legendary_abilities)
        self.assertIn('primor', hacan.exhausted_legendary_abilities)
        session.snapshot.restore()
        self.assertIn('primor', sol.exhausted_legendary_abilities)
        self.assertNotIn('primor', hacan.exhausted_legendary_abilities)
        self.assertEqual(sol.faction, source.planet_owners['primor'])


if __name__ == '__main__':
    unittest.main()
