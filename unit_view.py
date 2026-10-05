from __future__ import annotations

import math
from dataclasses import dataclass

import arcade

from units import Region, UNIT_TYPES, Unit, UnitPlacement, layout_units

COLORS = {"blu": (106, 183, 255), "ylw": (244, 183, 87), "ppl": (185, 148, 248)}


@dataclass
class UnitHit:
    tile_position: tuple[int, int]
    placement: UnitPlacement
    x: float
    y: float
    radius: float


class UnitRenderer:
    def __init__(self):
        self.textures = {}
        self.layouts = {}
        self.labels = {}
        self.hits = []
        self.elapsed = 0.0

    def update(self, delta_time):
        self.elapsed += delta_time

    def placements(self, tile, detailed):
        signature = tuple((u.unit_id, u.kind, u.owner, u.location) for u in tile.units)
        key = tile, detailed
        if key not in self.layouts or self.layouts[key][0] != signature:
            self.layouts[key] = signature, layout_units(tile, detailed)
        return self.layouts[key][1]

    def label(self, key, text, x, y, size=10, color=(235, 242, 255)):
        if key not in self.labels:
            self.labels[key] = arcade.Text('', x, y, color, size, font_name='Arial', anchor_x='center', anchor_y='center', bold=True)
        item = self.labels[key]
        item.text, item.x, item.y, item.font_size, item.color = text, x, y, size, color
        item.draw()

    def draw(self, tile, x, y, width, *, detailed=False, selected=(), hovered=(), interactive=True, scope='main'):
        scale = width / 345
        height = width * 299 / 345
        left, top = x - width / 2, y + height / 2
        if detailed:
            for planet in tile.planets:
                px, py = planet.center
                ground = [u for u in tile.units if u.location.planet_id == planet.planet_id]
                if ground:
                    color = COLORS.get(ground[0].color_code, (160, 185, 210))
                    arcade.draw_circle_outline(left + px * scale, top - py * scale, 56 * scale, (*color, 85), max(1, scale))
        for placement in self.placements(tile, detailed):
            unit = placement.units[0]
            ids = tuple(u.unit_id for u in placement.units)
            cx, cy = left + placement.x * scale, top - placement.y * scale
            pixel_size = max(13, placement.size * scale)
            color = COLORS.get(unit.color_code, (160, 185, 210))
            is_selected = any(i in selected for i in ids)
            is_hovered = any(i in hovered for i in ids)
            if is_selected or is_hovered:
                strength = 115 if is_selected else 65
                pulse = 1 + .035 * math.sin(self.elapsed * 4)
                arcade.draw_circle_filled(cx, cy, pixel_size * .78 * pulse, (*color, 18))
                arcade.draw_circle_outline(cx, cy, pixel_size * .64, (*color, strength), max(1, scale * .65))
            if unit.image_path not in self.textures:
                self.textures[unit.image_path] = arcade.load_texture(unit.image_path)
            texture = self.textures[unit.image_path]
            tw = pixel_size * texture.width / max(texture.width, texture.height)
            th = pixel_size * texture.height / max(texture.width, texture.height)
            arcade.draw_texture_rect(texture, arcade.XYWH(cx + max(1, scale), cy - max(1.5, scale), tw, th), color=arcade.types.Color(0, 0, 0, 145), angle=placement.angle)
            arcade.draw_texture_rect(texture, arcade.XYWH(cx, cy, tw, th), angle=placement.angle)
            if len(placement.units) > 1:
                badge = max(8, min(13, 6 + 3 * scale))
                bx, by = cx + tw * .42, cy - th * .38
                arcade.draw_circle_filled(bx, by, badge, (9, 16, 28, 245))
                arcade.draw_circle_outline(bx, by, badge, (*color, 220), 1)
                self.label((scope, ids, 'count'), str(len(placement.units)), bx, by, min(13, max(10, 8 + scale)))
            if unit.damaged:
                arcade.draw_line(cx - tw / 2, cy + th / 2, cx + tw / 2, cy - th / 2, (245, 110, 100), 2)
            cargo = [u for u in tile.units if u.location.region == Region.TRANSPORT and u.location.carrier_id in ids]
            if cargo and detailed:
                self.label((scope, ids, 'cargo'), f'Cargo {len(cargo)}', cx, cy - pixel_size * .85, 11, color)
            if interactive:
                self.hits.append(UnitHit(tile.position, placement, cx, cy, max(10, pixel_size * .65)))

    def hit_test(self, x, y):
        for hit in reversed(self.hits):
            if math.hypot(hit.x - x, hit.y - y) <= hit.radius:
                return hit
        return None
