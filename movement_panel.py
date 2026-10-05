from __future__ import annotations

import math
import arcade

from units import UNIT_TYPES, Region

INK = (223, 232, 244)
MUTED = (132, 154, 180)
ACCENT = (100, 207, 224)
CARD = (23, 39, 57)


class MovementPanel:
    def __init__(self):
        self.scroll = 0
        self.scroll_max = 0
        self.hits = []
        self.buttons = []
        self.viewport = (0, 0, 0, 0)

    def reset(self):
        self.scroll = 0

    def scroll_by(self, amount):
        self.scroll = max(0, min(self.scroll_max, self.scroll + amount))

    def hit_test(self, x, y):
        for action, left, right, bottom, top in self.buttons:
            if left <= x <= right and bottom <= y <= top:
                return action
        vx, vy, vw, vh = self.viewport
        if vx <= x <= vx + vw and vy <= y <= vy + vh:
            return next((action for action, left, right, bottom, top in self.hits
                         if left <= x <= right and bottom <= y <= top), None)
        return None

    def rows(self, window, session, source, units, x, y, width, passenger=False):
        cell = (width - 8) / 2
        counts = {}
        for index, unit in enumerate(units):
            row, col = divmod(index, 2)
            left, top = x + col * (cell + 8), y - row * 48
            selected = unit.unit_id in session.selected
            arcade.draw_lrbt_rectangle_filled(left, left + cell, top - 43, top,
                                             (32, 69, 82) if selected else CARD)
            if selected:
                arcade.draw_lrbt_rectangle_outline(left, left + cell, top - 43, top, ACCENT, 1)
            window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[unit.kind]["sprite"]}.png',
                                      left + 17, top - 21, 25)
            counts[unit.kind] = counts.get(unit.kind, 0) + 1
            name = f'{UNIT_TYPES[unit.kind]["name"]} {counts[unit.kind]}'
            if passenger:
                detail = next((p.name for p in source.tile.planets if p.planet_id == unit.location.planet_id), 'Space')
            else:
                detail = f'Move {unit.move_value} · Capacity {unit.capacity}'
            window.text(('movement_name', unit.unit_id), name, left + 33, top - 17, 11, INK)
            window.text(('movement_detail', unit.unit_id), detail, left + 33, top - 34, 10, MUTED)
            self.hits.append((('unit', unit.unit_id), left, left + cell, top - 43, top))
        return y - math.ceil(len(units) / 2) * 48

    def timeline(self, window, session, x, y, width):
        stage = session.stage
        movement_done = stage != 'movement'
        invasion_done = stage == 'complete'
        ships_selected = any(session.ships(source) for source in session.sources.values())
        landing_assigned = any(planet_id is not None for planet_id in session.landings.values())
        has_forces = bool(session.landings)
        invasion_current = stage == 'invasion'
        rows = (
            ('STEP 1 · ACTIVATION', 'Done'),
            ('STEP 2 · MOVEMENT', 'Done' if movement_done else 'In progress'),
            ('  Move Ships', ('Done' if ships_selected else 'Skipped') if movement_done else 'Current'),
            ('  Space Cannon Offense', 'Skipped'),
            ('STEP 3 · SPACE COMBAT', 'Skipped'),
            ('STEP 4 · INVASION', 'Done' if invasion_done else ('In progress' if invasion_current and has_forces else ('Current' if invasion_current else 'Waiting'))),
            ('  Bombardment', 'Skipped'),
            ('  Commit Ground Forces', ('Done' if landing_assigned else 'Skipped') if invasion_done else ('Current' if invasion_current and has_forces else 'Skipped')),
            ('  Space Cannon Defense', 'Skipped'),
            ('  Ground Combat', 'Skipped'),
            ('  Establish Control', 'Done' if invasion_done and landing_assigned else 'Skipped'),
        )
        for index, (label, status) in enumerate(rows):
            current = status == 'Current'
            if current:
                arcade.draw_lrbt_rectangle_filled(x - 5, x + width, y - 3, y + 13, (36, 69, 91))
            color = (255, 207, 107) if current else (ACCENT if status in ('Done', 'In progress') else MUTED)
            window.text(('action_step', index), label, x, y, 9 if label.startswith('  ') else 10, color, width * .61)
            window.text(('action_status', index), status, x + width * .64, y, 9, color, width * .36)
            y -= 15
        return y - 6

    def draw(self, window, session, left):
        self.hits.clear()
        self.buttons.clear()
        x, width = left + 18, window.sidebar - 36
        target = session.target
        window.text('move_header', 'ACTIVATED SYSTEM', x, window.height - 26, 10, ACCENT)
        window.text('move_target', target.name, x, window.height - 62, 14, INK, width)
        window.text('move_token', f'{session.player.faction.upper()} · Tactical reserve: {session.player.command_pools["tactical"]}',
                    x, window.height - 90, 11, MUTED)
        top, bottom = window.height - 250, 144
        content_y = self.timeline(window, session, x, window.height - 119, width)
        self.viewport = (int(left), bottom, int(window.sidebar), max(1, int(top - bottom)))
        old_scissor = window.ctx.scissor
        window.ctx.scissor = self.viewport
        start = min(top - 18, content_y)
        y = start + self.scroll
        try:
            if session.stage == 'movement':
                window.text('move_sources', f'SOURCES IN RANGE · {len(session.sources)}', x, y, 11, ACCENT)
                y -= 28
                if not session.sources:
                    window.text('move_empty', 'No ships can reach this system', x, y - 15, 12, MUTED)
                    y -= 45
                for source in session.sources.values():
                    title = f'Tile {source.tile.system_id} · {source.tile.name.split("/")[0]}'
                    window.text(('move_source', source.tile.position), title, x, y, 13, INK)
                    y = self.rows(window, session, source, source.ships, x, y - 13, width) - 8
                    used, capacity = session.cargo_values(source)
                    window.text(('move_capacity', source.tile.position), f'Cargo {used}/{capacity} · choose passengers', x, y - 14, 11, ACCENT)
                    y -= 27
                    if source.passengers:
                        y = self.rows(window, session, source, source.passengers, x, y, width, passenger=True)
                    carried = session.carried(source)
                    if carried:
                        window.text(('move_aboard', source.tile.position), f'Already aboard: {len(carried)} (included)', x, y - 16, 11, MUTED)
                        y -= 26
                    y -= 23
                    arcade.draw_line(x, y + 12, x + width, y + 12, (41, 60, 80), 1)
            elif session.stage == 'invasion':
                forces = window.movement.landing_forces(session)
                window.text('landing_title', 'COMMIT GROUND FORCES', x, y, 11, ACCENT)
                y -= 28
                if not forces:
                    window.text('landing_empty', 'No transported infantry or mechs can land.', x, y - 10, 11, MUTED, width)
                    y -= 38
                for unit in forces:
                    planet_id = session.landings.get(unit.unit_id)
                    planet = next((p for p in target.planets if p.planet_id == planet_id), None)
                    label = planet.name if planet else 'Keep aboard'
                    top = y
                    arcade.draw_lrbt_rectangle_filled(x, x + width, top - 38, top, CARD)
                    window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[unit.kind]["sprite"]}.png', x + 17, top - 19, 24)
                    carrier = next((u for u in target.units if u.unit_id == unit.location.carrier_id), None)
                    window.text(('landing_unit', unit.unit_id), f'{UNIT_TYPES[unit.kind]["name"]} · {carrier.kind.title() if carrier else "Transport"}', x + 34, top - 14, 10, INK)
                    window.text(('landing_planet', unit.unit_id), f'Land on: {label}  ›', x + 34, top - 30, 10, ACCENT)
                    self.hits.append((('landing', unit.unit_id), x, x + width, top - 38, top))
                    y -= 44
                window.text('landing_help', 'Click a force to cycle through planets or keep it aboard.', x, y - 4, 10, MUTED, width)
                y -= 25
            else:
                window.text('action_complete', 'TACTICAL ACTION COMPLETE', x, y, 12, ACCENT)
                window.text('action_result', session.outcome, x, y - 27, 11, INK, width)
                y -= 70
            self.scroll_max = max(0, start - (y - self.scroll) - (top - bottom) + 40)
            self.scroll_by(0)
        finally:
            window.ctx.scissor = old_scissor
        if self.scroll_max:
            track = top - bottom
            thumb = max(28, track * track / (track + self.scroll_max))
            thumb_top = top - self.scroll / self.scroll_max * (track - thumb)
            arcade.draw_lrbt_rectangle_filled(window.width - 10, window.width - 6, thumb_top - thumb, thumb_top, ACCENT)
        ships = sum(len(session.ships(source)) for source in session.sources.values())
        cargo = sum(session.cargo_values(source)[0] for source in session.sources.values())
        landed = sum(planet_id is not None for planet_id in session.landings.values())
        summary = (f'{ships} ships · {cargo} passengers selected' if session.stage == 'movement' else
                   f'{landed} ground force{"s" if landed != 1 else ""} assigned to planet{"s" if landed != 1 else ""}')
        window.text('move_summary', summary, x, 118, 11, INK, width)
        split = x + width * .64
        if session.stage == 'movement':
            actions = (('confirm', f'Move {ships} ships' if ships else 'Finish movement', x, split - 6, (30, 88, 105)),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'invasion':
            landing_assigned = any(planet_id is not None for planet_id in session.landings.values())
            actions = (('establish', 'Land forces' if landing_assigned else 'Skip invasion', x, split - 6, (30, 88, 105)),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'complete':
            actions = (('done', 'Done', x, split - 6, (30, 88, 105)),
                       ('cancel', 'Cancel', split, x + width, CARD))
        else:
            actions = (('continue', 'Continue', x, split - 6, (30, 88, 105)),
                       ('cancel', 'Cancel', split, x + width, CARD))
        for action, label, lo, hi, color in actions:
            arcade.draw_lrbt_rectangle_filled(lo, hi, 65, 100, color)
            window.text(('move_button', action), label, lo + 10, 77, 12, INK)
            self.buttons.append(((action,), lo, hi, 65, 100))
        window.text('move_help', 'Ctrl+Z cancels the full tactical action', x, 29, 10, MUTED, width)
