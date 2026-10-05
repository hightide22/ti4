from __future__ import annotations

import math
from dataclasses import dataclass

import arcade

from units import UNIT_TYPES, Unit, system_inventory

INK = (223, 232, 244)
MUTED = (130, 151, 177)
ACCENT = (100, 207, 224)
CARD = (20, 33, 51)
PLANET_TYPES = {"CULTURAL": "Cultural", "INDUSTRIAL": "Industrial", "HAZARDOUS": "Hazardous", "FACTION": "Homeworld", "MR": "Mecatol Rex"}
ORDER = ("warsun", "flagship", "dreadnought", "carrier", "cruiser", "destroyer", "fighter", "infantry", "mech", "pds", "spacedock")


@dataclass
class InventoryHit:
    units: tuple[Unit, ...]
    left: float
    right: float
    bottom: float
    top: float


class SystemPanel:
    def __init__(self):
        self.scroll = 0
        self.scroll_max = 0
        self.hits = []
        self.viewport = (0, 0, 0, 0)

    def reset(self):
        self.scroll = 0

    def clamp(self):
        self.scroll = max(0, min(self.scroll, self.scroll_max))

    def hit_test(self, x, y):
        vx, vy, vw, vh = self.viewport
        if not (vx <= x <= vx + vw and vy <= y <= vy + vh):
            return None
        return next((h for h in self.hits if h.left <= x <= h.right and h.bottom <= y <= h.top), None)

    def draw_counts(self, window, key, groups, x, y, width):
        if not groups:
            window.text((key, 'empty'), 'No units', x + 12, y - 23, 12, MUTED)
            return y - 45
        kinds = [kind for kind in ORDER if kind in groups]
        columns = 2
        cell_width = (width - 10) / columns
        for index, kind in enumerate(kinds):
            row, column = divmod(index, columns)
            cx, top = x + column * (cell_width + 10), y - row * 57
            members = tuple(groups[kind])
            selected = any(u.unit_id in window.selected_units for u in members)
            arcade.draw_lrbt_rectangle_filled(cx, cx + cell_width, top - 50, top, (28, 52, 69) if selected else CARD)
            unit = members[0]
            renderer = window.unit_renderer
            if unit.image_path not in renderer.textures:
                renderer.textures[unit.image_path] = arcade.load_texture(unit.image_path)
            texture = renderer.textures[unit.image_path]
            size = 33
            tw, th = size * texture.width / max(texture.width, texture.height), size * texture.height / max(texture.width, texture.height)
            arcade.draw_texture_rect(texture, arcade.XYWH(cx + 25, top - 25, tw, th))
            label = 'Space dock' if kind == 'spacedock' else UNIT_TYPES[kind]['name']
            window.text((key, kind, 'name'), label, cx + 50, top - 20, 11, INK)
            window.text((key, kind, 'count'), str(len(members)), cx + 50, top - 40, 16, ACCENT)
            self.hits.append(InventoryHit(members, cx, cx + cell_width, top - 50, top))
        return y - math.ceil(len(kinds) / columns) * 57

    def draw(self, window, tile, left):
        px, width = left + 24, window.sidebar - 48
        text = window.text
        text('system_label', 'SELECTED SYSTEM', px, window.height - 36, 11, MUTED)
        text('system_name', tile.name.split(' - ')[0], px, window.height - 74, 18, INK, width)
        text('system_id', f'Tile {tile.system_id} · {len(tile.units)} units in system', px, window.height - 100, 12, MUTED)
        if tile.player:
            text('owner', tile.player, px, window.height - 128, 12, tuple(tile.color))
        inventory = system_inventory(tile)
        top, bottom = window.height - 154, 166
        self.viewport = (int(left), bottom, int(window.sidebar), max(1, int(top - bottom)))
        self.hits.clear()
        previous_scissor = window.ctx.scissor
        window.ctx.scissor = self.viewport
        try:
            start = window.height - 179
            y = start + self.scroll
            preview_width = min(width - 20, 230)
            preview_height = preview_width * 299 / 345
            arcade.draw_lrbt_rectangle_filled(px, px + width, y - preview_height - 16, y + 8, CARD)
            texture = window.tile_sprites[tile].texture
            arcade.draw_texture_rect(texture, arcade.XYWH(
                px + width / 2, y - preview_height / 2 - 4,
                preview_width, preview_height))
            y -= preview_height + 44
            text('fleet_title', 'SPACE FLEET', px, y, 12, ACCENT)
            y = self.draw_counts(window, 'fleet', inventory['fleet'], px, y - 15, width) - 18
            if inventory['cargo']:
                text('cargo_title', 'TRANSPORTED UNITS', px, y, 11, MUTED)
                y = self.draw_counts(window, 'cargo', inventory['cargo'], px, y - 15, width) - 18
            for planet in tile.planets:
                arcade.draw_line(px, y + 6, px + width, y + 6, (41, 60, 80), 1)
                text((planet.planet_id, 'name'), planet.name, px, y - 19, 17, INK)
                label = PLANET_TYPES.get(planet.planet_type, planet.planet_type or '')
                text((planet.planet_id, 'type'), label, px + width - 115, y - 18, 10, MUTED)
                text((planet.planet_id, 'values'), f'Resources: {planet.resources}   Influence: {planet.influence}', px, y - 45, 12, ACCENT)
                y = self.draw_counts(window, planet.planet_id, inventory['planets'][planet.planet_id], px, y - 58, width) - 16
            if not tile.planets:
                text('no_planets', 'No planets in this system', px, y - 18, 13, MUTED)
                y -= 45
            if tile.wormholes:
                text('wormholes', 'Wormholes: ' + ', '.join(tile.wormholes), px, y - 18, 12, ACCENT)
                y -= 40
            self.scroll_max = max(0, start - (y - self.scroll) - (top - bottom) + 35)
            self.clamp()
        finally:
            window.ctx.scissor = previous_scissor
        if self.scroll_max:
            track_top = top - 5
            arcade.draw_line(window.width - 9, bottom, window.width - 9, track_top, (39, 54, 75), 2)
            thumb = max(35, (track_top - bottom) ** 2 / (track_top - bottom + self.scroll_max))
            thumb_top = track_top - self.scroll / self.scroll_max * (track_top - bottom - thumb)
            arcade.draw_lrbt_rectangle_filled(window.width - 12, window.width - 6, thumb_top - thumb, thumb_top, ACCENT)
        arcade.draw_line(px, 154, px + width, 154, (41, 60, 80), 1)
        picked = next((u for u in tile.units if u.unit_id in window.selected_units), None)
        if picked:
            location = next((p.name for p in tile.planets if p.planet_id == picked.location.planet_id), 'Space')
            count = sum(u.unit_id in window.selected_units for u in tile.units)
            text('selection', f'{UNIT_TYPES[picked.kind]["name"]}: {count} · {location}', px, 126, 12, ACCENT)
        else:
            text('selection_tip', 'Select a unit or an inventory row', px, 126, 12, MUTED)
        text('help', 'Space / double-click — detail view\nMouse wheel over panel — scroll\nRight drag — pan · F — fit board', px, 90, 11, MUTED, width)
