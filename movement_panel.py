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

    def draw(self, window, session, left):
        self.hits.clear()
        self.buttons.clear()
        x, width = left + 18, window.sidebar - 36
        target = session.target
        window.text('move_header', 'ACTIVATED SYSTEM', x, window.height - 26, 10, ACCENT)
        window.text('move_target', target.name, x, window.height - 62, 14, INK, width)
        window.text('move_token', f'{session.player.faction.upper()} · Strategic reserve: {session.player.command_pools["strategic"]}',
                    x, window.height - 90, 11, MUTED)
        texture = window.tile_sprites[target].texture
        preview_width = min(250, width)
        preview_height = preview_width * texture.height / texture.width
        arcade.draw_texture_rect(texture, arcade.XYWH(x + width / 2, window.height - 113 - preview_height / 2,
                                                     preview_width, preview_height))
        top, bottom = window.height - 131 - preview_height, 144
        self.viewport = (int(left), bottom, int(window.sidebar), max(1, int(top - bottom)))
        old_scissor = window.ctx.scissor
        window.ctx.scissor = self.viewport
        start = top - 21
        y = start + self.scroll
        try:
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
        window.text('move_summary', f'{ships} ships · {cargo} passengers selected', x, 118, 12, INK)
        split = x + width * .64
        for action, label, lo, hi, color in (
                ('confirm', f'Move {ships} ships' if ships else 'Finish activation', x, split - 6, (30, 88, 105)),
                ('cancel', 'Cancel', split, x + width, CARD)):
            arcade.draw_lrbt_rectangle_filled(lo, hi, 65, 100, color)
            window.text(('move_button', action), label, lo + 10, 77, 12, INK)
            self.buttons.append(((action,), lo, hi, 65, 100))
        window.text('move_help', 'Select ships, then passengers.\nCtrl+Z: undo activation / last move', x, 29, 11, MUTED, width)
