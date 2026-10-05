from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import arcade

from board import Board, Tile, load_board
from units import Region, UNIT_TYPES
from unit_view import UnitRenderer
from system_panel import SystemPanel

ROOT = Path(__file__).resolve().parent
DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
BG = (7, 12, 23)
PANEL = (14, 23, 38)
INK = (223, 232, 244)
MUTED = (130, 151, 177)
ACCENT = (100, 207, 224)


def world(position):
    q, r = position
    return 1.5 * q, math.sqrt(3) * (r + q / 2)


def nearest_hex(x, y):
    q = 2 * x / 3
    r = y / math.sqrt(3) - q / 2
    s = -q - r
    iq, ir, iz = round(q), round(r), round(s)
    errors = [abs(iq - q), abs(ir - r), abs(iz - s)]
    if errors[0] > errors[1] and errors[0] > errors[2]:
        iq = -ir - iz
    elif errors[1] > errors[2]:
        ir = -iq - iz
    return iq, ir


class TileSprite(arcade.Sprite):
    def __init__(self, tile: Tile):
        super().__init__(tile.image_path)
        self.tile = tile


class BoardWindow(arcade.Window):
    def __init__(self, smoke=False, map_path=ROOT / 'maps/three_player.json'):
        super().__init__(1440, 960, 'Twilight Imperium IV — three-player board', resizable=True, vsync=True)
        self.map_config, self.board = load_board(map_path)
        self.tile_sprites = {tile: TileSprite(tile) for tile in self.board.values()}
        self.labels = {}
        self.unit_renderer = UnitRenderer()
        self.system_panel = SystemPanel()
        self.selected_units = ()
        self.hovered_units = ()
        self.focus_view = False
        self.focus_zoom = 1.0
        self.last_click = (None, 0.0)
        self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
        self.hover = None
        self.zoom = self.target_zoom = 1.0
        self.map_center = [0.0, 0.0]
        self.frames = 0
        self.smoke = smoke
        rng = random.Random(4)
        self.stars = [(rng.random(), rng.random(), rng.randrange(45, 100)) for _ in range(190)]
        self.fit()

    @property
    def sidebar(self):
        return min(420, max(340, self.width * .29))

    @property
    def viewport_center(self):
        return (self.width - self.sidebar) / 2, (self.height - 80) / 2

    def fit(self):
        coords = [world(p) for p in self.board]
        xs, ys = zip(*coords)
        self.fit_scale = min((self.width - self.sidebar - 80) / (max(xs) - min(xs) + 2), (self.height - 180) / (max(ys) - min(ys) + math.sqrt(3)))
        self.map_center = [(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2]
        self.zoom = self.target_zoom = 1.0

    def screen(self, position):
        x, y = world(position)
        cx, cy = self.viewport_center
        scale = self.fit_scale * self.zoom
        return cx + (x - self.map_center[0]) * scale, cy + (y - self.map_center[1]) * scale

    def pick(self, x, y):
        if x >= self.width - self.sidebar or y > self.height - 80:
            return None
        cx, cy = self.viewport_center
        scale = self.fit_scale * self.zoom
        position = nearest_hex((x - cx) / scale + self.map_center[0], (y - cy) / scale + self.map_center[1])
        return position if position in self.board else None

    def text(self, key, value, x, y, size=14, color=INK, width=None):
        if key not in self.labels:
            self.labels[key] = arcade.Text('', x, y, color, size, font_name='Arial', width=width, multiline=width is not None)
        label = self.labels[key]
        label.text, label.x, label.y, label.color = value, x, y, color
        if width is not None:
            label.width = width
        label.draw()

    def outline(self, position, color, thickness):
        x, y = self.screen(position)
        radius = self.fit_scale * self.zoom * .98
        points = [(x + radius * math.cos(math.pi * i / 3), y + radius * math.sin(math.pi * i / 3)) for i in range(6)]
        arcade.draw_polygon_outline(points, color, thickness)

    def draw_tile(self, tile, x, y, width, detailed=False, scope='main', interactive=True):
        texture = self.tile_sprites[tile].texture
        arcade.draw_texture_rect(texture, arcade.XYWH(x, y, width, width * texture.height / texture.width))
        self.unit_renderer.draw(tile, x, y, width, detailed=detailed, selected=self.selected_units, hovered=self.hovered_units, interactive=interactive, scope=scope)

    def toggle_focus(self):
        self.focus_view = not self.focus_view
        self.focus_zoom = 1.0
        self.hovered_units = ()

    def on_draw(self):
        self.clear(BG)
        for sx, sy, brightness in self.stars:
            arcade.draw_point(sx * (self.width - self.sidebar), sy * (self.height - 80), (brightness, brightness, brightness + 20), 1)
        radius = self.fit_scale * self.zoom
        if self.selected not in self.board:
            self.selected = next(iter(self.board))
        self.tile_sprites = {tile: self.tile_sprites.get(tile) or TileSprite(tile) for tile in self.board.values()}
        self.unit_renderer.hits.clear()
        if self.focus_view:
            tile = self.board[self.selected]
            cx, cy = self.viewport_center
            width = min(self.width - self.sidebar - 90, (self.height - 200) * 345 / 299) * self.focus_zoom
            self.draw_tile(tile, cx, cy + 15, width, detailed=True)
        else:
            for position, tile in self.board.items():
                x, y = self.screen(position)
                self.draw_tile(tile, x, y, radius * 2)
                if tile.player is not None:
                    self.outline(position, tuple(tile.color), 2)
            if self.hover is not None and self.hover != self.selected:
                self.outline(self.hover, (170, 185, 207), 2)
            self.outline(self.selected, ACCENT, 3)
        left = self.width - self.sidebar
        arcade.draw_lrbt_rectangle_filled(left, self.width, 0, self.height, PANEL)
        arcade.draw_lrbt_rectangle_filled(0, left, self.height - 80, self.height, (10, 17, 29))
        self.text('title', 'TWILIGHT IMPERIUM IV', 30, self.height - 34, 21)
        self.text('subtitle', f'Game board · {len(self.board.home_tiles)} players · {len(self.board)} systems', 30, self.height - 61, 12, MUTED)
        self.text('zoom', f'{self.focus_zoom if self.focus_view else self.zoom:.1f}×', left - 62, self.height - 45, 13, ACCENT)
        arcade.draw_lrbt_rectangle_filled(left - 225, left - 85, self.height - 56, self.height - 24, (25, 45, 63))
        self.text('focus_button', 'Galaxy view' if self.focus_view else 'Detail view', left - 212, self.height - 46, 12, ACCENT)
        self.system_panel.draw(self, self.board[self.selected], left)
        self.text('status', f'{len(self.board)} systems  /  {len(self.board.home_tiles)} home systems', 24, 21, 12, MUTED)

    def on_update(self, delta_time):
        self.unit_renderer.update(delta_time)
        self.zoom += (self.target_zoom - self.zoom) * min(1, delta_time * 14)
        self.frames += 1
        if self.smoke and self.frames == 5:
            for p in self.board:
                assert self.pick(*self.screen(p)) == p, f'Picking failed: {p}'
            for p in self.board:
                x, y = self.screen(p)
                self.last_click = (None, 0.0)
                self.unit_renderer.hits.clear()
                self.on_mouse_press(x, y, arcade.MOUSE_BUTTON_LEFT, 0)
                assert self.selected == p
            assert self.pick(self.width - 10, 100) is None
            self.on_mouse_scroll(100, 100, 0, 100)
            assert self.target_zoom == 3.5
            self.on_mouse_scroll(100, 100, 0, -100)
            assert self.target_zoom == .55
            self.fit()
            previous = tuple(self.map_center)
            self.on_mouse_drag(100, 100, 30, 20, arcade.MOUSE_BUTTON_RIGHT, 0)
            assert tuple(self.map_center) != previous
            self.fit()
            self.selected = self.board.home_tiles[0].position if self.board.home_tiles else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(ROOT / 'board-home-preview.png')
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(ROOT / 'board-preview.png')
            for tile in self.board.home_tiles:
                if tile.units:
                    self.focus_view = True
                    self.selected = tile.position
                    self.system_panel.reset()
                    self.on_draw()
                    row = self.system_panel.hits[0]
                    self.on_mouse_press((row.left + row.right) / 2, (row.bottom + row.top) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert set(self.selected_units) == {u.unit_id for u in row.units}
                    assert self.unit_renderer.hits
                    hit = self.unit_renderer.hits[0]
                    self.selected_units = tuple(u.unit_id for u in hit.placement.units)
                    assert self.unit_renderer.hit_test(hit.x, hit.y) is not None
                    self.on_draw()
                    arcade.get_image().save(ROOT / f'unit-preview-{tile.units[0].owner}.png')
                    if self.system_panel.scroll_max:
                        self.on_mouse_scroll(self.width - 30, self.height / 2, 0, -3)
                        assert self.system_panel.scroll > 0
                        self.on_draw()
                        arcade.get_image().save(ROOT / f'unit-preview-{tile.units[0].owner}-scrolled.png')
                        self.system_panel.reset()

            self.focus_view = False
            self.selected_units = ()
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(ROOT / 'board-preview.png')
            print(f'PASS: {len(self.board)} independent tile objects and sprites; selection, zoom and pan checked')
            self.close()

    def on_mouse_motion(self, x, y, dx, dy):
        self.hover = None if self.focus_view else self.pick(x, y)
        hit = self.unit_renderer.hit_test(x, y) if x < self.width - self.sidebar and y < self.height - 80 else None
        inventory_hit = self.system_panel.hit_test(x, y)
        self.hovered_units = tuple(u.unit_id for u in hit.placement.units) if hit else tuple(u.unit_id for u in inventory_hit.units) if inventory_hit else ()

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        left = self.width - self.sidebar
        if left - 225 <= x <= left - 85 and self.height - 56 <= y <= self.height - 24:
            self.toggle_focus()
            return
        if x >= left:
            inventory_hit = self.system_panel.hit_test(x, y)
            if inventory_hit:
                self.selected_units = tuple(u.unit_id for u in inventory_hit.units)
            return
        if y >= self.height - 80:
            return
        hit = self.unit_renderer.hit_test(x, y)
        picked = hit.tile_position if hit else self.selected if self.focus_view else self.pick(x, y)
        if picked is not None:
            if picked != self.selected:
                self.selected_units = ()
                self.system_panel.reset()
            self.selected = picked
            if hit:
                self.selected_units = tuple(u.unit_id for u in hit.placement.units)
            elif not self.focus_view:
                self.selected_units = ()
            now = time.monotonic()
            if self.last_click[0] == picked and now - self.last_click[1] < .33:
                self.toggle_focus()
                self.last_click = (None, 0.0)
            else:
                self.last_click = (picked, now)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if not self.focus_view and buttons & (arcade.MOUSE_BUTTON_RIGHT | arcade.MOUSE_BUTTON_MIDDLE) and x < self.width - self.sidebar:
            scale = self.fit_scale * self.zoom
            self.map_center[0] -= dx / scale
            self.map_center[1] -= dy / scale

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if x >= self.width - self.sidebar:
            self.system_panel.scroll -= scroll_y * 40
            self.system_panel.clamp()
        elif self.focus_view and x < self.width - self.sidebar:
            self.focus_zoom = max(.7, min(1.4, self.focus_zoom * 1.1 ** scroll_y))
        elif x < self.width - self.sidebar:
            self.target_zoom = min(3.5, max(.55, self.target_zoom * 1.15 ** scroll_y))

    def on_key_press(self, symbol, modifiers):
        if symbol == arcade.key.SPACE:
            self.toggle_focus()
        elif symbol == arcade.key.ESCAPE:
            self.focus_view = False
        elif symbol == arcade.key.F:
            self.focus_view = False
            self.fit()

    def on_resize(self, width, height):
        super().on_resize(width, height)
        if hasattr(self, 'board'):
            self.fit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--map', type=Path, default=ROOT / 'maps/three_player.json')
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    if args.validate:
        _, board = load_board(args.map)
        for position in board:
            assert nearest_hex(*world(position)) == position
        print(f'PASS: {len(board)} tile objects, unique coordinates, all images present')
        return
    BoardWindow(smoke=args.smoke_test, map_path=args.map)
    arcade.run()


if __name__ == '__main__':
    main()
