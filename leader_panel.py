"""Movable leader roster and the manual PoK leader-action entry points.

The panel only returns semantic actions.  The game controller owns timing,
payment, targets beyond the choices displayed here, and effect resolution.
"""

from __future__ import annotations

import textwrap

import arcade

from leaders import leader_available, leader_catalog
from leader_effects import (arborec_agent_replacements, cabal_agent_options,
                            muaat_agent_systems, xxcha_agent_remove_options)
from ui_theme import (ACCENT, BORDER, CARD, GOLD, INK, MUTED, PANEL,
                      SELECTED, button, modal, surface)
from units import Region, UNIT_TYPES


ROLE_ORDER = {'agent': 0, 'commander': 1, 'hero': 2}
ROLE_COLOR = {'agent': ACCENT, 'commander': GOLD, 'hero': (194, 160, 240)}

# These have controller-side effect functions.  Other printed effects appear in
# the roster, but do not expose a dead button outside their timing window.
MANUAL_ACTION_HEROES = frozenset({
    'cabalhero', 'empyreanhero', 'keleresheroharka', 'naazhero',
    'nomadhero', 'titanshero',
})
MANUAL_ACTION_AGENTS = frozenset({'arborecagent', 'cabalagent', 'hacanagent',
                                  'l1z1xagent', 'muaatagent', 'saaragent',
                                  'xxchaagent'})


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
        self.agent_mode: str | None = None
        self.agent_ship_id: str | None = None
        self.agent_planet_id: str | None = None
        self.agent_player_faction: str | None = None
        self.reaction_owner_faction: str | None = None

    def close(self) -> None:
        self.open = False
        self.hits.clear()
        self.bounds = None
        self.cancel_agent_action()
        self.reaction_owner_faction = None

    def open_for_reaction(self, player, leader_id: str) -> None:
        """Open this leader's card for a triggered timing window."""
        self.open = True
        self.reaction_owner_faction = player.faction
        self.select(leader_id, player)
        if leader_id in ('empyreanagent', 'argentagent'):
            self.begin_agent_action(leader_id)

    def begin_agent_action(self, leader_id: str) -> None:
        self.agent_mode = leader_id
        self.agent_ship_id = None
        self.agent_planet_id = None
        self.agent_player_faction = None
        self.target_page = 0

    def cancel_agent_action(self) -> None:
        self.agent_mode = None
        self.agent_ship_id = None
        self.agent_planet_id = None
        self.agent_player_faction = None

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
        elif action[0] == 'use' and action[1] in MANUAL_ACTION_AGENTS and len(action) == 2:
            self.begin_agent_action(action[1])
            return None
        elif action[0] == 'target_page':
            self.target_page = max(0, self.target_page + action[1])
        elif action[0] == 'agent_ship_select':
            self.agent_ship_id = action[1]
            self.target_page = 0
        elif action[0] == 'agent_planet_select':
            self.agent_planet_id = action[1]
            self.target_page = 0
        elif action[0] == 'agent_player_select':
            self.agent_player_faction = action[1]
            self.target_page = 0
        elif action[0] == 'agent_back':
            self.agent_ship_id = None
            self.agent_planet_id = None
            self.agent_player_faction = None
            self.target_page = 0
        elif action[0] == 'agent_cancel':
            self.cancel_agent_action()
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

        if self.agent_mode == alias:
            self._draw_agent_choices(window, player, board, movement, x, bottom, width)
            return

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

    def _draw_agent_choices(self, window, player, board, movement, x, bottom, width):
        alias = self.agent_mode
        choice_title = ('CHOOSE A COMMAND POOL' if alias == 'empyreanagent' else
                        'PLACE PRODUCED GROUND FORCES' if alias == 'argentagent' else
                        'CHOOSE THE EFFECT TARGET')
        window.text(('leader_agent_choice_title', alias), choice_title,
                    x, bottom + 174, 10, ACCENT)
        page_size = 4
        pages = 1
        if alias == 'empyreanagent':
            session = movement.session
            beneficiary = session.player if session and session.stage == 'empyrean_agent' else None
            if beneficiary is None:
                window.text('empyrean_agent_no_beneficiary',
                            'The command-token reaction is no longer available.',
                            x, bottom + 128, 9, MUTED, width)
            else:
                window.text('empyrean_agent_beneficiary',
                            f'{beneficiary.faction.upper()} GAINS 1 COMMAND TOKEN · CHOOSE A POOL',
                            x, bottom + 166, 9, GOLD, max_width=width)
                for index, pool in enumerate(('tactical', 'fleet', 'strategic')):
                    by = bottom + 128 - index * 34
                    button(window, ('empyrean_agent_pool', pool),
                           f'ADD TO {pool.upper()} POOL', x, by, width, 29,
                           size=9)
                    self.hits.append((('use', alias, pool), x, x + width,
                                      by, by + 29))
        elif alias == 'argentagent':
            session = movement.session
            unit_ids = (session.argent_agent_produced_ids
                        if session and session.stage == 'argent_agent' else [])
            pages = max(1, (len(unit_ids) + page_size - 1) // page_size)
            self.target_page = min(self.target_page, pages - 1)
            visible = unit_ids[self.target_page * page_size:
                               (self.target_page + 1) * page_size]
            for index, unit_id in enumerate(visible):
                unit = next((candidate for candidate in session.target.units
                             if candidate.unit_id == unit_id), None)
                if unit is None:
                    continue
                options = movement.argent_agent_destination_options(session, unit_id)
                destination_id = session.argent_agent_destinations.get(unit_id)
                destination = next((option for option in options
                                    if option[0] == destination_id), None)
                kind = UNIT_TYPES[unit.kind]['name'].upper()
                label = (f'{kind} · MOVE TO {destination[1]} · TILE {destination[2]}'
                         if destination else f'{kind} · STAY IN TILE {session.target.number}')
                by = bottom + 138 - index * 31
                button(window, ('argent_agent_cycle', unit_id), label,
                       x, by, width, 27, size=8)
                self.hits.append((('argent_agent_cycle', unit_id), x,
                                  x + width, by, by + 27))
        elif alias == 'l1z1xagent':
            session = movement.session
            targets = ([unit for unit in session.target.units
                        if unit.owner == session.player.faction and
                        unit.kind == 'infantry' and
                        unit.location.region in (Region.PLANET, Region.TRANSPORT)]
                       if session and session.player is player else [])
            targets.sort(key=lambda unit: (unit.location.region.value,
                                           unit.location.planet_id or '',
                                           unit.unit_id))
            pages = max(1, (len(targets) + page_size - 1) // page_size)
            self.target_page = min(self.target_page, pages - 1)
            visible = targets[self.target_page * page_size:
                              (self.target_page + 1) * page_size]
            for index, unit in enumerate(visible):
                location = (unit.location.planet_id or 'TRANSPORT'
                            if unit.location.region == Region.PLANET else 'IN SPACE')
                by = bottom + 138 - index * 31
                button(window, ('agent_infantry_target', unit.unit_id),
                       f'{location} · INFANTRY', x, by, width, 27, size=8)
                self.hits.append((('use', alias, unit.unit_id), x, x + width,
                                  by, by + 27))
        elif alias == 'saaragent':
            session = movement.session
            active_player = session.player if session else None
            ships = [(tile, unit) for tile in board.values() for unit in tile.units
                     if active_player and unit.owner == active_player.faction and
                     UNIT_TYPES[unit.kind]['ship'] and
                     unit.location.region == Region.SPACE]
            ships.sort(key=lambda row: (row[0].number, row[1].kind, row[1].unit_id))
            pages = max(1, (len(ships) + page_size - 1) // page_size)
            self.target_page = min(self.target_page, pages - 1)
            visible = ships[self.target_page * page_size:
                            (self.target_page + 1) * page_size]
            for index, (tile, ship) in enumerate(visible):
                by = bottom + 138 - index * 31
                label = (f'TILE {tile.number} · {UNIT_TYPES[ship.kind]["name"].upper()} · '
                         f'MOVE {ship.move_value}')
                button(window, ('saar_ship_target', ship.unit_id), label,
                       x, by, width, 27, size=8)
                self.hits.append((('use', alias, ship.unit_id), x, x + width,
                                  by, by + 27))
        elif alias == 'muaatagent':
            if self.agent_player_faction is None:
                beneficiaries = list(window.player_panel.players)
                pages = max(1, (len(beneficiaries) + page_size - 1) // page_size)
                self.target_page = min(self.target_page, pages - 1)
                visible = beneficiaries[self.target_page * page_size:
                                        (self.target_page + 1) * page_size]
                for index, beneficiary in enumerate(visible):
                    by = bottom + 138 - index * 31
                    button(window, ('agent_beneficiary', beneficiary.faction),
                           f'{beneficiary.faction.upper()} · CHOOSE PLAYER',
                           x, by, width, 27, size=8)
                    self.hits.append((('agent_player_select', beneficiary.faction),
                                      x, x + width, by, by + 27))
            else:
                beneficiary = next((candidate for candidate in window.player_panel.players
                                    if candidate.faction == self.agent_player_faction), None)
                systems = muaat_agent_systems(beneficiary, board) if beneficiary else []
                pages = max(1, (len(systems) + page_size - 1) // page_size)
                self.target_page = min(self.target_page, pages - 1)
                visible = systems[self.target_page * page_size:
                                  (self.target_page + 1) * page_size]
                if not systems:
                    window.text('muaat_agent_no_system',
                                'That player has no War Sun or flagship on the board.',
                                x, bottom + 128, 9, MUTED, width)
                for index, tile in enumerate(visible):
                    ships = [unit.kind for unit in tile.units
                             if unit.owner == beneficiary.faction and
                             unit.kind in ('warsun', 'flagship') and
                             unit.location.region == Region.SPACE]
                    label = f'TILE {tile.number} · {tile.name} · {", ".join(kind.title() for kind in ships)}'
                    by = bottom + 138 - index * 31
                    button(window, ('muaat_agent_system', tile.position), label,
                           x, by, width, 27, size=8)
                    self.hits.append((('use', alias,
                                       ('muaat', beneficiary.faction, tile.position)),
                                      x, x + width, by, by + 27))
        elif alias == 'hacanagent':
            button(window, 'hacan_agent_gain', 'GAIN UP TO 2 COMMODITIES', x,
                   bottom + 138, width, 27, size=8)
            self.hits.append((('use', alias, ('hacan', 'gain')),
                              x, x + width, bottom + 138, bottom + 165))
            beneficiaries = [candidate for candidate in window.player_panel.players
                             if candidate.faction != player.faction]
            pages = max(1, (len(beneficiaries) + page_size - 1) // page_size)
            self.target_page = min(self.target_page, pages - 1)
            visible = beneficiaries[self.target_page * page_size:
                                    (self.target_page + 1) * page_size]
            window.text('hacan_agent_replenish_label', 'OR REPLENISH ANOTHER PLAYER',
                        x, bottom + 122, 8, ACCENT)
            for index, beneficiary in enumerate(visible):
                by = bottom + 88 - index * 31
                button(window, ('hacan_agent_replenish', beneficiary.faction),
                       f'{beneficiary.faction.upper()} · REPLENISH COMMODITIES',
                       x, by, width, 27, size=8)
                self.hits.append((('use', alias,
                                   ('hacan', 'replenish', beneficiary.faction)),
                                  x, x + width, by, by + 27))
        elif alias == 'cabalagent':
            pending = (window.strategy.pending_cabal_replenishments[0]
                       if window.strategy.pending_cabal_replenishments else None)
            target = (next((candidate for candidate in window.player_panel.players
                            if candidate.faction == pending[1]), None)
                      if pending else None)
            choices = (cabal_agent_options(target, board,
                                           commodity_value=pending[2],
                                           captured_by=player)
                       if target and pending else ())
            if target is None:
                window.text('cabal_agent_no_target',
                            'The replenishment reaction is no longer available.',
                            x, bottom + 128, 9, MUTED, width)
            elif not choices:
                window.text('cabal_agent_no_units',
                            f'{target.faction.upper()} has no eligible unit in reinforcements.',
                            x, bottom + 128, 9, MUTED, width)
            else:
                window.text('cabal_agent_target',
                            f'CONVERT {target.commodities} COMMODITIES · '
                            f'UNIT LIMIT {pending[2]}',
                            x, bottom + 166, 9, GOLD, max_width=width)
                pages = max(1, (len(choices) + page_size - 1) // page_size)
                self.target_page = min(self.target_page, pages - 1)
                visible = choices[self.target_page * page_size:
                                  (self.target_page + 1) * page_size]
                for index, kind in enumerate(visible):
                    by = bottom + 132 - index * 31
                    button(window, ('cabal_agent_unit', kind),
                           f'CAPTURE {UNIT_TYPES[kind]["name"].upper()}',
                           x, by, width, 27, size=8)
                    self.hits.append((('use', alias, kind), x, x + width,
                                      by, by + 27))
        elif alias == 'arborecagent':
            if self.agent_ship_id is None:
                targets = [(tile, unit) for tile in board.values() for unit in tile.units
                           if unit.location.region == Region.SPACE and
                           UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter']
                targets.sort(key=lambda row: (row[0].number, row[1].owner,
                                              row[1].kind, row[1].unit_id))
                pages = max(1, (len(targets) + page_size - 1) // page_size)
                self.target_page = min(self.target_page, pages - 1)
                visible = targets[self.target_page * page_size:
                                  (self.target_page + 1) * page_size]
                for index, (tile, ship) in enumerate(visible):
                    by = bottom + 138 - index * 31
                    label = (f'TILE {tile.number} · {ship.owner.upper()} · '
                             f'{UNIT_TYPES[ship.kind]["name"]}')
                    button(window, ('agent_ship_target', ship.unit_id), label,
                           x, by, width, 27, size=8)
                    self.hits.append((('agent_ship_select', ship.unit_id),
                                      x, x + width, by, by + 27))
            else:
                ship = next((unit for tile in board.values() for unit in tile.units
                             if unit.unit_id == self.agent_ship_id), None)
                target = next((candidate for candidate in window.player_panel.players
                               if ship and candidate.faction == ship.owner), None)
                if ship is None or target is None:
                    self.agent_ship_id = None
                    return
                tile = next(tile for tile in board.values() if ship in tile.units)
                window.text(('leader_agent_ship', ship.unit_id),
                            f'{ship.owner.upper()} · {UNIT_TYPES[ship.kind]["name"]} · TILE {tile.number}',
                            x, bottom + 151, 9, GOLD, max_width=width)
                choices = arborec_agent_replacements(target, ship, board)
                for index, kind in enumerate(choices[:4]):
                    by = bottom + 116 - index * 30
                    label = f'REPLACE WITH {UNIT_TYPES[kind]["name"].upper()}'
                    button(window, ('agent_ship_replacement', kind), label,
                           x, by, width, 26, size=8)
                    self.hits.append((('use', alias,
                                       ('arborec', ship.owner, ship.unit_id, kind)),
                                      x, x + width, by, by + 26))
                button(window, 'leader_agent_back', 'BACK TO SHIPS', x,
                       bottom + 20, min(145, width), 25, size=8)
                self.hits.append((('agent_back',), x, x + min(145, width),
                                  bottom + 20, bottom + 45))
        elif alias == 'xxchaagent':
            cards = [card for card in player.planets if card.exhausted]
            pages = max(1, (len(cards) + page_size - 1) // page_size)
            self.target_page = min(self.target_page, pages - 1)
            visible = cards[self.target_page * page_size:
                            (self.target_page + 1) * page_size]
            if self.agent_planet_id is None:
                for index, card in enumerate(visible):
                    by = bottom + 138 - index * 31
                    button(window, ('agent_planet_target', card.planet.planet_id),
                           f'READY · {card.planet.name.upper()}', x, by,
                           width, 27, size=8)
                    self.hits.append((('agent_planet_select', card.planet.planet_id),
                                      x, x + width, by, by + 27))
            else:
                planet_id = self.agent_planet_id
                button(window, 'agent_ready_only', 'READY PLANET ONLY', x,
                       bottom + 132, width, 27, size=8)
                self.hits.append((('use', alias, ('xxcha', planet_id, None)),
                                  x, x + width, bottom + 132, bottom + 159))
                options = xxcha_agent_remove_options(player, board, planet_id)
                for index, unit in enumerate(options[:3]):
                    by = bottom + 98 - index * 30
                    label = f'READY + REMOVE {unit.owner.upper()} INFANTRY'
                    button(window, ('agent_remove_infantry', unit.unit_id), label,
                           x, by, width, 26, size=8)
                    self.hits.append((('use', alias,
                                       ('xxcha', planet_id, unit.unit_id)),
                                      x, x + width, by, by + 26))
                button(window, 'leader_agent_back', 'BACK TO PLANETS', x,
                       bottom + 20, min(145, width), 25, size=8)
                self.hits.append((('agent_back',), x, x + min(145, width),
                                  bottom + 20, bottom + 45))

        if alias in ('muaatagent', 'hacanagent'):
            if self.agent_player_faction is not None:
                button(window, 'leader_agent_back', 'BACK TO PLAYERS', x,
                       bottom + 20, min(140, width), 25, size=8)
                self.hits.append((('agent_back',), x, x + min(140, width),
                                  bottom + 20, bottom + 45))
            if pages > 1:
                for direction, label, bx in ((-1, '‹', x + width - 184),
                                              (1, '›', x + width - 144)):
                    if 0 <= self.target_page + direction < pages:
                        button(window, ('leader_agent_page', direction), label,
                               bx, bottom + 20, 34, 25, size=10)
                        self.hits.append((('target_page', direction), bx, bx + 34,
                                          bottom + 20, bottom + 45))
                window.text('leader_agent_target_page',
                            f'{self.target_page + 1}/{pages}', x + width - 112,
                            bottom + 28, 8, MUTED)
            button(window, 'leader_agent_cancel', 'CANCEL', x + width - 96,
                   bottom + 20, 96, 25, size=8)
            self.hits.append((('agent_cancel',), x + width - 96,
                              x + width, bottom + 20, bottom + 45))
        elif alias == 'argentagent':
            if pages > 1:
                for direction, label, bx in ((-1, '‹', x),
                                              (1, '›', x + width - 34)):
                    if 0 <= self.target_page + direction < pages:
                        button(window, ('leader_agent_page', direction), label,
                               bx, bottom + 20, 30, 25, size=11)
                        self.hits.append((('target_page', direction), bx,
                                          bx + 30, bottom + 20, bottom + 45))
                window.text('leader_agent_target_page',
                            f'{self.target_page + 1}/{pages}',
                            x + width / 2 - 12, bottom + 27, 8, MUTED)
            session = movement.session
            can_apply = bool(session and session.argent_agent_destinations)
            button(window, 'leader_argent_apply', 'USE AGENT',
                   x + width - 204, bottom + 20, 100, 25,
                   enabled=can_apply,
                   primary=True, size=8)
            if can_apply:
                self.hits.append((('use', alias), x + width - 204,
                                  x + width - 104, bottom + 20, bottom + 45))
            button(window, 'leader_argent_decline', 'DECLINE',
                   x + width - 96, bottom + 20, 96, 25, size=8)
            self.hits.append((('agent_cancel',), x + width - 96,
                              x + width, bottom + 20, bottom + 45))
        elif alias == 'arborecagent' and self.agent_ship_id is not None or \
                alias == 'xxchaagent' and self.agent_planet_id is not None:
            button(window, 'leader_agent_cancel', 'CANCEL', x + width - 96,
                   bottom + 20, 96, 25, size=8)
            self.hits.append((('agent_cancel',), x + width - 96,
                              x + width, bottom + 20, bottom + 45))
        elif pages > 1:
            by = bottom + 20
            for direction, label, bx in ((-1, '‹', x), (1, '›', x + width - 34)):
                if 0 <= self.target_page + direction < pages:
                    button(window, ('leader_agent_page', direction), label,
                           bx, by, 30, 25, size=11)
                    self.hits.append((('target_page', direction), bx, bx + 30,
                                      by, by + 25))
            window.text('leader_agent_target_page',
                        f'{self.target_page + 1}/{pages}', x + width / 2 - 12,
                        by + 7, 8, MUTED)
            button(window, 'leader_agent_cancel', 'CANCEL', x + width - 96,
                   bottom + 20, 96, 25, size=8)
            self.hits.append((('agent_cancel',), x + width - 96,
                              x + width, bottom + 20, bottom + 45))
        else:
            cancel_label = 'DECLINE' if alias == 'empyreanagent' else 'CANCEL'
            button(window, 'leader_agent_cancel', cancel_label, x + width - 96,
                   bottom + 20, 96, 25, size=8)
            self.hits.append((('agent_cancel',), x + width - 96,
                              x + width, bottom + 20, bottom + 45))

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
