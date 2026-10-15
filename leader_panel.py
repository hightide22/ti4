"""Movable leader roster and the manual PoK leader-action entry points.

The panel only returns semantic actions.  The game controller owns timing,
payment, targets beyond the choices displayed here, and effect resolution.
"""

from __future__ import annotations

import textwrap

import arcade

from leaders import leader_available, leader_catalog
from ui_theme import (ACCENT, BORDER, CARD, GOLD, INK, MUTED, PANEL,
                      SELECTED, button, modal, surface)


ROLE_ORDER = {'agent': 0, 'commander': 1, 'hero': 2}
ROLE_COLOR = {'agent': ACCENT, 'commander': GOLD, 'hero': (194, 160, 240)}

# These have controller-side effect functions.  Other printed effects appear in
# the roster, but do not expose a dead button outside their timing window.
MANUAL_ACTION_HEROES = frozenset({
    'cabalhero', 'empyreanhero', 'keleresheroharka', 'naazhero',
    'nomadhero', 'titanshero',
})


class LeaderPanel:
    """Leader modal; ``hit_test`` returns actions without changing game state.

    Actions are ``('close',)``, ``('select', leader_id)``,
    ``('record_objective', secret)``, ``('use', leader_id)`` and, for the Naaz-Rokha agent,
    ``('use', 'naazagent', (beneficiary_faction, tile_position, planet_id))``.
    ``('target_page', direction)`` changes only this panel's target pagination.
    ``handle_click`` applies local close/select/page actions and returns the
    tuple for the caller to resolve if it is a game action.
    """

    def __init__(self):
        self.open = False
        self.selected_id: str | None = None
        self.hits: list[tuple[tuple, float, float, float, float]] = []
        self.bounds: tuple[float, float, float, float] | None = None
        self.offset = [0.0, 0.0]
        self.scroll = 0.0
        self.scroll_max = 0.0
        self.target_page = 0
        self.available_actions: frozenset[str] = frozenset()

    def close(self) -> None:
        self.open = False
        self.hits.clear()
        self.bounds = None

    def select(self, leader_id: str, player) -> bool:
        if leader_id not in player.leaders:
            return False
        if self.selected_id != leader_id:
            self.selected_id = leader_id
            self.target_page = 0
        return True

    def hit_test(self, x: float, y: float):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def handle_click(self, x: float, y: float, player):
        action = self.hit_test(x, y)
        if action is None:
            return None
        if action[0] == 'close':
            self.close()
        elif action[0] == 'select':
            self.select(action[1], player)
        elif action[0] == 'target_page':
            self.target_page = max(0, self.target_page + action[1])
        return action

    def drag_header(self, x: float, y: float) -> bool:
        if self.bounds is None:
            return False
        left, right, _, top = self.bounds
        return left <= x <= right and top - 73 <= y <= top - 4

    def move(self, dx: float, dy: float) -> None:
        self.offset[0] += dx
        self.offset[1] += dy

    def scroll_by(self, delta: float) -> None:
        self.scroll = max(0.0, min(self.scroll_max,
                                   self.scroll + float(delta) * 46.0))

    @staticmethod
    def _sorted_leaders(player):
        catalog = leader_catalog()
        return sorted((alias for alias in player.leaders if alias in catalog),
                      key=lambda alias: (ROLE_ORDER.get(catalog[alias]['type'], 9),
                                         catalog[alias]['name']))

    @staticmethod
    def _status(player, alias):
        state = player.leaders[alias]
        if state.purged:
            return 'PURGED', MUTED
        if not state.unlocked:
            return 'LOCKED', MUTED
        if state.exhausted:
            return 'EXHAUSTED', MUTED
        if leader_catalog()[alias]['type'] == 'commander':
            return 'UNLOCKED · PASSIVE', GOLD
        return 'READY', ACCENT

    @staticmethod
    def _active_action_window(window, player, movement) -> bool:
        turn = getattr(window, 'turn_order', None)
        return bool(turn and turn.active_player is player and
                    turn.can_take_action and movement.session is None and
                    not getattr(getattr(window, 'strategy', None), 'session', None) and
                    not getattr(turn, 'strategy_selection', False) and
                    not getattr(turn, 'command_allocation', False))

    @staticmethod
    def _naaz_targets(window, board):
        beneficiary = getattr(getattr(window, 'turn_order', None), 'active_player', None)
        if beneficiary is None:
            return []
        targets = []
        for tile in board.values():
            for planet in tile.planets:
                if (tile.planet_owners.get(planet.planet_id) == beneficiary.faction and
                        not planet.faction_homeworld and planet.planet_type):
                    targets.append((beneficiary.faction, tile.position,
                                    planet.planet_id, planet.name, tile.number))
        return sorted(targets, key=lambda value: (value[4], value[3]))

    @staticmethod
    def _can_use_manual_hero(window, player, board, movement, alias):
        if alias not in MANUAL_ACTION_HEROES or not leader_available(player, alias):
            return False
        if not LeaderPanel._active_action_window(window, player, movement):
            return False
        if alias == 'titanshero':
            return any(card.planet.planet_id == 'elysium' for card in player.planets)
        return True

    def _rect(self, window):
        dashboard = getattr(getattr(window, 'player_panel', None), 'HEIGHT', 0)
        width = min(940, max(420, window.width - 32))
        height = min(650, max(320, window.height - dashboard - 24))
        base_x = (window.width - width) / 2
        base_y = dashboard + (window.height - dashboard - height) / 2
        x = max(0, min(window.width - width, base_x + self.offset[0]))
        y = max(dashboard, min(window.height - height, base_y + self.offset[1]))
        self.offset[:] = [x - base_x, y - base_y]
        self.bounds = (x, x + width, y, y + height)
        return x, y, width, height, dashboard

    @staticmethod
    def _wrapped(window, key, text, x, top, width, *, size=10, line_height=18,
                 max_lines=9, color=INK):
        clean = ' '.join((text or '').split())
        if not clean:
            return 0
        rows = textwrap.wrap(clean, width=max(18, int(width / (size * .56))))
        for index, line in enumerate(rows[:max_lines]):
            window.text((key, index), line, x, top - index * line_height,
                        size, color, max_width=width)
        if len(rows) > max_lines:
            window.text((key, 'more'), '…', x,
                        top - max_lines * line_height, size, MUTED)
        return min(len(rows), max_lines)

    def _draw_roster(self, window, player, aliases, x, bottom, width, top):
        viewport_top = top - 97
        viewport_bottom = bottom + 22
        row_height = 91
        self.scroll_max = max(0, len(aliases) * row_height -
                              (viewport_top - viewport_bottom))
        self.scroll = min(self.scroll, self.scroll_max)
        context = getattr(window, 'ctx', None)
        old_scissor = context.scissor if context is not None else None
        if context is not None:
            context.scissor = (int(x), int(viewport_bottom), int(width),
                               int(viewport_top - viewport_bottom))
        try:
            for index, alias in enumerate(aliases):
                row_top = viewport_top - index * row_height + self.scroll
                row_bottom = row_top - 82
                if row_top < viewport_bottom or row_bottom > viewport_top:
                    continue
                data = leader_catalog()[alias]
                role = data['type']
                status, status_color = self._status(player, alias)
                selected = alias == self.selected_id
                surface(x, x + width, row_bottom, row_top,
                        SELECTED if selected else CARD,
                        ROLE_COLOR.get(role, BORDER) if selected else BORDER)
                surface(x + 10, x + 43, row_top - 43, row_top - 10,
                        SELECTED, ROLE_COLOR.get(role, BORDER))
                window.text(('leader_role_glyph', alias), role[0].upper(),
                            x + 20, row_top - 32, 16, ROLE_COLOR.get(role, ACCENT))
                window.text(('leader_roster_role', alias), role.upper(),
                            x + 53, row_top - 21, 8, ROLE_COLOR.get(role, ACCENT))
                window.text(('leader_roster_name', alias), data['name'],
                            x + 53, row_top - 43, 11, INK,
                            max_width=width - 65)
                window.text(('leader_roster_status', alias), status,
                            x + 53, row_top - 66, 8, status_color,
                            max_width=width - 65)
                self.hits.append((('select', alias), x, x + width,
                                  max(row_bottom, viewport_bottom),
                                  min(row_top, viewport_top)))
        finally:
            if context is not None:
                context.scissor = old_scissor

    def _draw_details(self, window, player, board, movement, alias,
                      x, bottom, width, top):
        data = leader_catalog()[alias]
        role = data['type']
        status, status_color = self._status(player, alias)
        x += 18
        width -= 36
        window.text(('leader_detail_role', alias), role.upper(), x,
                    top - 112, 10, ROLE_COLOR.get(role, ACCENT))
        window.text(('leader_detail_status', alias), status, x + width - 130,
                    top - 112, 9, status_color, max_width=130)
        self._wrapped(window, ('leader_detail_name', alias), data['name'], x,
                      top - 146, width, size=20, line_height=26, max_lines=2)
        title = data.get('title') or ''
        window.text(('leader_detail_title', alias), title, x, top - 198,
                    11, GOLD, max_width=width)
        window.text(('leader_detail_timing_label', alias), 'TIMING', x,
                    top - 232, 9, ACCENT)
        timing_rows = self._wrapped(window, ('leader_detail_timing', alias),
                                    data.get('abilityWindow', ''), x,
                                    top - 252, width, size=10,
                                    line_height=17, max_lines=3, color=MUTED)
        ability_top = top - 278 - max(0, timing_rows - 1) * 17
        window.text(('leader_detail_ability_label', alias), 'ABILITY', x,
                    ability_top, 9, ACCENT)
        naaz_targets = (self._naaz_targets(window, board)
                        if alias == 'naazagent' and
                        alias in self.available_actions and
                        leader_available(player, alias) and
                        movement.session is None else [])
        self._wrapped(window, ('leader_detail_ability', alias),
                      data.get('abilityText', ''), x, ability_top - 20,
                      width, size=10, line_height=18,
                      max_lines=(3 if naaz_targets else
                                 max(3, int((ability_top - bottom - 118) / 18))))

        if not player.leaders[alias].unlocked:
            unlock = data.get('unlockCondition') or 'Score 3 objectives to unlock a hero.'
            surface(x, x + width, bottom + 25, bottom + 82, CARD)
            window.text(('leader_unlock_label', alias), 'UNLOCK CONDITION', x + 12,
                        bottom + 61, 8, GOLD)
            self._wrapped(window, ('leader_unlock_text', alias), unlock,
                          x + 12, bottom + 44, width - 24,
                          size=9, line_height=13, max_lines=2, color=MUTED)
            return

        can_use = (self._can_use_manual_hero(window, player, board, movement, alias) or
                   alias in self.available_actions and
                   leader_available(player, alias) and role != 'commander')
        if can_use and alias != 'naazagent':
            bx, by, bw, bh = x, bottom + 25, min(240, width), 38
            button(window, ('leader_use', alias),
                   'USE HERO' if role == 'hero' else 'USE AGENT', bx, by, bw,
                   bh, primary=True, size=11)
            self.hits.append((('use', alias), bx, bx + bw, by, by + bh))
            return

        if naaz_targets:
            self._draw_naaz_targets(window, alias, naaz_targets,
                                    x, bottom, width)
            return

        if role != 'commander' and not player.leaders[alias].purged:
            window.text(('leader_timing_hint', alias),
                        'Available at the timing window shown above.' if
                        leader_available(player, alias) else
                        'Ready again during the next status phase.',
                        x, bottom + 34, 9, MUTED, max_width=width)

    def _draw_naaz_targets(self, window, alias, targets, x, bottom, width):
        page_size = 3
        pages = max(1, (len(targets) + page_size - 1) // page_size)
        self.target_page = min(self.target_page, pages - 1)
        visible = targets[self.target_page * page_size:
                          (self.target_page + 1) * page_size]
        window.text('leader_naaz_targets_title', 'EXPLORE A PLANET AT TURN END',
                    x, bottom + 169, 9, GOLD, max_width=width)
        for index, (faction, position, planet_id, name, number) in enumerate(visible):
            by = bottom + 126 - index * 36
            label = f'TILE {number} · {name}'
            button(window, ('leader_naaz_target', planet_id), label,
                   x, by, width, 31, size=9)
            self.hits.append((('use', alias, (faction, position, planet_id)),
                              x, x + width, by, by + 31))
        if pages > 1:
            by = bottom + 23
            button(window, 'leader_naaz_previous', '‹', x, by, 36, 28,
                   enabled=self.target_page > 0, size=12)
            button(window, 'leader_naaz_next', '›', x + width - 36,
                   by, 36, 28, enabled=self.target_page < pages - 1,
                   size=12)
            if self.target_page > 0:
                self.hits.append((('target_page', -1), x, x + 36, by, by + 28))
            if self.target_page < pages - 1:
                self.hits.append((('target_page', 1), x + width - 36,
                                  x + width, by, by + 28))
            window.text('leader_naaz_page', f'{self.target_page + 1}/{pages}',
                        x + width / 2 - 14, by + 8, 9, MUTED)

    def draw(self, window, player, board, movement, *, available_actions=()):
        """Render the roster.

        ``available_actions`` is the caller's set of leader IDs whose printed
        timing windows are open and whose effects it can resolve.  This enables
        timing-specific agents without advertising unusable buttons.
        """
        self.available_actions = frozenset(available_actions)
        self.hits.clear()
        if not self.open:
            self.bounds = None
            return
        aliases = self._sorted_leaders(player)
        if self.selected_id not in aliases:
            self.selected_id = aliases[0] if aliases else None
            self.target_page = 0
        x, bottom, width, height, dashboard = self._rect(window)
        top = bottom + height
        arcade.draw_lrbt_rectangle_filled(0, window.width, dashboard,
                                          window.height, (3, 7, 14, 190))
        modal(x, x + width, bottom, top)
        window.text('leader_panel_title', f'{player.faction.upper()} · LEADERS',
                    x + 22, top - 38, 17, ACCENT)
        window.text('leader_panel_move_hint', 'DRAG HEADER TO MOVE',
                    x + width - 221, top - 32, 8, MUTED)
        close_x, close_y = x + width - 104, top - 68
        button(window, 'leader_panel_close', 'CLOSE', close_x, close_y,
               79, 28, size=9)
        self.hits.append((('close',), close_x, close_x + 79,
                          close_y, close_y + 28))
        score_y = top - 68
        button(window, 'leader_record_public', 'RECORD PUBLIC', x + 22,
               score_y, 120, 28, size=8)
        button(window, 'leader_record_secret', 'RECORD SECRET', x + 148,
               score_y, 120, 28, size=8)
        self.hits.append((('record_objective', False), x + 22, x + 142,
                          score_y, score_y + 28))
        self.hits.append((('record_objective', True), x + 148, x + 268,
                          score_y, score_y + 28))
        if width >= 680:
            window.text('leader_record_count',
                        f'SCORED OBJECTIVES {player.scored_objectives} · '
                        f'SECRET {player.scored_secret_objectives}',
                        x + 279, top - 59, 9, MUTED,
                        max_width=width - 396)
        window.text('leader_record_hint',
                    'Manual record only: use after an objective has already been scored.',
                    x + 22, top - 89, 8, MUTED, max_width=width - 44)

        roster_x = x + 22
        roster_width = min(292, max(205, width * .32))
        detail_x = roster_x + roster_width + 13
        detail_width = max(180, x + width - detail_x - 22)
        surface(detail_x, detail_x + detail_width,
                bottom + 21, top - 96, PANEL)
        self._draw_roster(window, player, aliases, roster_x, bottom,
                          roster_width, top)
        if self.selected_id is None:
            window.text('leader_panel_empty', 'No leaders for this faction.',
                        detail_x + 18, top - 125, 11, MUTED)
        else:
            self._draw_details(window, player, board, movement,
                               self.selected_id, detail_x, bottom,
                               detail_width, top)
