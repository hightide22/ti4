"""The relic modal must return exact choices without spending game state."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from board import Board, TILES
from exploration import ExplorationController
from player import PlanetCard, PlayerState
from relic_action_panel import RelicActionPanel
from units import Region, Unit, UnitLocation


def game():
    home = TILES.create(1, (0, 0))
    nearby = TILES.create(30, (1, 0))
    board = Board((home, nearby))
    sol = PlayerState('sol', 'Sol', 'blu', 4,
                      [PlanetCard(planet) for planet in
                       (*home.planets, *nearby.planets)])
    hacan = PlayerState('hacan', 'Hacan', 'ylw', 6)
    for tile in (home, nearby):
        for planet in tile.planets:
            tile.planet_owners[planet.planet_id] = 'sol'
    exploration = ExplorationController(board, (sol, hacan), relic_deck=[])
    return exploration, home, nearby, sol, hacan


class RelicActionPanelTests(unittest.TestCase):
    def test_codex_overflow_requires_exact_discard_and_cannot_be_closed(self):
        exploration, _, _, sol, _ = game()
        sol.action_cards[:] = [f'card-{index}' for index in range(9)]
        panel = RelicActionPanel()
        self.assertTrue(panel.begin_discard(sol))
        self.assertEqual(2, panel.required_discard_count)
        self.assertIsNone(panel.handle(('close',), exploration, None))
        self.assertTrue(panel.open)
        self.assertIsNone(panel.handle(('confirm',), exploration, None))
        panel.handle(('card', 1), exploration, None)
        self.assertIsNone(panel.handle(('confirm',), exploration, None))
        panel.handle(('card', 5), exploration, None)
        panel.handle(('card', 7), exploration, None)
        self.assertEqual({1, 5}, panel.selected_cards)
        self.assertEqual(('discard_to_limit', 'sol',
                          ((1, 'card-1'), (5, 'card-5'))),
                         panel.handle(('confirm',), exploration, None))
        self.assertFalse(panel.open)
        self.assertEqual(9, len(sol.action_cards))

    def test_yssaril_has_no_discard_requirement(self):
        exploration, _, _, _, _ = game()
        yssaril = PlayerState('yssaril', 'Yssaril', 'grn', 3,
                              action_cards=[f'card-{index}' for index in range(9)])
        panel = RelicActionPanel()
        self.assertFalse(panel.begin_discard(yssaril))
        self.assertFalse(panel.open)

    def test_hand_limit_recounts_when_cards_change_while_window_is_open(self):
        exploration, _, _, sol, _ = game()
        sol.action_cards[:] = [f'card-{index}' for index in range(9)]
        panel = RelicActionPanel()
        self.assertTrue(panel.begin_discard(sol))
        panel.handle(('card', 8), exploration, None)
        sol.action_cards.pop()
        self.assertIsNone(panel.handle(('confirm',), exploration, None))
        self.assertEqual(1, panel.required_discard_count)
        self.assertFalse(panel.selected_cards)
        panel.handle(('card', 2), exploration, None)
        self.assertEqual(('discard_to_limit', 'sol', ((2, 'card-2'),)),
                         panel.handle(('confirm',), exploration, None))

    def test_dynamis_is_immediate_and_does_not_open_modal(self):
        _, _, _, sol, _ = game()
        panel = RelicActionPanel()
        self.assertEqual(('use_relic', 'dynamiscore'),
                         panel.begin('dynamiscore', sol))
        self.assertFalse(panel.open)

    def test_codex_selects_at_most_three_discard_instances(self):
        exploration, _, _, sol, _ = game()
        deck = SimpleNamespace(discard=['morale_boost', 'morale_boost',
                                        'direct_hit', 'disable'])
        panel = RelicActionPanel()
        panel.begin('codex', sol)
        for index in range(4):
            panel.handle(('card', index), exploration, deck)
        self.assertEqual({0, 1, 2}, panel.selected_cards)
        self.assertEqual(('use_relic', 'codex',
                          ('morale_boost', 'morale_boost', 'direct_hit')),
                         panel.handle(('confirm',), exploration, deck))
        self.assertEqual(4, len(deck.discard))
        self.assertFalse(panel.open)

    def test_nanoforge_only_targets_controlled_non_home_planet(self):
        exploration, home, nearby, sol, _ = game()
        panel = RelicActionPanel()
        panel.begin('nanoforge', sol)
        self.assertIsNone(panel.handle(('planet', home.planets[0].planet_id),
                                       exploration, None))
        self.assertIsNone(panel.planet_id)
        self.assertIsNone(panel.handle(('confirm',), exploration, None))
        target = nearby.planets[0].planet_id
        panel.handle(('planet', target), exploration, None)
        self.assertEqual(('use_relic', 'nanoforge', target),
                         panel.handle(('confirm',), exploration, None))
        self.assertEqual({}, exploration.attached_relics)

    def test_stellar_converter_requires_adjacent_bombardment(self):
        exploration, home, nearby, sol, _ = game()
        for planet in nearby.planets:
            nearby.planet_owners[planet.planet_id] = 'hacan'
        panel = RelicActionPanel()
        panel.begin('stellarconverter', sol)
        target = nearby.planets[0].planet_id
        panel.handle(('planet', target), exploration, None)
        self.assertIsNone(panel.planet_id)
        home.units.append(Unit('dread-1', 'dreadnought', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        panel.handle(('planet', target), exploration, None)
        self.assertEqual(('use_relic', 'stellarconverter', target),
                         panel.handle(('confirm',), exploration, None))
        self.assertFalse(exploration.destroyed_planets)

    def test_jr_xs455_o_collects_structure_payment_or_trade_good_choice(self):
        exploration, _, nearby, sol, _ = game()
        panel = RelicActionPanel()
        panel.begin('titanprototype', sol)
        target = next(planet.planet_id for planet in nearby.planets
                      if planet.resources == 3)
        panel.handle(('planet', target), exploration, None)
        panel.handle(('structure', 'pds'), exploration, None)
        self.assertIsNone(panel.handle(('confirm',), exploration, None))
        panel.handle(('pay_planet', target), exploration, None)
        self.assertEqual(('use_relic', 'titanprototype', 'sol', target,
                          'pds', (target,), 0),
                         panel.handle(('confirm',), exploration, None))
        self.assertFalse(sol.planets[-1].exhausted)
        self.assertFalse(nearby.units)

        panel.begin('titanprototype', sol)
        panel.handle(('recipient', 'hacan'), exploration, None)
        self.assertEqual(('use_relic', 'titanprototype', 'hacan', None,
                          None, (), 0),
                         panel.handle(('decline',), exploration, None))


if __name__ == '__main__':
    unittest.main()
