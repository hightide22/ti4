"""PoK and Codex card coverage at their actual game timing windows."""
import random
import unittest

from action_card_deck import ActionCardDeck
from action_cards import ActionCardController, canonical_action_card
from board import load_board
from exploration import ExplorationController
from movement import MovementController, Session, Snapshot
from player import PlanetCard, create_players
from technology import (ai_development_skip_required, available_technologies,
                        can_use_specialty_planet, missing_prerequisites,
                        technology_catalog)
from technology_actions import TechnologyActions
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class ExpansionCardTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board()
        self.players = create_players(self.board, config)
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.cards = ActionCardController(self.players, self.movement, deck=[], turn=self.turn)
        self.sol = next(player for player in self.players if player.faction == 'sol')
        self.hacan = next(player for player in self.players if player.faction == 'hacan')

    def session(self, stage='movement', tile=None):
        tile = tile or next(iter(self.board.values()))
        session = Session(self.sol, tile, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage=stage)
        self.movement.session = session
        return session

    def test_curated_deck_has_only_cards_with_implemented_rules(self):
        deck = ActionCardDeck(random.Random(4))
        self.assertEqual(len(deck.cards), 50)
        self.assertTrue({'rally', 'harness', 'counterstroke', 'forward_supply_base',
                         'blitz', 'solar_flare', 'war_machine', 'refit'} <= {
                             canonical_action_card(alias) for alias in deck.cards})

    def test_rally_spends_reinforcements_after_enemy_activation(self):
        session = self.session()
        session.target.units.append(Unit('exp-enemy-ship', 'destroyer', self.hacan.faction,
                                         self.hacan.color_code, UnitLocation(Region.SPACE)))
        self.sol.action_cards.append('rally')
        before = self.sol.command_pools['fleet']
        self.assertTrue(self.cards.can_play('sol', 'rally', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(self.sol.command_pools['fleet'], before + 2)
        self.assertNotIn('rally', self.sol.action_cards)

    def test_harness_replenishes_commodities_only_at_anomaly(self):
        tile = next(tile for tile in self.board.values() if tile.anomalies)
        session = self.session(tile=tile)
        self.sol.commodities = 0
        self.sol.action_cards.append('harness')
        self.assertTrue(self.cards.can_play('sol', 'harness', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(self.sol.commodities, self.sol.commodity_limit)

    def test_counterstroke_returns_existing_board_token_to_tactics(self):
        session = self.session()
        session.target.command_tokens.add('hacan')
        self.hacan.action_cards.append('counterstroke')
        before = self.hacan.command_pools['tactical']
        self.assertTrue(self.cards.can_play('hacan', 'counterstroke', session))
        self.cards.play(self.hacan, 0)
        self.assertNotIn('hacan', session.target.command_tokens)
        self.assertEqual(self.hacan.command_pools['tactical'], before + 1)

    def test_forward_supply_base_chooses_who_gets_one_good(self):
        session = self.session()
        session.target.units.append(Unit('exp-hacan-destroyer', 'destroyer', 'hacan',
                                         self.hacan.color_code, UnitLocation(Region.SPACE)))
        self.hacan.action_cards.append('fsb')
        self.assertTrue(self.cards.can_play('hacan', 'fsb', session))
        self.cards.play(self.hacan, 0)
        self.assertIn('sol', {key for key, _ in self.cards.pending_choices()})
        self.cards.resolve_pending('sol')
        self.assertEqual(self.hacan.trade_goods, 3)
        self.assertEqual(self.sol.trade_goods, 1)

    def test_war_machine_increases_production_and_discount(self):
        session = self.session(stage='production')
        session.production_sites = [('jord', 2)]
        session.production_limit = 2
        self.sol.action_cards.append('war_machine1')
        self.assertTrue(self.cards.can_play('sol', 'war_machine1', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(session.production_limit, 6)
        self.assertEqual(session.war_machine_discount, 1)

    def test_blitz_and_solar_flare_only_open_in_their_windows(self):
        session = self.session()
        session.target.units.append(Unit('exp-sol-carrier', 'carrier', 'sol',
                                         self.sol.color_code, UnitLocation(Region.SPACE)))
        self.sol.action_cards[:] = ['solar_flare', 'blitz']
        self.assertTrue(self.cards.can_play('sol', 'solar_flare', session))
        self.assertFalse(self.cards.can_play('sol', 'blitz', session))
        self.cards.play(self.sol, 0)
        self.assertTrue(session.solar_flare)
        session.stage = 'invasion_start'
        self.assertTrue(self.cards.can_play('sol', 'blitz', session))
        self.cards.play(self.sol, 0)
        self.assertIn('sol', session.blitz_factions)

    def test_refit_troops_replaces_selected_infantry_as_an_action(self):
        infantry = [(tile, unit) for tile in self.board.values() for unit in tile.units
                    if unit.owner == 'sol' and unit.kind == 'infantry'][:2]
        self.assertEqual(len(infantry), 2)
        self.sol.action_cards.append('refit')
        self.assertTrue(self.cards.can_play('sol', 'refit'))
        self.cards.play(self.sol, 0)
        for _, unit in infantry:
            self.assertIn(unit.unit_id, {key for key, _ in self.cards.pending_choices()})
            self.cards.resolve_pending(unit.unit_id)
        self.assertIsNone(self.cards.pending)
        self.assertTrue(self.turn.action_used)
        for tile, infantry_unit in infantry:
            self.assertNotIn(infantry_unit, tile.units)
            self.assertTrue(any(unit.kind == 'mech' and unit.owner == 'sol' and
                                unit.location == infantry_unit.location for unit in tile.units))

    def test_refit_troops_can_finish_after_one_infantry(self):
        infantry = next(unit for tile in self.board.values() for unit in tile.units
                        if unit.owner == 'sol' and unit.kind == 'infantry')
        self.sol.action_cards.append('refit')
        self.cards.play(self.sol, 0)
        self.cards.resolve_pending(infantry.unit_id)
        self.assertIn('__finish_refit__',
                      {key for key, _ in self.cards.pending_choices()})
        self.cards.resolve_pending('__finish_refit__')
        self.assertIsNone(self.cards.pending)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'mech'
                             for tile in self.board.values() for unit in tile.units), 1)

    def test_refit_troops_respects_four_mech_supply(self):
        home = next(tile for tile in self.board.values() if any(
            unit.owner == 'sol' and unit.kind == 'infantry' for unit in tile.units))
        infantry = next(unit for unit in home.units
                        if unit.owner == 'sol' and unit.kind == 'infantry')
        for index in range(3):
            home.units.append(Unit(f'existing-sol-mech-{index}', 'mech', 'sol',
                                   self.sol.color_code, infantry.location))
        self.sol.action_cards.append('refit')
        self.cards.play(self.sol, 0)
        self.cards.resolve_pending(infantry.unit_id)
        self.assertIsNone(self.cards.pending)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'mech'
                             for tile in self.board.values() for unit in tile.units), 4)
        self.sol.action_cards.append('refit')
        self.assertFalse(self.cards.can_play('sol', 'refit'))

    def test_refit_troops_pages_through_large_armies(self):
        home = next(tile for tile in self.board.values() if any(
            unit.owner == 'sol' and unit.kind == 'infantry' for unit in tile.units))
        infantry = next(unit for unit in home.units
                        if unit.owner == 'sol' and unit.kind == 'infantry')
        for index in range(13):
            home.units.append(Unit(f'extra-sol-infantry-{index}', 'infantry', 'sol',
                                   self.sol.color_code, infantry.location))
        self.sol.action_cards.append('refit')
        self.cards.play(self.sol, 0)
        first_page = {key for key, _ in self.cards.pending_choices()}
        self.assertIn('__next_refit__', first_page)
        self.cards.resolve_pending('__next_refit__')
        second_page = {key for key, _ in self.cards.pending_choices()}
        self.assertIn('__previous_refit__', second_page)
        self.assertTrue(any(unit_id in second_page for unit_id in
                            (f'extra-sol-infantry-{index}' for index in range(13))))


class ExpansionTechnologyTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board()
        self.players = create_players(self.board, config)
        self.sol = next(player for player in self.players if player.faction == 'sol')
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.actions = TechnologyActions(self.board, self.movement, self.turn)

    def _specialty_card(self):
        tile = next(tile for tile in self.board.values()
                    if any(planet.tech_specialties for planet in tile.planets))
        planet = next(planet for planet in tile.planets if planet.tech_specialties)
        tile.planet_owners[planet.planet_id] = 'sol'
        card = PlanetCard(planet)
        self.sol.planets.append(card)
        return card

    def test_codex_replacements_and_pok_technologies_are_available(self):
        catalog = technology_catalog()
        self.assertEqual(len(catalog), 85)
        self.assertTrue({'det', 'sr', 'pa', 'bs', 'aida', 'sdn', 'pi', 'md', 'x89c4'} <=
                        set(catalog))
        self.assertFalse({'md_base', 'md_c1', 'x89_base', 'x89'} & set(catalog))
        self.assertIn('sr', {tech['alias'] for tech in available_technologies(self.sol)})

    def test_ai_development_skips_one_upgrade_prerequisite_once(self):
        tech = technology_catalog()['pds2']
        self.sol.technologies |= {'aida'}
        self.assertEqual(missing_prerequisites(self.sol, tech), 0)
        self.assertTrue(ai_development_skip_required(self.sol, tech))
        self.sol.exhausted_technologies.add('aida')
        self.assertEqual(missing_prerequisites(self.sol, tech), 1)

    def test_psychoarchaeology_can_use_exhausted_specialty(self):
        card = self._specialty_card()
        card.exhausted = True
        self.assertFalse(can_use_specialty_planet(self.sol, card))
        self.sol.technologies |= {'pa'}
        self.assertTrue(can_use_specialty_planet(self.sol, card))
        card.exhausted = False
        self.assertIn(card.planet.planet_id,
                      {key for key, _ in self.actions.targets(self.sol, 'pa')})
        self.actions.use(self.sol, 'pa', card.planet.planet_id)
        self.assertTrue(card.exhausted)
        self.assertEqual(self.sol.trade_goods, 1)
        self.assertFalse(self.movement.history[-1].counts_as_action)

    def test_exploration_specialty_attachment_works_with_psychoarchaeology(self):
        tile = next(tile for tile in self.board.values()
                    if any(not planet.tech_specialties and
                           planet.planet_id not in {card.planet.planet_id for card in self.sol.planets}
                           for planet in tile.planets))
        planet = next(planet for planet in tile.planets if not planet.tech_specialties and
                      planet.planet_id not in {card.planet.planet_id for card in self.sol.planets})
        tile.planet_owners[planet.planet_id] = 'sol'
        card = PlanetCard(planet)
        self.sol.planets.append(card)
        exploration = ExplorationController(self.board, self.players)
        exploration.attachments[planet.planet_id] = ['biotic']
        self.movement.exploration = exploration
        self.sol.technologies |= {'pa'}
        self.assertTrue(can_use_specialty_planet(self.sol, card, exploration=exploration))
        expensive_biotic = {'requirements': 'GGGG', 'types': ('BIOTIC',)}
        self.assertEqual(
            missing_prerequisites(self.sol, expensive_biotic, [card],
                                  exploration=exploration),
            missing_prerequisites(self.sol, expensive_biotic, [card]) - 1)
        self.assertIn(planet.planet_id,
                      {key for key, _ in self.actions.targets(self.sol, 'pa')})
        self.actions.use(self.sol, 'pa', planet.planet_id)
        self.assertTrue(card.exhausted)
        self.assertEqual(self.sol.trade_goods, 1)
        self.movement.undo()
        self.assertFalse(card.exhausted)
        self.assertEqual(self.sol.trade_goods, 0)

    def test_bio_stims_readies_specialty_planet_at_end_of_turn(self):
        card = self._specialty_card()
        card.exhausted = True
        self.sol.technologies |= {'bs'}
        self.turn.mark_action_completed()
        target = f'planet:{card.planet.planet_id}'
        self.assertIn(target, {key for key, _ in self.actions.targets(self.sol, 'bs')})
        self.actions.use(self.sol, 'bs', target)
        self.assertFalse(card.exhausted)
        self.assertIn('bs', self.sol.exhausted_technologies)
        self.assertFalse(self.movement.history[-1].counts_as_action)

    def test_predictive_intelligence_redistributes_all_tokens_at_end_of_turn(self):
        self.sol.technologies |= {'pi'}
        self.turn.mark_action_completed()
        before = self.sol.command_pools.copy()
        fleet_in_home = max(sum(unit.owner == 'sol' and unit.kind not in ('fighter', 'spacedock')
                                and unit.location.region == Region.SPACE for unit in tile.units)
                            for tile in self.board.values())
        self.assertTrue(all(int(key.split(':')[1]) >= fleet_in_home
                            for key, _ in self.actions.targets(self.sol, 'pi')))
        target = f"{before['tactical'] + before['strategic']}:{before['fleet']}:0"
        self.assertIn(target, {key for key, _ in self.actions.targets(self.sol, 'pi')})
        self.actions.use(self.sol, 'pi', target)
        self.assertEqual(self.sol.command_pools['tactical'],
                         before['tactical'] + before['strategic'])
        self.assertEqual(self.sol.command_pools['fleet'], before['fleet'])
        self.assertEqual(self.sol.command_pools['strategic'], 0)
        self.assertEqual(sum(self.sol.command_pools.values()), sum(before.values()))
        self.assertIn('pi', self.sol.exhausted_technologies)
        self.assertFalse(self.movement.history[-1].counts_as_action)

    def test_sling_relay_opens_paid_one_ship_production(self):
        self.sol.technologies |= {'sr'}
        dock = next(unit for tile in self.board.values() for unit in tile.units
                    if unit.owner == 'sol' and unit.kind == 'spacedock')
        self.assertIn(dock.unit_id, {key for key, _ in self.actions.targets(self.sol, 'sr')})
        self.actions.use(self.sol, 'sr', dock.unit_id)
        session = self.movement.session
        self.assertEqual(session.stage, 'production')
        self.assertEqual(session.production_limit, 1)
        self.assertTrue(session.sling_relay)
        self.assertIn('sr', self.sol.exhausted_technologies)


if __name__ == '__main__':
    unittest.main()
