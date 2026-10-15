"""The acceptance harness must be reproducible and fail on stalled decisions."""
import random
import unittest
from unittest.mock import patch

from scripts.ai_benchmark import HeadlessGame, run_match
from exploration import PendingExploration
from player import PlanetCard
from units import Region, Unit, UnitLocation


class AIBenchmarkTests(unittest.TestCase):
    def test_seed_replays_real_match_and_restores_global_rng(self):
        before = random.getstate()
        first = run_match(count=3, rounds=2, seed=41)
        self.assertEqual(random.getstate(), before)
        second = run_match(count=3, rounds=2, seed=41)
        self.assertEqual(first['status'], 'passed', first.get('error'))
        self.assertEqual(second['status'], 'passed', second.get('error'))
        for result in (first, second):
            result.pop('wall_seconds')
            result.pop('decision_seconds')
        self.assertEqual(first, second)
        self.assertEqual(len(first['rounds']), 2)
        self.assertTrue(all(first['final'][faction]['planets'] > initial['planets']
                            for faction, initial in first['initial'].items()))

    def test_no_op_loop_reports_precise_phase_instead_of_hanging(self):
        with patch('scripts.ai_benchmark.ObservedAI.step', return_value=None):
            result = run_match(rounds=1, seed=5, stall_steps=3)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('No game-state change', result['error'])
        self.assertTrue(result['state']['draft'])
        self.assertEqual(result['state']['draft_cursor'], 0)
        self.assertEqual(result['steps'], 4)

    def test_bot_resolves_exploration_pending_even_during_draft(self):
        game = HeadlessGame(factions=('naaz', 'sol', 'hacan'))
        naaz = game.turn_order.players[0]
        tile = next(tile for tile in game.board.values()
                    if any(planet.faction_homeworld == 'naaz' for planet in tile.planets))
        planet_id = tile.planets[0].planet_id
        game.exploration.pending = PendingExploration(
            naaz.faction, tile.position, planet_id, 'mo1',
            ('place_infantry', 'decline'))
        before = len(tile.units)
        game.ai.step()
        self.assertIsNone(game.exploration.pending)
        self.assertEqual(len(tile.units), before + 1)

    def test_bot_assembles_fragments_and_uses_naaz_hero(self):
        game = HeadlessGame(factions=('naaz', 'sol', 'hacan'))
        naaz = game.turn_order.players[0]
        game.turn_order.strategy_selection = False
        game.turn_order.active_index = 0
        game.exploration.fragments['naaz'] = ['crf1', 'crf2']
        self.assertTrue(game.ai._play_component_action(naaz))
        self.assertEqual(game.exploration.fragments['naaz'], [])
        self.assertEqual(len(game.exploration.relics.owned['naaz']), 1)
        self.assertEqual(game.turn_order.actions_used, 1)
        game.exploration.relics.owned['naaz'].clear()
        game.turn_order.actions_used = 0
        game.turn_order.action_used = False
        naaz.leaders['naazhero'].unlocked = True
        self.assertTrue(game.ai._play_component_action(naaz))
        self.assertEqual(game.turn_order.actions_used, 1)
        self.assertEqual(list(game.ai.hero_secondary_queue), [('naaz', 7), ('naaz', 5)])
        game.ai.step()
        self.assertTrue(game.strategy.session.hero_secondary)
        for _ in range(30):
            if not game.ai.hero_secondary_queue and game.strategy.session is None:
                break
            game.ai.step()
        self.assertFalse(game.ai.hero_secondary_queue)
        self.assertIsNone(game.strategy.session)
        self.assertEqual(game.turn_order.actions_used, 1)

    def test_bot_discards_excess_action_card_before_other_decisions(self):
        game = HeadlessGame(factions=('naaz', 'sol', 'hacan'))
        naaz = game.turn_order.players[0]
        naaz.action_cards = ['flank_speed'] * 8
        game.ai.step()
        self.assertEqual(len(naaz.action_cards), 7)
        self.assertEqual(len(game.action_cards.discard), 1)

    def test_cabal_ai_can_plan_upgrade_using_riftmeld(self):
        game = HeadlessGame(factions=('cabal', 'sol', 'hacan'))
        cabal = game.turn_order.players[0]
        cabal.captured_units['destroyer'] = 1
        choice = game.ai.choose_technology(cabal, 0, values={'dd2': 100})
        self.assertIsNotNone(choice)
        self.assertEqual(choice[0], 'dd2')
        self.assertEqual(choice[1], ())

    def test_freelancers_offer_is_paid_or_declined_without_stalling(self):
        for affordable in (True, False):
            with self.subTest(affordable=affordable):
                game = HeadlessGame(factions=('sol', 'jolnar', 'hacan'))
                sol = game.turn_order.players[0]
                tile = next(tile for tile in game.board.values()
                            if any(planet.faction_homeworld == 'sol'
                                   for planet in tile.planets))
                before = len(tile.units)
                if not affordable:
                    for card in sol.planets:
                        card.exhausted = True
                    sol.trade_goods = 0
                game.exploration.pending = PendingExploration(
                    'sol', tile.position, tile.planets[0].planet_id, 'frln1',
                    ('produce_one_unit', 'decline'))
                game.ai.step()
                self.assertIsNotNone(game.exploration.freelancer_offer)
                game.ai.step()
                self.assertIsNone(game.exploration.freelancer_offer)
                self.assertEqual(len(tile.units), before + int(affordable))

    def test_freelancers_ai_resolves_capacity_before_continuing(self):
        game = HeadlessGame(factions=('sol', 'jolnar', 'hacan'))
        sol = game.turn_order.players[0]
        tile = next(tile for tile in game.board.values()
                    if tile.player is None and tile.planets and not tile.units)
        planet = tile.planets[0]
        tile.planet_owners[planet.planet_id] = sol.faction
        sol.planets.append(PlanetCard(planet))
        for index in range(7):
            tile.units.append(Unit(f'overflow-fighter-{index}', 'fighter',
                                   sol.faction, sol.color_code,
                                   UnitLocation(Region.SPACE)))
        game.exploration.freelancer_offer = (sol.faction, tile.position,
                                              planet.planet_id)
        game.ai.step()
        self.assertEqual('capacity_overflow', game.movement.session.stage)
        game.ai.resolve_movement()
        self.assertIsNone(game.movement.session)
        self.assertEqual(6, sum(unit.kind == 'fighter' and unit.owner == sol.faction
                                for unit in tile.units))
        self.assertIsNone(game.exploration.freelancer_offer)

    def test_hero_frontier_waits_for_intervening_exploration_decisions(self):
        game = HeadlessGame(factions=('empyrean', 'sol', 'hacan'))
        player = game.turn_order.players[0]
        tile = next(iter(game.board.values()))
        game.hero_frontier_queue = [(player, tile.position)]
        with patch.object(game.exploration, 'explore_frontier') as explore:
            game.exploration.freelancer_offer = (player.faction, tile.position, None)
            game.advance_hero_frontier()
            explore.assert_not_called()
            game.exploration.freelancer_offer = None
            game.exploration.secret_objectives.pending_returns[player.faction] = 1
            game.advance_hero_frontier()
            explore.assert_not_called()
            del game.exploration.secret_objectives.pending_returns[player.faction]
            game.advance_hero_frontier()
            explore.assert_called_once()
        self.assertFalse(game.hero_frontier_queue)

    def test_bot_returns_excess_secret_objective(self):
        game = HeadlessGame(factions=('sol', 'jolnar', 'hacan'))
        sol = game.turn_order.players[0]
        game.exploration.secret_objectives.draw(sol, 3)
        self.assertEqual(game.exploration.secret_objectives.pending_returns['sol'], 1)
        game.ai.step()
        self.assertNotIn('sol', game.exploration.secret_objectives.pending_returns)
        self.assertEqual(len(game.exploration.secret_objectives.hands['sol']), 3)


if __name__ == '__main__':
    unittest.main()
