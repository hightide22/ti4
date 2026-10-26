import random
import unittest
from dataclasses import replace
from itertools import combinations
from unittest.mock import patch

from action_cards import ActionCardController
from board import ROOT, load_board
from exploration import ExplorationController
from movement import MovementController, Session, Snapshot
from player import PlanetCard, create_players
from turn_order import TurnOrder
from technology import available_technologies
from units import Region, Unit, UnitLocation, unit_profile


class RemainingFactionTests(unittest.TestCase):
    def game(self, factions):
        config, board = load_board(ROOT / 'maps/four_player.json', factions)
        players = create_players(board, config)
        return board, players, {player.faction: player for player in players}

    def test_winnu_blood_ties_removes_custodians_without_payment(self):
        board, players, by_faction = self.game(('winnu', 'sol', 'jolnar', 'hacan'))
        winnu = by_faction['winnu']
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        self.assertTrue(mecatol.custodians_present)
        session = Session(winnu, mecatol, {}, Snapshot.capture(board, winnu, players))
        movement = MovementController(board, players)

        self.assertTrue(movement.capture_planet(session, 'mr'))

        self.assertFalse(mecatol.custodians_present)
        self.assertEqual(mecatol.planet_owners['mr'], 'winnu')
        self.assertTrue(any('Blood Ties' in line for line in session.custodian_payment_log))
        movement.finish_invasion(session)
        self.assertTrue(any(unit.owner == 'winnu' and unit.kind == 'pds' and
                            unit.location.planet_id == 'mr' for unit in mecatol.units))
        self.assertTrue(any(unit.owner == 'winnu' and unit.kind == 'spacedock' and
                            unit.location.planet_id == 'mr' for unit in mecatol.units))

    def test_other_faction_cannot_take_mecatol_without_six_ready_influence(self):
        board, players, by_faction = self.game(('sol', 'jolnar', 'hacan', 'letnev'))
        sol = by_faction['sol']
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        session = Session(sol, mecatol, {}, Snapshot.capture(board, sol, players))
        movement = MovementController(board, players)

        self.assertFalse(movement.capture_planet(session, 'mr'))

        self.assertTrue(mecatol.custodians_present)
        self.assertNotIn('mr', mecatol.planet_owners)
        self.assertTrue(session.custodian_payment_failed)

    def test_custodians_cost_exhausts_controlled_planets_and_undo_restores_token(self):
        board, players, by_faction = self.game(('sol', 'jolnar', 'hacan', 'letnev'))
        sol = by_faction['sol']
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        existing = {card.planet.planet_id for card in sol.planets}
        candidates = [(tile, planet) for tile in board.values() if tile is not mecatol
                      for planet in tile.planets if planet.planet_id not in existing and
                      planet.planet_id not in tile.planet_owners]
        chosen = next(choice for size in range(1, len(candidates) + 1)
                      for choice in combinations(candidates, size)
                      if sum(planet.influence for _, planet in choice) >= 6)
        for tile, planet in chosen:
            tile.planet_owners[planet.planet_id] = 'sol'
            sol.planets.append(PlanetCard(planet))
        snapshot = Snapshot.capture(board, sol, players)
        session = Session(sol, mecatol, {}, snapshot)
        movement = MovementController(board, players)

        self.assertTrue(movement.capture_planet(session, 'mr'))
        paid = [card for card in sol.planets if card.exhausted and
                card.planet.planet_id != 'mr']
        self.assertGreaterEqual(sum(card.planet.influence for card in paid), 6)
        self.assertFalse(mecatol.custodians_present)

        snapshot.restore()
        self.assertTrue(mecatol.custodians_present)
        self.assertNotIn('mr', mecatol.planet_owners)
        self.assertTrue(all(not card.exhausted for card in sol.planets))

    def test_naalu_foresight_moves_ships_and_cargo_and_spends_strategy_token(self):
        board, players, by_faction = self.game(('naalu', 'sol', 'jolnar', 'hacan'))
        naalu, sol = by_faction['naalu'], by_faction['sol']
        target = next(tile for tile in board.values()
                      if any(board.neighbors(tile.position)))
        carrier = Unit('foresight-carrier', 'carrier', 'naalu', naalu.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('foresight-infantry', 'infantry', 'naalu', naalu.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        target.units.extend((carrier, infantry))
        movement = MovementController(board, players)
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players))
        movement.session = session
        options = movement.foresight_options(session)
        self.assertTrue(options)
        destination = options[0]
        session.stage = 'foresight'
        session.foresight_affected = [(target.position, 'sol')]
        session.foresight_next_stage = 'complete'
        before = naalu.command_pools['strategic']

        self.assertTrue(movement.resolve_foresight(destination.position))

        self.assertNotIn(carrier, target.units)
        self.assertNotIn(infantry, target.units)
        self.assertIn(carrier, destination.units)
        self.assertIn(infantry, destination.units)
        self.assertIn('naalu', destination.command_tokens)
        self.assertEqual(naalu.command_pools['strategic'], before - 1)

    def test_nekro_cannot_research_but_copies_opponent_technology_once_per_combat(self):
        board, players, by_faction = self.game(('nekro', 'sol', 'jolnar', 'hacan'))
        nekro, sol = by_faction['nekro'], by_faction['sol']
        sol.technologies = sol.technologies | {'ac2', 'gd'}
        self.assertEqual(available_technologies(nekro), [])
        starting_technologies = set(nekro.technologies)
        target = next(tile for tile in board.values() if tile.number != 18)
        victim = Unit('nekro-tech-victim', 'carrier', 'sol', sol.color_code,
                      UnitLocation(Region.SPACE))
        target.units.append(victim)
        session = Session(nekro, target, {}, Snapshot.capture(board, nekro, players),
                          combat_type='space', combat_factions=('nekro', 'sol'),
                          stage='space_combat')
        movement = MovementController(board, players)

        movement.destroy_unit(target, victim, session)

        self.assertIn('ac2', nekro.technologies)
        self.assertIn('nekro', session.nekro_tech_gained)
        copied_technologies = set(nekro.technologies)
        self.assertEqual(len(copied_technologies), len(starting_technologies) + 1)
        second = Unit('nekro-tech-victim-2', 'cruiser', 'sol', sol.color_code,
                      UnitLocation(Region.SPACE))
        target.units.append(second)
        movement.destroy_unit(target, second, session)
        self.assertEqual(set(nekro.technologies), copied_technologies)

    def test_nekro_can_gain_once_in_space_combat_and_again_in_each_ground_combat(self):
        board, players, by_faction = self.game(('nekro', 'sol', 'jolnar', 'hacan'))
        nekro, sol = by_faction['nekro'], by_faction['sol']
        sol.technologies = sol.technologies | {'ac2', 'gd', 'st'}
        starting_technologies = len(nekro.technologies)
        target = next(tile for tile in board.values() if tile.planets and tile.number != 18)
        planet = target.planets[0]
        target.planet_owners[planet.planet_id] = 'sol'
        session = Session(nekro, target, {}, Snapshot.capture(board, nekro, players),
                          combat_type='space', combat_factions=('nekro', 'sol'),
                          stage='space_combat')
        movement = MovementController(board, players)

        first_victim = Unit('nekro-space-tech-victim', 'carrier', 'sol', sol.color_code,
                            UnitLocation(Region.SPACE))
        target.units.append(first_victim)
        movement.destroy_unit(target, first_victim, session)
        self.assertIn('nekro', session.nekro_tech_gained)
        self.assertEqual(len(nekro.technologies), starting_technologies + 1)

        target.units.append(Unit('sol-ground-combatant', 'infantry', 'sol', sol.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        movement.start_ground_combat(session, planet.planet_id)
        self.assertNotIn('nekro', session.nekro_tech_gained)
        second_victim = next(unit for unit in target.units
                             if unit.owner == 'sol' and unit.kind == 'infantry')
        movement.destroy_unit(target, second_victim, session)
        self.assertEqual(len(nekro.technologies), starting_technologies + 2)

    def test_mentak_can_pillage_a_neighbor_commodity_after_transaction(self):
        board, players, by_faction = self.game(('mentak', 'sol', 'jolnar', 'hacan'))
        mentak, sol = by_faction['mentak'], by_faction['sol']
        origin = next(tile for tile in board.values() if any(board.neighbors(tile.position)))
        neighbor = board.neighbors(origin.position)[0]
        origin.units.append(Unit('mentak-presence', 'cruiser', 'mentak', mentak.color_code,
                                 UnitLocation(Region.SPACE)))
        planet = next((planet for planet in neighbor.planets), None)
        if planet:
            neighbor.planet_owners[planet.planet_id] = 'sol'
        else:
            neighbor.units.append(Unit('sol-presence', 'cruiser', 'sol', sol.color_code,
                                       UnitLocation(Region.SPACE)))
        sol.trade_goods = 3
        sol.commodities = 1
        before = mentak.commodities
        movement = MovementController(board, players)

        self.assertEqual(movement.resolve_pillage(sol, currency='commodities'), 1)

        self.assertEqual(sol.commodities, 0)
        self.assertEqual(mentak.commodities, before + 1)
        self.assertEqual(sol.trade_goods, 3)

    def test_naalu_telepathic_sets_zero_initiative_and_sardakk_gets_combat_bonus(self):
        _, players, _ = self.game(('naalu', 'sol', 'jolnar', 'hacan'))
        turn = TurnOrder(players)
        turn.strategy_assignments.update({'naalu': [8], 'sol': [1], 'jolnar': [2], 'hacan': [3]})
        self.assertEqual(turn._initiative_key(0)[0], 0)
        self.assertEqual(MovementController.combat_threshold('sardakk', {'combatHitsOn': 8}), 7)
        self.assertEqual(MovementController.combat_threshold('sol', {'combatHitsOn': 8}), 8)

    def test_xxcha_peace_accords_claims_an_empty_adjacent_planet(self):
        board, players, by_faction = self.game(('xxcha', 'sol', 'jolnar', 'hacan'))
        xxcha = by_faction['xxcha']
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == 'xxcha' for planet in tile.planets))
        adjacent_planets = [(tile, planet) for tile in board.neighbors(home.position)
                            if tile.number != 18 for planet in tile.planets]
        target_tile, target_planet = adjacent_planets[0]
        for tile, planet in adjacent_planets[1:]:
            tile.units.append(Unit(f'peace-accords-occupant-{planet.planet_id}', 'infantry', 'sol',
                                   by_faction['sol'].color_code,
                                   UnitLocation(Region.PLANET, planet.planet_id)))
        target_tile.planet_owners.pop(target_planet.planet_id, None)
        movement = MovementController(board, players)
        self.assertEqual(target_planet.planet_type, 'CULTURAL')
        movement.exploration = ExplorationController(board, players, decks={
            'cultural': ['crf1'], 'hazardous': [], 'industrial': [], 'frontier': []})

        claimed = movement.peace_accords(xxcha)

        self.assertEqual(claimed.planet_id, target_planet.planet_id)
        self.assertEqual(target_tile.planet_owners[target_planet.planet_id], 'xxcha')
        card = next(card for card in xxcha.planets
                    if card.planet.planet_id == target_planet.planet_id)
        self.assertTrue(card.exhausted)
        self.assertIn('crf1', movement.exploration.fragments['xxcha'])

    def test_xxcha_peace_accords_reaches_matching_wormhole_system(self):
        board, players, by_faction = self.game(('xxcha', 'sol', 'jolnar', 'hacan'))
        xxcha = by_faction['xxcha']
        for tile in board.values():
            tile.planet_owners.clear()
            tile.units[:] = [unit for unit in tile.units if unit.owner != 'xxcha']

        pairs = [(left, right) for left in board.values() if left.planets and left.wormholes
                 for right in board.values() if right is not left and
                 set(left.wormholes) & set(right.wormholes) and
                 right not in board.neighbors(left.position)]
        self.assertTrue(pairs, 'map must have matching non-adjacent wormhole systems')
        source, target = pairs[0]
        source_planet = source.planets[0]
        target_planet = replace(source_planet,
                                planet_id='peace-accords-wormhole-target',
                                name='Peace Accords Test World',
                                resources=99, influence=99)
        target.definition = replace(target.definition,
                                    planets=(target_planet, *target.planets))
        source.planet_owners[source_planet.planet_id] = 'xxcha'

        movement = MovementController(board, players)
        claimed = movement.peace_accords(xxcha)

        self.assertEqual(target_planet.planet_id, claimed.planet_id)
        self.assertEqual('xxcha', target.planet_owners[target_planet.planet_id])

    def test_xxcha_peace_accords_cannot_claim_destroyed_planet(self):
        board, players, by_faction = self.game(('xxcha', 'sol', 'jolnar', 'hacan'))
        xxcha = by_faction['xxcha']
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == 'xxcha' for planet in tile.planets))
        adjacent_planets = [(tile, planet) for tile in board.neighbors(home.position)
                            if tile.number != 18 for planet in tile.planets]
        target_tile, target_planet = adjacent_planets[0]
        for tile, planet in adjacent_planets[1:]:
            tile.units.append(Unit(f'peace-accords-occupant-{planet.planet_id}',
                                   'infantry', 'sol', by_faction['sol'].color_code,
                                   UnitLocation(Region.PLANET, planet.planet_id)))
        movement = MovementController(board, players)
        movement.exploration = ExplorationController(board, players)
        movement.exploration.destroyed_planets.add(target_planet.planet_id)

        self.assertIsNone(movement.peace_accords(xxcha))
        self.assertNotIn(target_planet.planet_id, target_tile.planet_owners)

    def test_mentak_ambush_rolls_at_combat_start(self):
        board, players, by_faction = self.game(('mentak', 'sol', 'jolnar', 'hacan'))
        mentak, sol = by_faction['mentak'], by_faction['sol']
        target = next(tile for tile in board.values() if not tile.units and tile.number != 18)
        cruiser = Unit('mentak-ambush-cruiser', 'cruiser', 'mentak', mentak.color_code,
                       UnitLocation(Region.SPACE))
        enemy = Unit('mentak-ambush-enemy', 'carrier', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.extend((cruiser, enemy))
        session = Session(mentak, target, {}, Snapshot.capture(board, mentak, players))
        movement = MovementController(board, players)
        movement.session = session

        with patch('movement.random.randint', return_value=10):
            movement.start_combat(session)

        self.assertEqual(session.mentak_ambush_rolls[0]['threshold'],
                         MovementController.combat_threshold('mentak', unit_profile(cruiser)))
        self.assertTrue(session.mentak_ambush_rolls[0]['hit'])
        self.assertEqual(session.stage, 'space_cannon_assign')

    def test_yin_devotion_sacrifices_a_ship_for_a_hit_after_a_round(self):
        board, players, by_faction = self.game(('yin', 'sol', 'jolnar', 'hacan'))
        yin, sol = by_faction['yin'], by_faction['sol']
        target = next(tile for tile in board.values() if not tile.units and tile.number != 18)
        cruiser = Unit('yin-devotion-cruiser', 'cruiser', 'yin', yin.color_code,
                       UnitLocation(Region.SPACE))
        escort = Unit('yin-devotion-escort', 'carrier', 'yin', yin.color_code,
                      UnitLocation(Region.SPACE))
        victim = Unit('yin-devotion-victim', 'carrier', 'sol', sol.color_code,
                      UnitLocation(Region.SPACE))
        target.units.extend((cruiser, escort, victim))
        session = Session(yin, target, {}, Snapshot.capture(board, yin, players),
                          combat_type='space', combat_factions=('yin', 'sol'),
                          combat_round=1, stage='combat_end')
        movement = MovementController(board, players)

        movement._finish_combat_round(session)

        self.assertNotIn(cruiser, target.units)
        self.assertNotIn(victim, target.units)
        self.assertTrue(any('Yin Devotion' in line for line in session.assault_log))

    def test_yssaril_scheming_draws_an_extra_card_without_hand_cap(self):
        board, players, by_faction = self.game(('yssaril', 'sol', 'jolnar', 'hacan'))
        yssaril = by_faction['yssaril']
        yssaril.action_cards[:] = [f'held-{index}' for index in range(7)]
        movement = MovementController(board, players)
        deck = ActionCardController([yssaril], movement, deck=['base', 'extra', 'unused'],
                                    rng=random.Random(14))

        drawn = deck.draw(yssaril, 1)

        self.assertEqual(len(drawn), 2)
        self.assertEqual(len(yssaril.action_cards), 8)

    def test_yin_indoctrination_can_spend_influence_from_another_system(self):
        board, players, by_faction = self.game(('yin', 'sol', 'jolnar', 'hacan'))
        yin, sol = by_faction['yin'], by_faction['sol']
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == 'sol' for planet in tile.planets))
        planet = home.planets[0]
        home.units.extend((
            Unit('indoctrination-sol', 'infantry', 'sol', sol.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
            Unit('indoctrination-yin', 'infantry', 'yin', yin.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
        ))
        movement = MovementController(board, players)
        session = Session(sol, home, {}, Snapshot.capture(board, sol, players))
        movement.session = session
        before = sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                     unit.location.planet_id == planet.planet_id for unit in home.units)

        movement.start_ground_combat(session, planet.planet_id)

        after_sol = sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                        unit.location.planet_id == planet.planet_id for unit in home.units)
        after_yin = sum(unit.owner == 'yin' and unit.kind == 'infantry' and
                        unit.location.planet_id == planet.planet_id for unit in home.units)
        self.assertEqual(after_sol, before - 1)
        self.assertEqual(after_yin, 2)
        self.assertTrue(any(card.exhausted for card in yin.planets))


if __name__ == '__main__':
    unittest.main()
