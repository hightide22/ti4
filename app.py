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
from player import PlanetCard, create_players
from player_panel import PlayerPanel
from movement import MovementController, MovementError
from movement_panel import MovementPanel

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
        display_width, display_height = arcade.get_display_size()
        super().__init__(min(1440, display_width), min(960, display_height),
                         'Twilight Imperium IV — three-player board', resizable=True, vsync=True)
        self.set_minimum_size(min(1120, display_width), min(720, display_height))
        self.maximize()
        self.map_config, self.board = load_board(map_path)
        self.tile_sprites = {tile: TileSprite(tile) for tile in self.board.values()}
        self.labels = {}
        self.unit_renderer = UnitRenderer()
        self.unit_renderer.blocking_layouts = smoke
        self.system_panel = SystemPanel()
        self.player_panel = PlayerPanel(create_players(self.board, self.map_config))
        self.movement = MovementController(self.board, self.player_panel.players)
        self.movement_panel = MovementPanel()
        self.movement_error = None
        self.token_hits = []
        self.token_context = None
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
        return min(340, max(320, self.width * .24))

    @property
    def viewport_center(self):
        return (self.width - self.sidebar) / 2, self.player_panel.HEIGHT + (self.height - 80 - self.player_panel.HEIGHT) / 2

    def fit(self):
        coords = [world(p) for p in self.board]
        xs, ys = zip(*coords)
        self.fit_scale = min((self.width - self.sidebar - 80) / (max(xs) - min(xs) + 2), (self.height - 180 - self.player_panel.HEIGHT) / (max(ys) - min(ys) + math.sqrt(3)))
        self.map_center = [(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2]
        self.zoom = self.target_zoom = 1.0

    def screen(self, position):
        x, y = world(position)
        cx, cy = self.viewport_center
        scale = self.fit_scale * self.zoom
        return cx + (x - self.map_center[0]) * scale, cy + (y - self.map_center[1]) * scale

    def pick(self, x, y):
        if x >= self.width - self.sidebar or y > self.height - 80 or y < self.player_panel.HEIGHT:
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
        scale = width / 345
        tile_left, tile_top = x - width / 2, y + width * 299 / 345 / 2
        for planet in tile.planets:
            faction = tile.planet_owners.get(planet.planet_id)
            owner = next((player for player in self.player_panel.players if player.faction == faction), None)
            if owner:
                px = tile_left + (planet.center[0] + planet.radius * .78) * scale
                py = tile_top - (planet.center[1] - planet.radius * .78) * scale
                marker_size = max(8, width * .06)
                self.player_panel.image(f'command_token/control_{owner.color_code}.png', px, py, marker_size)
                self.player_panel.image(f'factions/{owner.faction}.png', px, py, marker_size * .52)
        players = [player for player in self.player_panel.players if player.faction in tile.command_tokens]
        token_size = width * .18
        spacing = token_size + max(2, width * .012)
        for index, player in enumerate(players):
            cx = x + (index - (len(players) - 1) / 2) * spacing
            cy = y
            self.player_panel.image(f'command_token/command_{player.color_code}.png', cx, cy, token_size)
            self.player_panel.image(f'factions/{player.faction}.png', cx, cy, token_size * .55)
            self.token_hits.append((tile.position, player.faction, cx, cy, token_size * .52))

    @property
    def planet_hover_system(self):
        planet_id = self.player_panel.hovered_planet_id
        if planet_id is None:
            return None
        return next((tile.position for tile in self.board.values()
                     if any(p.planet_id == planet_id for p in tile.planets)), None)

    def highlight_system(self, x, y, width):
        radius = width * .49
        def vertices(factor):
            return [(x + radius * factor * math.cos(math.pi * i / 3),
                     y + radius * factor * math.sin(math.pi * i / 3)) for i in range(6)]
        arcade.draw_polygon_filled(vertices(1), (*ACCENT, 13))
        arcade.draw_polygon_outline(vertices(1.025), (*ACCENT, 24), 6)
        arcade.draw_polygon_outline(vertices(1.01), (*ACCENT, 65), 3)
        arcade.draw_polygon_outline(vertices(1), (*ACCENT, 155), 1.5)

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
        self.token_hits.clear()
        if self.focus_view:
            tile = self.board[self.selected]
            cx, cy = self.viewport_center
            width = min(self.width - self.sidebar - 90, (self.height - 200 - self.player_panel.HEIGHT) * 345 / 299) * self.focus_zoom
            self.draw_tile(tile, cx, cy + 15, width, detailed=True)
            if self.planet_hover_system == tile.position:
                self.highlight_system(cx, cy + 15, width)
        else:
            for position, tile in self.board.items():
                x, y = self.screen(position)
                self.draw_tile(tile, x, y, radius * 2)
                if self.planet_hover_system == position:
                    self.highlight_system(x, y, radius * 2)
                if tile.player is not None:
                    self.outline(position, tuple(tile.color), 2)
            if self.hover is not None and self.hover != self.selected:
                self.outline(self.hover, (170, 185, 207), 2)
            self.outline(self.selected, ACCENT, 3)
        if self.unit_renderer.is_loading:
            self.text('unit_layout_loading', 'Preparing fleet layouts…',
                      (self.width - self.sidebar) / 2, self.height * .55, 13, MUTED)
        left = self.width - self.sidebar
        arcade.draw_lrbt_rectangle_filled(left, self.width, 0, self.height, PANEL)
        arcade.draw_lrbt_rectangle_filled(0, left, self.height - 80, self.height, (10, 17, 29))
        self.text('title', 'TWILIGHT IMPERIUM IV', 30, self.height - 34, 21)
        self.text('subtitle', f'Game board · {len(self.board.home_tiles)} players · {len(self.board)} systems', 30, self.height - 61, 12, MUTED)
        self.text('zoom', f'{self.focus_zoom if self.focus_view else self.zoom:.1f}×', left - 62, self.height - 45, 13, ACCENT)
        arcade.draw_lrbt_rectangle_filled(left - 225, left - 85, self.height - 56, self.height - 24, (25, 45, 63))
        self.text('focus_button', 'Galaxy view' if self.focus_view else 'Detail view', left - 212, self.height - 46, 12, ACCENT)
        if self.movement.session:
            self.movement_panel.draw(self, self.movement.session, left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 112, 10, (245, 142, 128), self.sidebar - 36)
        else:
            self.system_panel.draw(self, self.board[self.selected], left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 34, 10,
                          (245, 142, 128), self.sidebar - 36)
        self.player_panel.draw(self)

    def on_update(self, delta_time):
        self.unit_renderer.update(delta_time)
        self.zoom += (self.target_zoom - self.zoom) * min(1, delta_time * 14)
        self.frames += 1
        if self.smoke and self.frames == 5:
            preview_dir = ROOT / 'previews'
            preview_dir.mkdir(exist_ok=True)
            for p in self.board:
                assert self.pick(*self.screen(p)) == p, f'Picking failed: {p}'
            for p in self.board:
                x, y = self.screen(p)
                self.last_click = (None, 0.0)
                self.unit_renderer.hits.clear()
                self.on_mouse_press(x, y, arcade.MOUSE_BUTTON_LEFT, 0)
                assert self.selected == p
            assert self.pick(self.width - 10, 100) is None
            self.on_mouse_scroll(100, 400, 0, 100)
            assert self.target_zoom == 3.5
            self.on_mouse_scroll(100, 400, 0, -100)
            assert self.target_zoom == .55
            self.fit()
            previous = tuple(self.map_center)
            self.on_mouse_drag(100, 400, 30, 20, arcade.MOUSE_BUTTON_RIGHT, 0)
            assert tuple(self.map_center) != previous
            self.fit()
            self.selected = self.board.home_tiles[0].position if self.board.home_tiles else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-home-preview.png')
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-preview.png')
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
                    arcade.get_image().save(preview_dir / f'unit-preview-{tile.units[0].owner}.png')
                    if self.system_panel.scroll_max:
                        self.on_mouse_scroll(self.width - 30, self.height / 2, 0, -3)
                        assert self.system_panel.scroll > 0
                        self.on_draw()
                        arcade.get_image().save(preview_dir / f'unit-preview-{tile.units[0].owner}-scrolled.png')
                        self.system_panel.reset()

            if self.player_panel.player:
                self.player_panel.active = 0
                self.on_draw()
                panel = self.player_panel
                def click_control(action):
                    control = next(c for c in panel.controls if c.action == action)
                    self.on_mouse_press(control.left + control.width / 2,
                                        control.bottom + control.height / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    self.on_draw()
                click_control(('planet', 0))
                assert panel.player.planets[0].exhausted
                click_control(('currency', 'commodities', 1))
                assert panel.player.commodities == 1
                click_control(('pool', 'tactical'))
                click_control(('pool', 'strategic'))
                assert panel.player.command_pools == {'tactical': 2, 'fleet': 3, 'strategic': 3}
                self.focus_view = False
                self.selected = self.board.home_tiles[0].position
                self.system_panel.reset()
                self.on_draw()
                arcade.get_image().save(preview_dir / 'player-panel-preview.png')
                click_control(('planet', 0))
                click_control(('currency', 'commodities', -1))
                click_control(('pool', 'strategic'))
                click_control(('pool', 'tactical'))
                if len(panel.players) > 1:
                    click_control(('player', 1))
                    assert panel.player.commodities == 0
                    assert not panel.player.planets[0].exhausted
                    click_control(('player', 0))
                hacan = next((i for i, p in enumerate(panel.players) if p.faction == 'hacan'), None)
                if hacan is not None:
                    click_control(('player', hacan))
                    click_control(('planet', 0))
                    self.on_draw()
                    arcade.get_image().save(preview_dir / 'player-panel-hacan-preview.png')
                    control = next(c for c in panel.controls if c.action == ('planet', 0))
                    previous_selection = self.selected
                    self.on_mouse_motion(control.left + 10, control.bottom + 10, 0, 0)
                    home = next(tile for tile in self.board.home_tiles if any(p.planet_id == panel.hovered_planet_id for p in tile.planets))
                    assert self.planet_hover_system == home.position
                    assert self.selected == previous_selection
                    self.on_draw()
                    assert next(c for c in panel.controls if c.action == ('planet', 0)).bottom == control.bottom
                    arcade.get_image().save(preview_dir / 'planet-hover-preview.png')
                    self.on_mouse_motion(100, self.height - 20, 0, 0)
                    assert panel.hovered_planet_id is None and self.planet_hover_system is None
                    click_control(('planet', 0))
                    click_control(('player', 0))
                original_cards = panel.player.planets
                panel.player.planets = [PlanetCard(p) for tile in self.board.values() for p in tile.planets][:15]
                self.on_draw()
                visible_cards = [c for c in panel.controls if c.action[0] == 'planet']
                assert len(visible_cards) < len(panel.player.planets)
                click_control(('cards', 1))
                assert panel.card_offset == 1
                control = next(c for c in panel.controls if c.action[0] == 'planet')
                self.on_mouse_motion(control.left + 10, control.bottom + 10, 0, 0)
                assert panel.hovered_planet == control.action[1]
                self.on_draw()
                arcade.get_image().save(preview_dir / 'player-panel-dense-preview.png')
                panel.player.planets = original_cards
                panel.hovered_planet = None
                panel.card_offset = 0
            self.focus_view = False
            self.selected_units = ()
            sol_player = next((player for player in self.player_panel.players if player.faction == 'sol'), None)
            if sol_player:
                self.player_panel.active = self.player_panel.players.index(sol_player)
                home = next(tile for tile in self.board.values() if any(unit.owner == 'sol' for unit in tile.units))
                target = next(tile for tile in self.board.neighbors(home.position)
                              if tile.planets and any(self.movement.route(home, tile, unit, sol_player)
                                     for unit in home.units if unit.owner == 'sol'))
                before_units = list(home.units)
                before_tactical = sol_player.command_pools['tactical']
                self.movement.activate(sol_player, target.position)
                self.selected = target.position
                self.on_draw()
                source = self.movement.session.sources[home.position]
                ship = source.ships[0]
                row = next(hit for hit in self.movement_panel.hits if hit[0] == ('unit', ship.unit_id)
                           and self.movement_panel.hit_test((hit[1] + hit[2]) / 2,
                                                            (hit[3] + hit[4]) / 2) == hit[0])
                self.on_mouse_press((row[1] + row[2]) / 2, (row[3] + row[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                infantry = next(unit for unit in source.passengers if unit.kind == 'infantry')
                passenger_row = next(hit for hit in self.movement_panel.hits
                                     if hit[0] == ('unit', infantry.unit_id))
                for _ in range(10):
                    if self.movement_panel.hit_test((passenger_row[1] + passenger_row[2]) / 2,
                                                    (passenger_row[3] + passenger_row[4]) / 2) == passenger_row[0]:
                        break
                    self.movement_panel.scroll_by(80)
                    self.on_draw()
                    passenger_row = next(hit for hit in self.movement_panel.hits
                                         if hit[0] == ('unit', infantry.unit_id))
                assert self.movement_panel.hit_test((passenger_row[1] + passenger_row[2]) / 2,
                                                    (passenger_row[3] + passenger_row[4]) / 2) == passenger_row[0]
                self.on_mouse_press((passenger_row[1] + passenger_row[2]) / 2,
                                    (passenger_row[3] + passenger_row[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                confirm = next(button for button in self.movement_panel.buttons if button[0] == ('confirm',))
                self.on_mouse_press((confirm[1] + confirm[2]) / 2,
                                    (confirm[3] + confirm[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                assert self.movement.session and self.movement.session.stage == 'invasion' and ship in target.units
                assert any(unit.location.region == Region.TRANSPORT and unit.location.carrier_id == ship.unit_id
                           for unit in target.units)
                self.focus_view = True
                self.on_draw()
                step_statuses = [self.labels[('action_status', index)].text for index in range(12)]
                assert step_statuses.count('Current') == 1
                assert self.labels[('action_step', 11)].text == 'STEP 5 · PRODUCTION'
                assert self.labels[('action_status', 11)].text == 'Skipped'
                arcade.get_image().save(preview_dir / 'cargo-preview.png')
                self.focus_view = False
                self.on_draw()
                landing_row = next(hit for hit in self.movement_panel.hits if hit[0] == ('landing', infantry.unit_id))
                self.on_mouse_press((landing_row[1] + landing_row[2]) / 2,
                                    (landing_row[3] + landing_row[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                planet_id = target.planets[0].planet_id
                assert self.movement.session.landings[infantry.unit_id] == planet_id
                establish = next(button for button in self.movement_panel.buttons if button[0] == ('establish',))
                self.on_mouse_press((establish[1] + establish[2]) / 2,
                                    (establish[3] + establish[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                assert self.movement.session is None
                assert target.planet_owners[planet_id] == 'sol'
                assert next(card for card in sol_player.planets if card.planet.planet_id == planet_id).exhausted
                self.focus_view = True
                self.on_draw()
                arcade.get_image().save(preview_dir / 'planet-control-preview.png')
                self.focus_view = False
                self.on_draw()
                arcade.get_image().save(preview_dir / 'system-control-preview.png')
                self.on_key_press(arcade.key.Z, arcade.key.MOD_CTRL)
                assert home.units == before_units and ship not in target.units
                assert 'sol' not in target.command_tokens
                assert sol_player.command_pools['tactical'] == before_tactical
                self.selected = target.position
                hacan_player = next((player for player in self.player_panel.players if player.faction == 'hacan'), None)
                if hacan_player:
                    target.command_tokens.update(('sol', 'hacan'))
                    self.on_draw()
                    assert {faction for position, faction, *_ in self.token_hits if position == target.position} == {'sol', 'hacan'}
                    arcade.get_image().save(preview_dir / 'command-tokens-preview.png')
                    before_tactical = hacan_player.command_pools['tactical']
                    token_hit = next(hit for hit in self.token_hits if hit[0] == target.position and hit[1] == 'hacan')
                    self.on_mouse_press(token_hit[2], token_hit[3], arcade.MOUSE_BUTTON_RIGHT, 0)
                    self.on_draw()
                    assert self.system_panel.remove_token_hit
                    bounds = self.system_panel.remove_token_hit
                    self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                        (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert 'hacan' not in target.command_tokens
                    assert hacan_player.command_pools['tactical'] == before_tactical + 1
                    # The second token verifies that removing one faction's token leaves the other intact.
                    token_hit = next(hit for hit in self.token_hits if hit[0] == target.position and hit[1] == 'sol')
                    self.on_mouse_press(token_hit[2], token_hit[3], arcade.MOUSE_BUTTON_RIGHT, 0)
                    self.on_draw()
                    bounds = self.system_panel.remove_token_hit
                    self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                        (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert not target.command_tokens
                self.on_draw()
                arcade.get_image().save(preview_dir / 'movement-preview.png')
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-preview.png')
            print(f'PASS: {len(self.board)} tile objects; board, player panel and movement activation / undo checked')
            self.close()

    def on_mouse_motion(self, x, y, dx, dy):
        self.player_panel.hover(x, y)
        self.hover = None if self.focus_view else self.pick(x, y)
        hit = self.unit_renderer.hit_test(x, y) if x < self.width - self.sidebar and self.player_panel.HEIGHT <= y < self.height - 80 else None
        inventory_hit = self.system_panel.hit_test(x, y)
        self.hovered_units = tuple(u.unit_id for u in hit.placement.units) if hit else tuple(u.unit_id for u in inventory_hit.units) if inventory_hit else ()

    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_RIGHT:
            if not self.movement.session:
                hit = next(((position, faction) for position, faction, cx, cy, radius in reversed(self.token_hits)
                            if math.dist((x, y), (cx, cy)) <= radius), None)
                if hit:
                    self.token_context = hit
                    self.selected = hit[0]
                    self.focus_view = False
                    self.system_panel.reset()
                else:
                    self.token_context = None
            return
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        if self.token_context and self.token_context[0] != self.selected:
            self.token_context = None
        left = self.width - self.sidebar
        if self.movement.session:
            if x >= left:
                action = self.movement_panel.hit_test(x, y)
                if action:
                    try:
                        if action[0] == 'unit':
                            self.movement.session.toggle(action[1])
                            self.movement_error = None
                        elif action[0] == 'confirm':
                            self.movement.confirm()
                            self.movement_error = None
                            self.movement_panel.reset()
                            self.selected_units = ()
                        elif action[0] == 'cancel':
                            self.movement.cancel()
                            self.movement_error = None
                            self.movement_panel.reset()
                            self.selected_units = ()
                        elif action[0] == 'landing':
                            self.movement.cycle_landing(action[1])
                        elif action[0] == 'establish':
                            self.movement.establish_control()
                            self.movement_panel.reset()
                            self.selected_units = ()
                    except MovementError as error:
                        self.movement_error = str(error)
            return
        if x < left and y < self.player_panel.HEIGHT:
            self.player_panel.handle_click(x, y)
            return
        if left - 225 <= x <= left - 85 and self.height - 56 <= y <= self.height - 24:
            self.toggle_focus()
            return
        if x >= left:
            remove = self.system_panel.remove_token_hit
            if self.token_context and remove and remove[0] <= x <= remove[1] and remove[2] <= y <= remove[3]:
                position, faction = self.token_context
                tile = self.board[position]
                if faction in tile.command_tokens:
                    tile.command_tokens.remove(faction)
                    player = next((p for p in self.player_panel.players if p.faction == faction), None)
                    if player:
                        player.command_pools['tactical'] += 1
                self.token_context = None
                self.system_panel.reset()
                return
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
                try:
                    self.movement.activate(self.player_panel.player, picked)
                    self.movement_panel.reset()
                    self.movement_error = None
                except MovementError as error:
                    self.movement_error = str(error)
                self.last_click = (None, 0.0)
            else:
                self.last_click = (picked, now)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if not self.focus_view and buttons & (arcade.MOUSE_BUTTON_RIGHT | arcade.MOUSE_BUTTON_MIDDLE) and x < self.width - self.sidebar and y >= self.player_panel.HEIGHT:
            scale = self.fit_scale * self.zoom
            self.map_center[0] -= dx / scale
            self.map_center[1] -= dy / scale

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if x < self.width - self.sidebar and y < self.player_panel.HEIGHT:
            return
        if x >= self.width - self.sidebar:
            if self.movement.session:
                self.movement_panel.scroll_by(-scroll_y * 40)
            else:
                self.system_panel.scroll -= scroll_y * 40
                self.system_panel.clamp()
        elif self.focus_view and x < self.width - self.sidebar:
            self.focus_zoom = max(.7, min(1.4, self.focus_zoom * 1.1 ** scroll_y))
        elif x < self.width - self.sidebar:
            self.target_zoom = min(3.5, max(.55, self.target_zoom * 1.15 ** scroll_y))

    def on_key_press(self, symbol, modifiers):
        if symbol == arcade.key.Z and modifiers & arcade.key.MOD_CTRL:
            if self.movement.undo():
                self.movement_error = None
                self.movement_panel.reset()
                self.selected_units = ()
        elif symbol == arcade.key.SPACE and not self.movement.session:
            self.toggle_focus()
        elif symbol == arcade.key.ESCAPE:
            if self.movement.session:
                self.movement.cancel()
                self.movement_panel.reset()
                self.movement_error = None
            self.focus_view = False
            self.token_context = None
        elif symbol == arcade.key.F and not self.movement.session:
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
