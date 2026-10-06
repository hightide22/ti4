from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from types import SimpleNamespace

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
        self.layout_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='unit-layout')
        self.pending_layouts = {}
        self.blocking_layouts = False

    @property
    def is_loading(self):
        return any(not future.done() for _, future in self.pending_layouts.values())

    def update(self, delta_time):
        self.elapsed += delta_time

    def placements(self, tile, detailed):
        # Layout runs on a worker while gameplay can move or destroy units. Give
        # the worker a consistent snapshot instead of the live mutable tile.
        units = tuple(replace(unit) for unit in tuple(tile.units))
        signature = tuple((u.unit_id, u.kind, u.owner, u.location) for u in units)
        key = tile, detailed
        cached = self.layouts.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        pending = self.pending_layouts.get(key)
        if pending and pending[0] == signature:
            future = pending[1]
            if self.blocking_layouts:
                placements = future.result()
            elif not future.done():
                return ()
            else:
                placements = future.result()
            self.layouts[key] = signature, placements
            self.pending_layouts.pop(key, None)
            return placements
        if pending:
            pending[1].cancel()
        layout_tile = SimpleNamespace(number=tile.number, planets=tile.planets, units=units)
        future = self.layout_executor.submit(layout_units, layout_tile, detailed)
        self.pending_layouts[key] = signature, future
        if self.blocking_layouts:
            placements = future.result()
            self.layouts[key] = signature, placements
            self.pending_layouts.pop(key, None)
            return placements
        return ()

    def label(self, key, text, x, y, size=10, color=(235, 242, 255)):
        if key not in self.labels:
            self.labels[key] = arcade.Text('', x, y, color, size, font_name='Arial', anchor_x='center', anchor_y='center', bold=True)
        item = self.labels[key]
        item.text, item.x, item.y, item.font_size, item.color = text, x, y, size, color
        item.draw()

    def draw_cargo(self, tile, placement, cx, cy, pixel_size, selected):
        carrier_ids = {unit.unit_id for unit in placement.units}
        if not carrier_ids:
            return
        cargo = [unit for unit in tile.units
                 if unit.location.region == Region.TRANSPORT and unit.location.carrier_id in carrier_ids]
        if not cargo:
            return
        columns = min(3, len(cargo))
        rows = math.ceil(len(cargo) / columns)
        icon_size = max(7, min(15, pixel_size * .34))
        start_x = cx + pixel_size * .48 + icon_size * .55
        for index, unit in enumerate(cargo):
            row, col = divmod(index, columns)
            icon_x = start_x + col * icon_size * 1.18
            icon_y = cy + (rows - 1) * icon_size * .59 - row * icon_size * 1.18
            color = COLORS.get(unit.color_code, (160, 185, 210))
            arcade.draw_circle_filled(icon_x, icon_y, icon_size * .57, (9, 16, 28, 235))
            arcade.draw_circle_outline(icon_x, icon_y, icon_size * .57,
                                       (*color, 230 if unit.unit_id in selected else 150),
                                       max(1, icon_size * .09))
            if unit.image_path not in self.textures:
                self.textures[unit.image_path] = arcade.load_texture(unit.image_path)
            texture = self.textures[unit.image_path]
            tw = icon_size * texture.width / max(texture.width, texture.height)
            th = icon_size * texture.height / max(texture.width, texture.height)
            arcade.draw_texture_rect(texture, arcade.XYWH(icon_x, icon_y, tw, th))

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
        placements = self.placements(tile, detailed)
        carrier_placements = []
        cargo_carrier_ids = {unit.location.carrier_id for unit in tile.units
                             if unit.location.region == Region.TRANSPORT}
        for placement in placements:
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
            if any(member.unit_id in cargo_carrier_ids for member in placement.units):
                carrier_placements.append((placement, cx, cy, pixel_size))
            if placement.badge_count is not None or len(placement.units) > 1:
                badge = max(8, min(13, 6 + 3 * scale))
                bx, by = cx + tw * .42, cy - th * .38
                arcade.draw_circle_filled(bx, by, badge, (9, 16, 28, 245))
                arcade.draw_circle_outline(bx, by, badge, (*color, 220), 1)
                self.label((scope, ids, 'count'), str(placement.badge_count or len(placement.units)), bx, by, min(13, max(10, 8 + scale)))
            if unit.damaged:
                arcade.draw_line(cx - tw / 2, cy + th / 2, cx + tw / 2, cy - th / 2, (245, 110, 100), 2)
            cargo = [u for u in tile.units if u.location.region == Region.TRANSPORT and u.location.carrier_id in ids]
            if cargo and detailed:
                self.label((scope, ids, 'cargo'), f'Cargo {len(cargo)}', cx, cy - pixel_size * .85, 11, color)
            if interactive:
                self.hits.append(UnitHit(tile.position, placement, cx, cy, max(10, pixel_size * .65)))
        for placement, cx, cy, pixel_size in carrier_placements:
            self.draw_cargo(tile, placement, cx, cy, pixel_size, selected)

    def hit_test(self, x, y):
        for hit in reversed(self.hits):
            if math.hypot(hit.x - x, hit.y - y) <= hit.radius:
                return hit
        return None
