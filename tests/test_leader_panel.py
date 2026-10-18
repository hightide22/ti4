"""Leader modal layout and semantic click regressions."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from board import ROOT, load_board
from leader_panel import LeaderPanel
from leaders import record_scored_objective
from player import create_players


class LeaderPanelTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board(
            ROOT / 'maps/four_player.json',
            ('naaz', 'sol', 'jolnar', 'hacan'))
        self.players = create_players(self.board, self.config)
        self.player = self.players[0]
        self.movement = SimpleNamespace(session=None)
        self.window = SimpleNamespace(
            width=1280, height=800,
            mouse_position=(-1, -1),
            text=MagicMock(),
            ctx=SimpleNamespace(scissor=None),
            player_panel=SimpleNamespace(HEIGHT=172),
            turn_order=SimpleNamespace(
                active_player=self.player,
                can_take_action=True,
                strategy_selection=False,
                command_allocation=False),
            strategy=SimpleNamespace(session=None))
        self.panel = LeaderPanel()
        self.panel.open = True

    def draw(self, *, player=None, available_actions=()):
        with (patch('leader_panel.arcade.draw_lrbt_rectangle_filled'),
              patch('leader_panel.modal'),
              patch('leader_panel.surface'),
              patch('leader_panel.button')):
            self.panel.draw(self.window, player or self.player,
                            self.board, self.movement,
                            available_actions=available_actions)

    def test_leader_statuses_and_selection_are_visible(self):
        self.draw()
        aliases = [action[1] for action, *_ in self.panel.hits
                   if action[0] == 'select']
        self.assertIn('naazagent', aliases)
        self.assertIn('naazcommander', aliases)
        self.assertIn('naazhero', aliases)
        self.assertEqual(self.panel._status(self.player, 'naazagent')[0], 'READY')
        self.assertEqual(self.panel._status(self.player, 'naazhero')[0], 'LOCKED')
        action, left, right, bottom, top = next(
            row for row in self.panel.hits
            if row[0] == ('select', 'naazcommander'))
        self.assertEqual(self.panel.handle_click((left + right) / 2,
                                                 (bottom + top) / 2,
                                                 self.player), action)
        self.assertEqual(self.panel.selected_id, 'naazcommander')

    def test_naaz_agent_only_offers_controlled_non_home_planets(self):
        neutral = next(tile for tile in self.board.values()
                       if any(planet.planet_type and not planet.faction_homeworld
                              for planet in tile.planets))
        planet = next(planet for planet in neutral.planets
                      if planet.planet_type and not planet.faction_homeworld)
        neutral.planet_owners[planet.planet_id] = self.player.faction
        self.panel.select('naazagent', self.player)
        self.draw()
        self.assertFalse(any(row[0][0] == 'use' for row in self.panel.hits))
        self.draw(available_actions=('naazagent',))
        use = [row[0] for row in self.panel.hits if row[0][0] == 'use']
        self.assertIn(('use', 'naazagent',
                       (self.player.faction, neutral.position,
                        planet.planet_id)), use)
        self.assertFalse(any(action[-1][-1] in {
            card.planet.planet_id for card in self.player.planets}
            for action in use if len(action) == 3))

    def test_hero_button_requires_unlock_and_action_window(self):
        self.panel.select('naazhero', self.player)
        self.draw()
        self.assertNotIn(('use', 'naazhero'),
                         [row[0] for row in self.panel.hits])
        for _ in range(3):
            record_scored_objective(self.player)
        self.draw()
        self.assertIn(('use', 'naazhero'),
                      [row[0] for row in self.panel.hits])
        self.movement.session = object()
        self.draw()
        self.assertNotIn(('use', 'naazhero'),
                         [row[0] for row in self.panel.hits])

    def test_timing_specific_agent_needs_controller_permission(self):
        sol = self.players[1]
        self.window.turn_order.active_player = sol
        self.panel.select('solagent', sol)
        self.draw(player=sol)
        self.assertNotIn(('use', 'solagent'),
                         [row[0] for row in self.panel.hits])
        # A controller can expose a triggered ability only at its printed
        # timing window; the panel never exhausts the leader itself.
        self.draw(player=sol, available_actions=('solagent',))
        self.assertIn(('use', 'solagent'),
                      [row[0] for row in self.panel.hits])

    def test_l1z1x_agent_button_opens_active_system_infantry_choices(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('l1z1x', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        player = players[0]
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        self.window.player_panel.players = players
        self.window.turn_order.active_player = player
        self.movement.session = SimpleNamespace(player=player, target=tile,
                                                stage='movement')
        self.panel.select('l1z1xagent', player)
        self.draw(player=player, available_actions=('l1z1xagent',))
        button = next(row for row in self.panel.hits
                      if row[0] == ('use', 'l1z1xagent'))
        self.assertIsNone(self.panel.handle_click(
            (button[1] + button[2]) / 2, (button[3] + button[4]) / 2, player))
        self.draw(player=player, available_actions=('l1z1xagent',))
        self.assertIn(('use', 'l1z1xagent', infantry.unit_id),
                      [row[0] for row in self.panel.hits])

    def test_header_drag_stays_on_screen(self):
        self.draw()
        left, right, bottom, top = self.panel.bounds
        self.assertTrue(self.panel.drag_header(left + 20, top - 20))
        self.panel.move(5000, 5000)
        self.draw()
        left, right, bottom, top = self.panel.bounds
        self.assertLessEqual(right, self.window.width)
        self.assertLessEqual(top, self.window.height)
        self.panel.move(-10000, -10000)
        self.draw()
        left, right, bottom, top = self.panel.bounds
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(bottom, self.window.player_panel.HEIGHT)


if __name__ == '__main__':
    unittest.main()
