from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import arcade

from board import Board, Tile, load_board
from units import Region, Unit, UnitLocation, UNIT_TYPES
from unit_view import UnitRenderer
from system_panel import SystemPanel
from player import PlanetCard, create_players
from player_panel import PlayerPanel
from movement import MovementController, MovementError
from movement_panel import MovementPanel
from combat_panel import CombatPanel
from turn_order import TurnOrder
from strategy_panel import StrategyPanel
from strategic_action import StrategyController
from player_roster import PlayerRoster
from main_menu import MainMenu
from transactions import TransactionController, TransactionError
from transaction_panel import TransactionPanel
from action_card_deck import ActionCardDeck

ROOT = Path(__file__).resolve().parent
DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
BG = (7, 12, 23)
PANEL = (14, 23, 38)
INK = (223, 232, 244)
MUTED = (130, 151, 177)
ACCENT = (100, 207, 224)
COLLAPSED_SIDEBAR_WIDTH = 38


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
    def __init__(self, smoke=False, map_path=ROOT / 'maps/three_player.json',
                 show_menu=False, menu_smoke_test=False):
        display_width, display_height = arcade.get_display_size()
        super().__init__(min(1440, display_width), min(960, display_height),
                         'Twilight Imperium IV', resizable=True, vsync=True)
        self.set_minimum_size(min(1120, display_width), min(720, display_height))
        self.maximize()
        self.smoke = smoke
        self.menu_smoke_test = menu_smoke_test
        self.main_menu = MainMenu()
        self.main_menu_visible = show_menu
        self.frames = 0
        rng = random.Random(4)
        self.stars = [(rng.random(), rng.random(), rng.randrange(45, 100)) for _ in range(190)]
        self.configure_game(map_path, self.main_menu.active_factions)

    def configure_game(self, map_path, factions):
        map_config = json.loads(Path(map_path).read_text(encoding='utf-8'))
        slot_count = sum(bool(entry.get('faction')) for entry in map_config['tiles'])
        self.map_config, self.board = load_board(
            map_path, factions if len(factions) == slot_count else None)
        self.tile_sprites = {tile: TileSprite(tile) for tile in self.board.values()}
        self.labels = {}
        self.unit_renderer = UnitRenderer()
        self.unit_renderer.blocking_layouts = self.smoke
        self.system_panel = SystemPanel()
        self.player_panel = PlayerPanel(create_players(self.board, self.map_config))
        self.action_card_deck = ActionCardDeck()
        self.turn_order = TurnOrder(self.player_panel.players, strategy_enabled=not self.smoke)
        self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
        self.strategy_panel = StrategyPanel()
        self.dragging_modal = None
        self.strategy_hover_system = None
        self.strategy_view = False
        self.roster = PlayerRoster()
        self.inspector_visible = True
        self.inspector_toggle_hit = None
        self.show_planet_control = True
        self.control_toggle_hit = None
        self.turn_history_size = 0
        self.turn_button_hit = None
        self.movement = MovementController(self.board, self.player_panel.players)
        self.strategy = StrategyController(self.board, self.turn_order, self.movement)
        self.transaction = TransactionController(self.board, self.player_panel.players,
                                                self.turn_order, self.movement)
        self.transaction_panel = TransactionPanel()
        self.movement_panel = MovementPanel()
        self.combat_panel = CombatPanel()
        self.movement_error = None
        self.token_hits = []
        self.token_context = None
        self.selected_units = ()
        self.hovered_units = ()
        self.focus_view = False
        self.focus_zoom = 1.0
        self.last_click = (None, 0.0)
        self.orbital_drop_mode = False
        self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
        self.hover = None
        self.zoom = self.target_zoom = 1.0
        self.map_center = [0.0, 0.0]
        self.fit()

    def start_from_menu(self):
        count = self.main_menu.player_count
        map_path = ROOT / ('maps/three_player.json' if count == 3 else 'maps/four_player.json')
        self.configure_game(map_path, self.main_menu.active_factions)
        self.main_menu_visible = False

    @property
    def sidebar(self):
        if not self.inspector_visible:
            return COLLAPSED_SIDEBAR_WIDTH
        return min(340, max(320, self.width * .24))

    def toggle_inspector(self):
        center = self.map_center[:]
        zoom = self.zoom
        target_zoom = self.target_zoom
        self.inspector_visible = not self.inspector_visible
        self.fit()
        self.map_center = center
        self.zoom, self.target_zoom = zoom, target_zoom
        self.system_panel.reset()
        self.movement_panel.reset()

    @property
    def viewport_center(self):
        return self.roster.WIDTH + (self.width - self.sidebar - self.roster.WIDTH) / 2, self.player_panel.HEIGHT + (self.height - 80 - self.player_panel.HEIGHT) / 2

    def fit(self):
        coords = [world(p) for p in self.board]
        xs, ys = zip(*coords)
        self.fit_scale = min((self.width - self.sidebar - self.roster.WIDTH - 80) / (max(xs) - min(xs) + 2), (self.height - 180 - self.player_panel.HEIGHT) / (max(ys) - min(ys) + math.sqrt(3)))
        self.map_center = [(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2]
        self.zoom = self.target_zoom = 1.0

    def screen(self, position):
        x, y = world(position)
        cx, cy = self.viewport_center
        scale = self.fit_scale * self.zoom
        return cx + (x - self.map_center[0]) * scale, cy + (y - self.map_center[1]) * scale

    def pick(self, x, y):
        if x < self.roster.WIDTH or x >= self.width - self.sidebar or y > self.height - 80 or y < self.player_panel.HEIGHT:
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
        if self.show_planet_control:
            self.draw_planet_control_border(tile, x, y, width)

    def draw_planet_control_border(self, tile, x, y, width):
        players = {player.faction: player for player in self.player_panel.players}
        counts = {}
        for planet in tile.planets:
            faction = tile.planet_owners.get(planet.planet_id)
            if faction in players:
                counts[faction] = counts.get(faction, 0) + 1
        if not counts:
            return
        factions = list(counts)
        total = sum(counts.values())
        edge_counts = {faction: max(1, int(6 * count / total)) for faction, count in counts.items()}
        while sum(edge_counts.values()) > 6:
            faction = max((f for f in factions if edge_counts[f] > 1),
                          key=lambda f: edge_counts[f] - 6 * counts[f] / total)
            edge_counts[faction] -= 1
        while sum(edge_counts.values()) < 6:
            faction = max(factions, key=lambda f: 6 * counts[f] / total - edge_counts[f])
            edge_counts[faction] += 1
        edge_factions = [faction for faction in factions for _ in range(edge_counts[faction])]
        colors = {'blu': (92, 166, 255), 'ylw': (255, 214, 86), 'ppl': (194, 132, 255),
                  'red': (247, 101, 101), 'grn': (104, 219, 147), 'org': (255, 151, 72),
                  'blk': (174, 188, 204), 'wht': (224, 235, 245), 'brn': (181, 128, 87),
                  'gry': (166, 180, 194)}
        radius = width * .49
        vertices = [(x + radius * math.cos(math.pi * index / 3),
                     y + radius * math.sin(math.pi * index / 3)) for index in range(6)]
        for index, faction in enumerate(edge_factions):
            color = colors.get(players[faction].color_code, ACCENT)
            start, end = vertices[index], vertices[(index + 1) % 6]
            arcade.draw_line(*start, *end, (*color, 65), 8)
            arcade.draw_line(*start, *end, color, 3)

    @property
    def planet_hover_system(self):
        if self.strategy_modal:
            return self.strategy_hover_system
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

    def draw_route_preview(self, route):
        if self.focus_view or not route or len(route) < 2:
            return
        radius = self.fit_scale * self.zoom * .98
        centers = [self.screen(position) for position in route]
        for index, (start_center, end_center) in enumerate(zip(centers, centers[1:])):
            dx, dy = end_center[0] - start_center[0], end_center[1] - start_center[1]
            distance = math.hypot(dx, dy)
            if distance <= 0:
                continue
            ux, uy = dx / distance, dy / distance
            start = (start_center[0] + ux * radius * .65,
                     start_center[1] + uy * radius * .65)
            end = (end_center[0] - ux * radius * .65,
                   end_center[1] - uy * radius * .65)
            arcade.draw_line(*start, *end, (8, 15, 26, 220), 8)
            arcade.draw_line(*start, *end, (255, 203, 103), 3)
            head_length = min(14, math.dist(start, end) * .65)
            head_width = max(5, head_length * .62)
            base = (end[0] - ux * head_length, end[1] - uy * head_length)
            px, py = -uy * head_width, ux * head_width
            arcade.draw_polygon_filled(
                [end, (base[0] + px, base[1] + py), (base[0] - px, base[1] - py)],
                (255, 203, 103),
            )

    def toggle_focus(self):
        self.focus_view = not self.focus_view
        self.focus_zoom = 1.0
        self.hovered_units = ()

    def sync_turn_action(self):
        if self.strategy.session:
            self.strategy.poll()
            self.sync_strategy_actor()
            return
        if self.smoke:
            return
        history_size = len(self.movement.history)
        if history_size > self.turn_history_size:
            self.turn_order.mark_action_completed()
        elif history_size < self.turn_history_size and self.turn_order.action_used and not self.movement.session:
            self.turn_order.action_used = False
        self.turn_history_size = history_size

    @property
    def strategy_modal(self):
        return (self.turn_order.strategy_selection or
                bool(self.strategy.session and self.strategy.session.stage != 'production') or
                bool(self.strategy_view and not self.movement.session))

    @property
    def transaction_modal(self):
        return bool(self.transaction.session)

    def sync_strategy_actor(self):
        player = self.strategy.player
        index = self.player_panel.players.index(player)
        if self.player_panel.active != index:
            self.player_panel.active = index
            self.player_panel.card_offset = 0
            self.player_panel.source_pool = None
            self.player_panel.hovered_planet = None
        self.strategy_view = bool(self.strategy.session and self.strategy.session.stage != 'production')
        self.turn_history_size = len(self.movement.history)

    def handle_strategy_action(self, action):
        if not action:
            return
        kind, *args = action
        ctl, session = self.strategy, self.strategy.session
        try:
            if kind == 'choose_strategy':
                self.turn_order.choose_strategy_card(args[0])
                self.sync_strategy_actor()
            elif kind == 'close':
                self.strategy_view = False
            elif kind == 'page':
                self.strategy_panel.page = max(0, self.strategy_panel.page + args[0])
            elif kind == 'start':
                ctl.start(args[0])
                self.sync_strategy_actor()
            elif session:
                if kind == 'accept':
                    ctl.accept_secondary()
                elif kind == 'decline':
                    ctl.decline_secondary()
                elif kind == 'buy':
                    ctl.change_purchase(args[0])
                elif kind == 'goods':
                    ctl.change_goods(args[0])
                elif kind == 'pay':
                    ctl.pay_leadership()
                elif kind == 'allocate':
                    pool = args[0]
                    if session.player.pending_commands:
                        ctl.allocate(pool)
                    elif session.stage == 'warfare_allocate':
                        if session.pool_source:
                            session.player.transfer_command(session.pool_source, pool)
                            session.pool_source = None
                        else:
                            session.pool_source = pool
                elif kind == 'system':
                    ctl.select_system(args[0])
                elif kind == 'ready_planet':
                    ctl.toggle_ready(args[0])
                elif kind == 'ready_confirm':
                    ctl.confirm_ready()
                elif kind == 'speaker':
                    ctl.choose_speaker(args[0])
                elif kind == 'trade_toggle':
                    session.free_trade.symmetric_difference_update((args[0],))
                elif kind == 'trade_confirm':
                    ctl.confirm_trade()
                elif kind == 'structure':
                    if not (session.primary and session.builds_left == 1):
                        session.structure = args[0]
                elif kind == 'build':
                    ctl.build(args[0])
                elif kind == 'produce_at':
                    ctl.produce_at(args[0])
                    self.movement_panel.reset()
                elif kind == 'continue':
                    ctl.continue_stage()
                self.sync_strategy_actor()
            self.movement_error = None
        except (ValueError, StopIteration) as error:
            self.movement_error = str(error) or 'This choice is no longer available.'

    def handle_transaction_action(self, action):
        if not action:
            return
        kind, *args = action
        try:
            if kind == 'cancel':
                self.transaction.cancel()
            elif kind == 'partner':
                self.transaction.choose_partner(args[0])
            elif kind == 'change':
                self.transaction.change(args[0], args[1])
            elif kind == 'toggle_card':
                self.transaction.toggle_action_card(args[0], args[1])
            elif kind == 'confirm':
                self.transaction.confirm()
            elif kind == 'back':
                session = self.transaction.session
                session.partner = None
                session.give_trade_goods = session.give_commodities = 0
                session.take_trade_goods = session.take_commodities = 0
                session.give_action_cards.clear()
                session.take_action_cards.clear()
            self.movement_error = None
        except (TransactionError, StopIteration) as error:
            self.movement_error = str(error) or 'This transaction is no longer available.'

    def strategy_tray_click(self, x, y):
        session = self.strategy.session
        if not session or y >= self.player_panel.HEIGHT:
            return
        control = next((c for c in reversed(self.player_panel.controls) if c.contains(x, y)), None)
        if not control:
            return
        kind, *args = control.action
        if kind == 'cards':
            self.player_panel.handle_click(x, y)
        elif session.stage == 'leadership' and kind == 'planet':
            self.strategy.toggle_payment(session.player.planets[args[0]].planet.planet_id)
        elif session.stage == 'ready_planets' and kind == 'planet':
            self.strategy.toggle_ready(session.player.planets[args[0]].planet.planet_id)
        elif session.stage in ('allocate', 'warfare_allocate') and kind == 'pool':
            self.handle_strategy_action(('allocate', args[0]))

    def pass_turn(self):
        self.strategy_view = False
        try:
            round_complete = self.turn_order.end_turn()
        except ValueError as error:
            self.movement_error = str(error)
            return
        if not self.turn_order.players:
            return
        if round_complete:
            self.clear_round_tokens()
            for player in self.turn_order.players:
                player.receive_round_commands()
                for card in player.planets:
                    card.exhausted = False
                if len(player.action_cards) < 7:
                    action_card = self.action_card_deck.draw()
                    if action_card:
                        player.action_cards.append(action_card)
            self.turn_order.begin_command_allocation()
            self.strategy_view = False
            if not self.turn_order.command_allocation and self.turn_order.strategy_enabled:
                self.turn_order.begin_strategy_phase()
                self.strategy_view = True
        self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
        self.player_panel.source_pool = None
        self.player_panel.card_offset = 0
        self.player_panel.hovered_planet = None
        self.turn_history_size = len(self.movement.history)
        self.selected_units = ()
        self.token_context = None
        self.movement_error = None
        self.system_panel.reset()
        self.movement_panel.reset()

    def clear_round_tokens(self):
        """Remove every faction's command token from every system at round end."""
        for tile in self.board.values():
            tile.command_tokens.clear()
        self.token_hits.clear()

    def on_draw(self):
        self.clear(BG)
        if self.main_menu_visible:
            self.main_menu.draw(self)
            return
        star_bins = [[] for _ in range(6)]
        for sx, sy, brightness in self.stars:
            bucket = min(5, max(0, (brightness - 45) // 10))
            star_bins[bucket].append((sx * (self.width - self.sidebar), sy * (self.height - 80)))
        for bucket, points in enumerate(star_bins):
            if points:
                brightness = 50 + bucket * 10
                arcade.draw_points(points, (brightness, brightness, min(255, brightness + 20)), 1)
        radius = self.fit_scale * self.zoom
        if self.selected not in self.board:
            self.selected = next(iter(self.board))
        self.unit_renderer.hits.clear()
        self.token_hits.clear()
        movement_route = self.movement_panel.preview_route(self.movement.session)
        if self.focus_view:
            tile = self.board[self.selected]
            cx, cy = self.viewport_center
            width = min(self.width - self.sidebar - self.roster.WIDTH - 90, (self.height - 200 - self.player_panel.HEIGHT) * 345 / 299) * self.focus_zoom
            self.draw_tile(tile, cx, cy + 15, width, detailed=True)
            if self.planet_hover_system == tile.position:
                self.highlight_system(cx, cy + 15, width)
            if self.movement_panel.hovered_source_position == tile.position:
                arcade.draw_polygon_outline(
                    [(cx + width * .49 * math.cos(math.pi * index / 3),
                      cy + 15 + width * .49 * math.sin(math.pi * index / 3)) for index in range(6)],
                    (255, 203, 103), 4)
        else:
            for position, tile in self.board.items():
                x, y = self.screen(position)
                self.draw_tile(tile, x, y, radius * 2)
                if self.planet_hover_system == position:
                    self.highlight_system(x, y, radius * 2)
                if self.movement_panel.hovered_source_position == position:
                    self.outline(position, (255, 203, 103), 4)
                if tile.player is not None and not self.show_planet_control:
                    self.outline(position, tuple(tile.color), 2)
            if self.hover is not None and self.hover != self.selected:
                self.outline(self.hover, (170, 185, 207), 2)
            self.outline(self.selected, ACCENT, 3)
            self.draw_route_preview(movement_route)
        if self.unit_renderer.is_loading:
            self.text('unit_layout_loading', 'Preparing fleet layouts…',
                      (self.width - self.sidebar) / 2, self.height * .55, 13, MUTED)
        left = self.width - self.sidebar
        arcade.draw_lrbt_rectangle_filled(left, self.width, 0, self.height, PANEL)
        tab_bottom = self.height - 110
        if self.inspector_visible:
            toggle_left, toggle_right = left - 17, left + 17
            toggle_glyph = '›'
        else:
            toggle_left, toggle_right = left + 2, self.width - 2
            toggle_glyph = '‹'
        arcade.draw_lrbt_rectangle_filled(0, left, self.height - 80, self.height, (10, 17, 29))
        self.text('title', 'TWILIGHT IMPERIUM IV', 30, self.height - 34, 21)
        self.text('subtitle', f'Game board · {len(self.board.home_tiles)} players · {len(self.board)} systems', 30, self.height - 61, 12, MUTED)
        self.text('zoom', f'{self.focus_zoom if self.focus_view else self.zoom:.1f}×', left - 62, self.height - 75, 11, ACCENT)
        arcade.draw_lrbt_rectangle_filled(left - 225, left - 118, self.height - 56, self.height - 24, (25, 45, 63))
        self.text('focus_button', 'Galaxy' if self.focus_view else 'Detail', left - 212, self.height - 46, 11, ACCENT)
        control_left, control_right = left - 110, left - 4
        arcade.draw_lrbt_rectangle_filled(control_left, control_right, self.height - 56, self.height - 24,
                                           (35, 66, 77) if self.show_planet_control else (25, 45, 63))
        self.text('control_toggle', 'BORDERS ON' if self.show_planet_control else 'BORDERS OFF',
                  control_left + 7, self.height - 46, 9,
                  (110, 218, 161) if self.show_planet_control else MUTED)
        self.control_toggle_hit = (control_left, control_right, self.height - 56, self.height - 24)
        active_player = self.turn_order.active_player
        active_faction = active_player.faction.upper() if active_player else 'NO PLAYER'
        allocation_status = (' · RESOLVING ' + self.strategy.player.faction.upper() if self.strategy.session else
                             ' · COMMAND ALLOCATION' if self.turn_order.command_allocation else '')
        self.text('turn_status', f'ROUND {self.turn_order.round_number} · {active_faction}{allocation_status}',
                  left - 370, self.height - 19, 9, ACCENT if self.turn_order.action_used else MUTED)
        turn_left, turn_right = left - 370, left - 233
        turn_bottom, turn_top = self.height - 58, self.height - 27
        pending_commands = active_player.pending_commands if active_player else 0
        enabled = (not self.movement.session and not pending_commands and
                   not self.turn_order.command_allocation and not self.turn_order.strategy_selection and not self.strategy.session)
        button_label = ('STRATEGY PHASE' if self.turn_order.strategy_selection else
                        'STRATEGY ACTION' if self.strategy.session else
                        'ALLOCATE COMMANDS' if pending_commands else
                        'END TURN' if self.turn_order.action_used else 'PASS')
        arcade.draw_lrbt_rectangle_filled(turn_left, turn_right, turn_bottom, turn_top,
                                           (31, 78, 83) if enabled else (34, 41, 52))
        arcade.draw_lrbt_rectangle_outline(turn_left, turn_right, turn_bottom, turn_top,
                                            (77, 151, 151) if enabled else (61, 75, 92), 1)
        self.text('pass_turn_button', button_label, turn_left + 10, turn_bottom + 10, 9,
                  INK if enabled else MUTED)
        self.turn_button_hit = (turn_left, turn_right, turn_bottom, turn_top) if enabled else None
        if not self.inspector_visible:
            self.system_panel.hits.clear()
            self.system_panel.remove_token_hit = None
            self.system_panel.add_token_hit = None
            self.system_panel.strategy_tab_hit = None
            self.movement_panel.hits.clear()
            self.movement_panel.buttons.clear()
            self.movement_panel.route_hits.clear()
            self.movement_panel.source_hits.clear()
        elif self.movement.session:
            self.movement_panel.draw(self, self.movement.session, left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 112, 10, (245, 142, 128), self.sidebar - 36)
        else:
            self.system_panel.draw(self, self.board[self.selected], left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 34, 10,
                          (245, 142, 128), self.sidebar - 36)
        arcade.draw_lrbt_rectangle_filled(toggle_left, toggle_right, tab_bottom, tab_bottom + 36,
                                           (25, 45, 63))
        arcade.draw_lrbt_rectangle_outline(toggle_left, toggle_right, tab_bottom, tab_bottom + 36,
                                            (77, 151, 151), 1)
        self.text('inspector_toggle_glyph', toggle_glyph,
                  (toggle_left + toggle_right) / 2 - 5, tab_bottom + 7, 19, ACCENT)
        self.inspector_toggle_hit = (toggle_left, toggle_right, tab_bottom, tab_bottom + 36)
        self.player_panel.production_planets = (
            set(self.strategy.session.payment_planets) if self.strategy.session and self.strategy.session.stage == 'leadership'
            else set(self.movement.session.production_planets)
            if self.movement.session and self.movement.session.stage == 'production' else set())
        self.player_panel.draw(self, show_details=False)
        self.roster.draw(self)
        if not self.strategy_modal:
            self.roster.draw_details(self)
        if self.player_panel.hovered_planet is not None:
            self.player_panel.draw_details(self)
        if self.movement.session and self.movement.session.stage in ('space_combat', 'ground_combat', 'retreat_selection'):
            self.combat_panel.draw(self, self.movement.session)
        if self.strategy_modal:
            self.strategy_panel.draw(self, self.turn_order)
        if self.transaction_modal:
            self.transaction_panel.draw(self, self.transaction)

    def on_update(self, delta_time):
        if self.main_menu_visible:
            self.frames += 1
            if self.menu_smoke_test and self.frames >= 2:
                self.on_draw()
                assert len(self.main_menu.hits) >= 7
                preview_dir = ROOT / 'previews'
                preview_dir.mkdir(exist_ok=True)
                arcade.get_image().save(preview_dir / 'main-menu-preview.png')
                hit = next(hit for hit in self.main_menu.hits if hit[0] == ('map', 4))
                self.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                hit = next(hit for hit in self.main_menu.hits if hit[0] == ('faction', 0, 1))
                self.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                assert len(set(self.main_menu.active_factions)) == 4
                self.on_draw()
                hit = next(hit for hit in self.main_menu.hits if hit[0] == ('start',))
                self.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                assert not self.main_menu_visible and len(self.board) == 37
                assert tuple(player.faction for player in self.player_panel.players) == self.main_menu.active_factions
                self.on_draw()
                arcade.get_image().save(preview_dir / 'four-player-menu-start-preview.png')
                letnev = next(player for player in self.player_panel.players if player.faction == 'letnev')
                sol = next(player for player in self.player_panel.players if player.faction == 'sol')
                letnev_home = next(tile for tile in self.board.values()
                                   if any(unit.owner == 'letnev' and unit.kind == 'carrier' for unit in tile.units))
                combat_target = next(tile for tile in self.movement.neighbors(letnev_home)
                                     if 'supernova' not in tile.anomalies and 'gravity_rift' not in tile.anomalies)
                combat_target.units.append(Unit('menu-smoke-enemy', 'cruiser', 'sol', sol.color_code,
                                                UnitLocation(Region.SPACE)))
                letnev.trade_goods = 2
                battle = self.movement.activate(letnev, combat_target.position)
                battle.toggle(battle.sources[letnev_home.position].ships[0].unit_id)
                self.movement.confirm()
                assert battle.stage == 'space_combat'
                self.selected = combat_target.position
                self.on_draw()
                assert 'munitions_button' in self.labels
                arcade.get_image().save(preview_dir / 'munitions-reserves-preview.png')
                spend = next(hit for hit in self.combat_panel.action_hits
                             if hit[0] == ('spend_munitions', 'letnev'))
                self.on_mouse_press((spend[1] + spend[2]) / 2, (spend[3] + spend[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                assert letnev.trade_goods == 0
                from unittest.mock import patch
                advance = self.combat_panel.advance_hit
                with patch('movement.random.randint', return_value=1):
                    self.on_mouse_press((advance[0] + advance[1]) / 2,
                                        (advance[2] + advance[3]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                die = next(hit for hit in self.combat_panel.reroll_die_hits
                           if hit[0][0] == 'reroll_die' and hit[0][1] == 'letnev')
                self.on_mouse_press((die[1] + die[2]) / 2, (die[3] + die[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                reroll = next(hit for hit in self.combat_panel.action_hits
                              if hit[0] == ('reroll_dice', 'letnev'))
                with patch('movement.random.randint', return_value=10):
                    self.on_mouse_press((reroll[1] + reroll[2]) / 2,
                                        (reroll[3] + reroll[4]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                assert battle.combat_rolls['letnev'][0]['value'] == 10
                print('PASS: main menu, four-player start, long-range trade, Orbital Drop and Letnev rerolls checked')
                self.close()
            return
        self.unit_renderer.update(delta_time)
        self.sync_turn_action()
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
            self.on_mouse_scroll(self.roster.WIDTH + 100, 400, 0, 100)
            assert self.target_zoom == 3.5
            self.on_mouse_scroll(self.roster.WIDTH + 100, 400, 0, -100)
            assert self.target_zoom == .55
            self.fit()
            previous = tuple(self.map_center)
            self.on_mouse_drag(self.roster.WIDTH + 100, 400, 30, 20, arcade.MOUSE_BUTTON_RIGHT, 0)
            assert tuple(self.map_center) != previous
            self.fit()
            self.selected = self.board.home_tiles[0].position if self.board.home_tiles else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-home-preview.png')
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-preview.png')
            assert self.show_planet_control and self.labels['control_toggle'].text == 'BORDERS ON'
            bounds = self.control_toggle_hit
            self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert not self.show_planet_control
            self.on_draw()
            assert self.labels['control_toggle'].text == 'BORDERS OFF'
            bounds = self.control_toggle_hit
            self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert self.show_planet_control
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
                assert not panel.player.planets[0].exhausted, 'Planets cannot be exhausted outside production payment.'
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
                assert all(self.labels[('action_status', index)].text == 'Wait' for index in range(3, 13)), \
                    {index: self.labels[('action_status', index)].text for index in range(13)}
                source = self.movement.session.sources[home.position]
                route_hit = self.movement_panel.route_hits[0]
                self.on_mouse_motion((route_hit[2] + route_hit[3]) / 2,
                                     (route_hit[4] + route_hit[5]) / 2, 0, 0)
                route_preview = self.movement_panel.preview_route(self.movement.session)
                assert self.movement_panel.hovered_source_position == home.position
                assert route_preview == source.routes[route_hit[1]] and route_preview[-1] == target.position
                self.on_draw()
                arcade.get_image().save(preview_dir / 'movement-route-preview.png')
                self.on_mouse_motion(-1, -1, 0, 0)
                production_planet = target.planets[0]
                target.units.append(Unit('smoke-production-base', 'spacedock', 'sol', sol_player.color_code,
                                         UnitLocation(Region.PLANET, planet_id=production_planet.planet_id)))
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
                step_statuses = [self.labels[('action_status', index)].text for index in range(13)]
                assert step_statuses.count('Current') == 1
                assert self.labels[('action_step', 11)].text == 'STEP 5 · PRODUCTION'
                assert all(self.labels[('action_status', index)].text == 'Skipped' for index in (4, 6))
                assert self.labels[('action_status', 5)].text == 'In progress'
                assert self.labels[('action_status', 7)].text == 'Current'
                assert all(self.labels[('action_status', index)].text == 'Wait' for index in (9, 10, 11, 12))
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
                assert self.movement.session and self.movement.session.stage == 'production'
                assert target.planet_owners[planet_id] == 'sol'
                assert next(card for card in sol_player.planets if card.planet.planet_id == planet_id).exhausted
                step_statuses = [self.labels[('action_status', index)].text for index in range(13)]
                assert step_statuses.count('Current') == 1
                assert self.labels[('action_step', 11)].text == 'STEP 5 · PRODUCTION'
                assert self.labels[('action_status', 11)].text == 'In progress'
                arcade.get_image().save(preview_dir / 'production-preview.png')
                infantry_plus = next(hit for hit in self.movement_panel.hits
                                     if hit[0] == ('production_unit', 'infantry', 1))
                self.on_mouse_press((infantry_plus[1] + infantry_plus[2]) / 2,
                                    (infantry_plus[3] + infantry_plus[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                home_card = next(control for control in self.player_panel.controls
                                 if control.action[0] == 'planet' and
                                 self.player_panel.player.planets[control.action[1]].planet.planet_id in
                                 {unit.location.planet_id for unit in home.units if unit.location.region == Region.PLANET})
                self.on_mouse_press(home_card.left + home_card.width / 2,
                                    home_card.bottom + home_card.height / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                self.on_draw()
                arcade.get_image().save(preview_dir / 'production-payment-preview.png')
                produce = next(button for button in self.movement_panel.buttons if button[0] == ('produce',))
                self.on_mouse_press((produce[1] + produce[2]) / 2,
                                    (produce[3] + produce[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                assert self.movement.session is None
                assert sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                           unit.location.planet_id == production_planet.planet_id for unit in target.units) == 3
                self.focus_view = True
                self.on_draw()
                arcade.get_image().save(preview_dir / 'planet-control-preview.png')
                self.focus_view = False
                self.on_draw()
                arcade.get_image().save(preview_dir / 'system-control-preview.png')
                self.on_key_press(arcade.key.Z, arcade.key.MOD_CTRL)
                assert sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                           unit.location.planet_id == production_planet.planet_id for unit in target.units) == 0
                assert home.units == before_units and ship not in target.units
                assert 'sol' not in target.command_tokens
                assert sol_player.command_pools['tactical'] == before_tactical
                # Exercise the real Ctrl+Z handler after fleet overflow destroys a carrier and its cargo.
                fleet_before = sol_player.command_pools['fleet']
                sol_player.command_pools['fleet'] = 1
                overflow_before_units = list(home.units)
                self.movement.activate(sol_player, target.position)
                overflow_session = self.movement.session
                overflow_source = overflow_session.sources[home.position]
                overflow_carrier = next(unit for unit in overflow_source.ships if unit.kind == 'carrier')
                overflow_destroyer = next(unit for unit in overflow_source.ships if unit.kind == 'destroyer')
                overflow_infantry = next(unit for unit in overflow_source.passengers if unit.kind == 'infantry')
                for unit in (overflow_carrier, overflow_destroyer, overflow_infantry):
                    overflow_session.toggle(unit.unit_id)
                self.movement.confirm()
                assert overflow_session.stage == 'fleet_overflow'
                self.movement.toggle_overflow_ship(overflow_carrier.unit_id)
                self.movement.resolve_fleet_overflow()
                assert overflow_session.stage == 'invasion'
                assert overflow_carrier not in target.units and overflow_infantry not in target.units
                self.movement.establish_control()
                assert self.movement.session is None
                self.on_key_press(arcade.key.Z, arcade.key.MOD_CTRL)
                assert home.units == overflow_before_units
                assert overflow_carrier in home.units and overflow_infantry in home.units
                assert 'sol' not in target.command_tokens
                assert sol_player.command_pools['fleet'] == 1
                sol_player.command_pools['fleet'] = fleet_before
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
                    before_tactical = sol_player.command_pools['tactical']
                    tx, ty = self.screen(target.position)
                    self.on_mouse_press(tx, ty, arcade.MOUSE_BUTTON_RIGHT, 0)
                    self.on_draw()
                    assert self.system_panel.add_token_hit
                    bounds = self.system_panel.add_token_hit
                    self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                        (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert 'sol' in target.command_tokens
                    assert sol_player.command_pools['tactical'] == before_tactical - 1
                    # Refresh token hit targets before the synthetic right-click.
                    self.on_draw()
                    self.on_mouse_press(tx, ty, arcade.MOUSE_BUTTON_RIGHT, 0)
                    self.on_draw()
                    bounds = self.system_panel.remove_token_hit
                    self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                        (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert 'sol' not in target.command_tokens

                    # Exercise the combat modal and dice controls with a deterministic miss round.
                    hacan_home = next(tile for tile in self.board.values()
                                      if any(unit.owner == 'hacan' for unit in tile.units))
                    combat_target = next(tile for tile in self.board.neighbors(hacan_home.position)
                                         if not any(unit.owner == 'sol' and unit.location.region == Region.SPACE
                                                    for unit in tile.units))
                    enemy_ship = Unit('smoke-combat-enemy', 'cruiser', 'sol', sol_player.color_code,
                                      UnitLocation(Region.SPACE))
                    combat_target.units.append(enemy_ship)
                    hacan_player = next(player for player in self.player_panel.players if player.faction == 'hacan')
                    self.player_panel.active = self.player_panel.players.index(hacan_player)
                    self.movement.activate(hacan_player, combat_target.position)
                    combat_session = self.movement.session
                    combat_source = next(iter(combat_session.sources.values()))
                    combat_ship = combat_source.ships[0]
                    combat_session.toggle(combat_ship.unit_id)
                    self.movement.confirm()
                    assert combat_session.stage == 'space_combat'
                    self.selected = combat_target.position
                    self.on_draw()
                    assert self.labels['combat_modal_title'].text == 'SPACE COMBAT'
                    arcade.get_image().save(preview_dir / 'space-combat-preview.png')
                    from unittest.mock import patch
                    button = self.combat_panel.advance_hit
                    with patch('movement.random.randint', return_value=1):
                        self.on_mouse_press((button[0] + button[1]) / 2,
                                            (button[2] + button[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                        self.on_draw()
                        button = self.combat_panel.advance_hit
                        self.on_mouse_press((button[0] + button[1]) / 2,
                                            (button[2] + button[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert combat_session.stage == 'space_combat' and combat_session.combat_round == 1
                    self.on_key_press(arcade.key.ESCAPE, 0)
                    self.player_panel.active = self.player_panel.players.index(sol_player)
                    assert enemy_ship in combat_target.units
                    combat_target.units.remove(enemy_ship)

                    # Draw the ground-combat modal after assigning a landed force.
                    sol_home = next(tile for tile in self.board.values()
                                    if any(unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
                    ground_target = next(tile for tile in self.board.neighbors(sol_home.position)
                                         if tile.planets and not tile.command_tokens and
                                         not any(unit.owner != 'sol' and unit.location.region == Region.SPACE
                                                 for unit in tile.units))
                    planet = ground_target.planets[0]
                    previous_owner = ground_target.planet_owners.get(planet.planet_id)
                    hacan_cards_before = list(hacan_player.planets)
                    ground_defender = Unit('smoke-ground-defender', 'infantry', 'hacan',
                                           hacan_player.color_code,
                                           UnitLocation(Region.PLANET, planet_id=planet.planet_id))
                    ground_target.planet_owners[planet.planet_id] = 'hacan'
                    ground_target.units.append(ground_defender)
                    hacan_player.planets.append(PlanetCard(planet))
                    self.player_panel.active = self.player_panel.players.index(sol_player)
                    ground_session = self.movement.activate(sol_player, ground_target.position)
                    ground_source = ground_session.sources[sol_home.position]
                    ground_carrier = next(unit for unit in ground_source.ships if unit.kind == 'carrier')
                    ground_infantry = next(unit for unit in ground_source.passengers if unit.kind == 'infantry')
                    ground_session.toggle(ground_carrier.unit_id)
                    ground_session.toggle(ground_infantry.unit_id)
                    self.movement.confirm()
                    self.movement.cycle_landing(ground_infantry.unit_id)
                    self.movement.establish_control()
                    assert ground_session.stage == 'ground_combat'
                    self.selected = ground_target.position
                    self.on_draw()
                    assert self.labels['combat_modal_title'].text == 'GROUND COMBAT'
                    arcade.get_image().save(preview_dir / 'ground-combat-preview.png')
                    from unittest.mock import patch
                    with patch('movement.random.randint', return_value=10):
                        bounds = self.combat_panel.advance_hit
                        self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                            (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                        self.on_draw()
                        for faction, kind in (('sol', 'infantry'), ('hacan', 'infantry')):
                            action = ('assign_hit', faction, kind)
                            bounds = next((hit[1:] for hit in self.combat_panel.assignment_hits
                                           if hit[0] == action))
                            self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                                (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                            self.on_draw()
                        bounds = self.combat_panel.advance_hit
                        self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                            (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                    assert self.movement.session is None
                    self.on_key_press(arcade.key.Z, arcade.key.MOD_CTRL)
                    assert self.movement.session is ground_session
                    assert ground_session.stage == 'invasion'
                    self.movement.cancel()
                    assert self.movement.session is None
                    assert ground_defender in ground_target.units
                    ground_target.units.remove(ground_defender)
                    if previous_owner is None:
                        ground_target.planet_owners.pop(planet.planet_id, None)
                    else:
                        ground_target.planet_owners[planet.planet_id] = previous_owner
                    hacan_player.planets[:] = hacan_cards_before
                self.on_draw()
                arcade.get_image().save(preview_dir / 'movement-preview.png')
            self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
            # The first round wraps only after every player passes. The completed round
            # clears board tokens, refreshes planets, and grants each player's commands.
            expected_players = self.player_panel.players
            sol_player.planets[0].exhausted = True
            for tile_index, tile in enumerate(self.board.values()):
                tile.command_tokens.update(player.faction for player in expected_players[:tile_index % 4])
            target.command_tokens.add('sol')
            self.on_draw()
            arcade.get_image().save(preview_dir / 'board-preview.png')
            self.turn_order.mark_action_completed()
            self.on_draw()
            assert self.labels['pass_turn_button'].text == 'END TURN'
            bounds = self.turn_button_hit
            self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert self.turn_order.active_player is expected_players[1]
            assert not self.turn_order.passed_indices
            assert self.turn_order.round_number == 1
            for pass_index, expected_player in enumerate((expected_players[1], expected_players[2], expected_players[0])):
                self.on_draw()
                assert self.player_panel.player is expected_player
                assert self.labels['pass_turn_button'].text == 'PASS'
                bounds = self.turn_button_hit
                assert bounds
                self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                    (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
                if pass_index < 2:
                    assert self.turn_order.active_player is (expected_players[2] if pass_index == 0 else expected_players[0])
                    assert expected_players.index(expected_player) in self.turn_order.passed_indices
            assert self.turn_order.round_number == 2
            assert self.turn_order.active_player is expected_players[0]
            assert not any(tile.command_tokens for tile in self.board.values())
            assert not self.token_hits
            assert all(not card.exhausted for player in expected_players for card in player.planets)
            assert all(len(player.action_cards) == 1 for player in expected_players)
            assert [player.pending_commands for player in expected_players] == [
                player.round_command_gain() for player in expected_players]
            expected_pool_counts = [(player, dict(player.command_pools)) for player in expected_players]
            self.on_draw()
            assert self.turn_button_hit is None
            assert self.turn_order.command_allocation
            for player in expected_players:
                self.on_draw()
                assert self.player_panel.player is player
                commands_to_allocate = player.pending_commands
                pending_control = next(control for control in self.player_panel.controls
                                       if control.action[0] == 'pending')
                self.on_mouse_press(pending_control.left + pending_control.width / 2,
                                    pending_control.bottom + pending_control.height / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                for command_index in range(commands_to_allocate):
                    pool = ('tactical', 'fleet', 'strategic')[command_index % 3]
                    self.on_draw()
                    pool_control = next(control for control in self.player_panel.controls
                                        if control.action == ('pool', pool))
                    self.on_mouse_press(pool_control.left + pool_control.width / 2,
                                        pool_control.bottom + pool_control.height / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                assert player.pending_commands == 0
                self.on_draw()
                previous_pool_counts = next(counts for expected_player, counts in expected_pool_counts
                                             if expected_player is player)
                assert sum(player.command_pools.values()) == (
                    sum(previous_pool_counts.values()) + player.round_command_gain())
            assert not self.turn_order.command_allocation
            assert all(player.pending_commands == 0 for player in expected_players)
            assert self.turn_order.active_player is expected_players[0]
            self.on_draw()
            assert self.turn_button_hit is not None
            assert self.labels['pass_turn_button'].text == 'PASS'
            assert self.player_panel.player is expected_players[0]
            # Smoke-test the new trade modal, including Hacan's long-range option.
            sol_player = expected_players[0]
            hacan_player = next(player for player in expected_players if player.faction == 'hacan')
            self.player_panel.active = expected_players.index(sol_player)
            self.on_draw()
            trade_control = next(control for control in self.player_panel.controls
                                 if control.action == ('trade',))
            self.on_mouse_press(trade_control.left + trade_control.width / 2,
                                trade_control.bottom + trade_control.height / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            assert self.transaction_modal and self.labels['trade_title'].text == 'NEGOTIATE A TRANSACTION'
            assert hacan_player in self.transaction.eligible_partners(sol_player)
            sol_player.action_cards[:] = [self.action_card_deck.draw() for _ in range(7)]
            hacan_player.action_cards[:] = [self.action_card_deck.draw() for _ in range(7)]
            partner_hit = next(hit for hit in self.transaction_panel.hits
                               if hit[0] == ('partner', 'hacan'))
            self.on_mouse_press((partner_hit[1] + partner_hit[2]) / 2,
                                (partner_hit[3] + partner_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            assert 'trade_arbiter_header' in self.labels
            assert sum(hit[0][0] == 'toggle_card' for hit in self.transaction_panel.hits) == 14
            arcade.get_image().save(preview_dir / 'transaction-preview.png')
            cancel_hit = next(hit for hit in self.transaction_panel.hits if hit[0] == ('cancel',))
            self.on_mouse_press((cancel_hit[1] + cancel_hit[2]) / 2,
                                (cancel_hit[3] + cancel_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)

            # Sol's component action is selectable from the dashboard and reversible.
            self.on_draw()
            drop_control = next(control for control in self.player_panel.controls
                                if control.action == ('orbital_drop',))
            planet_control = next(control for control in self.player_panel.controls
                                  if control.action[0] == 'planet')
            planet_id = sol_player.planets[planet_control.action[1]].planet.planet_id
            target = next(tile for tile in self.board.values()
                          if tile.planet_owners.get(planet_id) == 'sol')
            before_count = sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                               unit.location.planet_id == planet_id for unit in target.units)
            before_strategy = sol_player.command_pools['strategic']
            self.on_mouse_press(drop_control.left + drop_control.width / 2,
                                drop_control.bottom + drop_control.height / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            planet_control = next(control for control in self.player_panel.controls
                                  if control.action[0] == 'planet')
            self.on_mouse_press(planet_control.left + planet_control.width / 2,
                                planet_control.bottom + planet_control.height / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            assert sol_player.command_pools['strategic'] == before_strategy - 1
            assert sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                       unit.location.planet_id == planet_id for unit in target.units) == before_count + 2
            self.on_key_press(arcade.key.Z, arcade.key.MOD_CTRL)
            assert sol_player.command_pools['strategic'] == before_strategy
            assert sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                       unit.location.planet_id == planet_id for unit in target.units) == before_count
            print(f'PASS: {len(self.board)} tile objects; movement, combat, round passing, refresh, and command allocation checked')
            self.close()

    def on_mouse_motion(self, x, y, dx, dy):
        if self.main_menu_visible:
            return
        self.roster.hover(x, y)
        self.player_panel.hover(x, y)
        if self.strategy_modal:
            self.strategy_panel.update_hover(x, y)
            self.strategy_hover_system = self.strategy_panel.hover_system
        else:
            self.strategy_hover_system = None
        if self.movement.session and self.movement.session.stage == 'movement':
            self.movement_panel.update_hover(x, y)
        else:
            self.movement_panel.update_hover(-1, -1)
        self.hover = None if self.focus_view else self.pick(x, y)
        hit = self.unit_renderer.hit_test(x, y) if x < self.width - self.sidebar and self.player_panel.HEIGHT <= y < self.height - 80 else None
        inventory_hit = self.system_panel.hit_test(x, y)
        self.hovered_units = tuple(u.unit_id for u in hit.placement.units) if hit else tuple(u.unit_id for u in inventory_hit.units) if inventory_hit else ()

    def on_mouse_press(self, x, y, button, modifiers):
        if self.main_menu_visible:
            if button == arcade.MOUSE_BUTTON_LEFT:
                action = self.main_menu.hit_test(x, y)
                if action:
                    if action[0] == 'map':
                        self.main_menu.player_count = action[1]
                    elif action[0] == 'faction':
                        self.main_menu.select_faction(action[1], action[2])
                    elif action[0] == 'start':
                        self.start_from_menu()
            return
        self.sync_turn_action()
        if self.transaction_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                self.handle_transaction_action(self.transaction_panel.hit_test(x, y))
            return
        if self.strategy_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                if self.strategy_panel.drag_header(x, y):
                    self.dragging_modal = 'strategy'
                    return
                action = self.strategy_panel.hit_test(x, y)
                if action:
                    self.handle_strategy_action(action)
                else:
                    self.strategy_tray_click(x, y)
            return
        if button == arcade.MOUSE_BUTTON_LEFT and self.inspector_toggle_hit and all((
                self.inspector_toggle_hit[0] <= x <= self.inspector_toggle_hit[1],
                self.inspector_toggle_hit[2] <= y <= self.inspector_toggle_hit[3])):
            self.toggle_inspector()
            return
        if self.roster.WIDTH > x and self.player_panel.HEIGHT <= y < self.height - 80:
            action = self.roster.hit_test(x, y)
            if button == arcade.MOUSE_BUTTON_LEFT and action and not self.movement.session:
                player = self.turn_order.players[action[1]]
                if action[0] == 'card' and player is self.turn_order.active_player:
                    self.strategy_view = True
                elif action[0] == 'player' and self.smoke:
                    self.player_panel.active = action[1]
                    self.player_panel.card_offset = 0
            return
        if button == arcade.MOUSE_BUTTON_RIGHT:
            if not self.movement.session:
                hit = next(((position, faction) for position, faction, cx, cy, radius in reversed(self.token_hits)
                            if math.dist((x, y), (cx, cy)) <= radius), None)
                position = self.selected if self.focus_view else self.pick(x, y)
                if hit:
                    self.token_context = hit
                    self.selected = hit[0]
                    self.focus_view = False
                    self.system_panel.reset()
                elif position is not None:
                    self.token_context = (position, None)
                    self.selected = position
                    self.focus_view = False
                    self.system_panel.reset()
                else:
                    self.token_context = None
            return
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        if self.token_context and self.token_context[0] != self.selected:
            self.token_context = None
        if self.movement.session and self.movement.session.stage in ('space_combat', 'ground_combat', 'retreat_selection'):
            if self.combat_panel.drag_header(x, y):
                self.dragging_modal = 'combat'
                return
            action = self.combat_panel.hit_test(x, y)
            if action:
                try:
                    if action[0] == 'assign_hit':
                        self.movement.assign_combat_hit(action[1], action[2])
                    elif action[0] == 'spend_munitions':
                        self.movement.spend_munitions(action[1])
                    elif action[0] == 'reroll_die':
                        self.movement.toggle_combat_reroll(action[1], action[2])
                    elif action[0] == 'reroll_dice':
                        self.movement.reroll_selected_combat_dice(action[1])
                    elif action[0] == 'advance':
                        self.movement.advance_combat()
                    elif action[0] == 'announce_retreat':
                        self.movement.announce_retreat()
                    elif action[0] == 'retreat_to':
                        self.movement.resolve_retreat(action[1])
                        self.movement_panel.reset()
                    self.movement_error = None
                except MovementError as error:
                    self.movement_error = str(error)
            return
        left = self.width - self.sidebar
        if self.movement.session:
            if self.movement.session.stage == 'production' and y < self.player_panel.HEIGHT:
                control = next((control for control in reversed(self.player_panel.controls)
                                if control.contains(x, y) and control.action[0] == 'planet'), None)
                if control:
                    planet_id = self.player_panel.player.planets[control.action[1]].planet.planet_id
                    self.movement.toggle_production_planet(planet_id)
                    return
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
                        elif action[0] == 'bombard':
                            self.movement.cycle_bombardment_target(action[1])
                        elif action[0] == 'resolve_bombardment':
                            self.movement.resolve_bombardment()
                            self.movement_panel.reset()
                        elif action[0] == 'overflow':
                            self.movement.toggle_overflow_ship(action[1])
                        elif action[0] == 'resolve_overflow':
                            self.movement.resolve_fleet_overflow()
                            self.movement_panel.reset()
                        elif action[0] == 'production_unit':
                            self.movement.adjust_production(action[1], action[2])
                        elif action[0] == 'production_trade_goods':
                            self.movement.change_production_trade_goods(action[1])
                        elif action[0] == 'establish':
                            self.movement.establish_control()
                            self.movement_panel.reset()
                            self.selected_units = ()
                        elif action[0] == 'produce':
                            self.movement.produce()
                            self.movement_panel.reset()
                            self.selected_units = ()
                        elif action[0] == 'skip_production':
                            self.movement.skip_production()
                            self.movement_panel.reset()
                            self.selected_units = ()
                    except MovementError as error:
                        self.movement_error = str(error)
                self.sync_turn_action()
            return
        if x < left and y < self.player_panel.HEIGHT:
            if self.orbital_drop_mode:
                panel_control = next((control for control in reversed(self.player_panel.controls)
                                      if control.contains(x, y)), None)
                if panel_control and panel_control.action[0] == 'orbital_drop':
                    self.orbital_drop_mode = False
                elif panel_control and panel_control.action[0] == 'planet':
                    active = self.turn_order.active_player
                    planet_id = active.planets[panel_control.action[1]].planet.planet_id
                    try:
                        self.movement.orbital_drop(active, planet_id)
                        self.orbital_drop_mode = False
                        self.movement_error = None
                    except MovementError as error:
                        self.movement_error = str(error)
                return
            player_control = next((control for control in reversed(self.player_panel.controls)
                                   if control.contains(x, y) and control.action[0] == 'player'), None)
            active_player_index = (self.player_panel.players.index(self.turn_order.active_player)
                                   if self.turn_order.active_player else -1)
            if not self.smoke and player_control and player_control.action[1] != active_player_index:
                return
            panel_control = next((control for control in reversed(self.player_panel.controls)
                                  if control.contains(x, y)), None)
            if self.turn_order.command_allocation and (
                    not panel_control or panel_control.action[0] not in ('pool', 'pending')):
                return
            if panel_control and panel_control.action[0] in ('pool', 'pending') and not self.turn_order.command_allocation and not self.smoke:
                return
            if panel_control and panel_control.action[0] == 'orbital_drop':
                self.orbital_drop_mode = True
                return
            if panel_control and panel_control.action[0] == 'trade':
                try:
                    self.transaction.open()
                    self.movement_error = None
                except TransactionError as error:
                    self.movement_error = str(error)
                return
            self.player_panel.handle_click(x, y)
            if self.turn_order.command_allocation and self.player_panel.player and \
                    self.player_panel.player.pending_commands == 0:
                finished = self.turn_order.finish_player_command_allocation()
                if finished and self.turn_order.strategy_enabled:
                    self.turn_order.begin_strategy_phase()
                    self.strategy_view = True
                self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
                self.player_panel.source_pool = None
                self.player_panel.card_offset = 0
            return
        if self.turn_button_hit and self.turn_button_hit[0] <= x <= self.turn_button_hit[1] and \
                self.turn_button_hit[2] <= y <= self.turn_button_hit[3]:
            self.pass_turn()
            return
        if self.control_toggle_hit and self.control_toggle_hit[0] <= x <= self.control_toggle_hit[1] and \
                self.control_toggle_hit[2] <= y <= self.control_toggle_hit[3]:
            self.show_planet_control = not self.show_planet_control
            return
        if left - 225 <= x <= left - 118 and self.height - 56 <= y <= self.height - 24:
            self.toggle_focus()
            return
        if x >= left:
            if self.system_panel.strategy_tab_hit and all((
                    self.system_panel.strategy_tab_hit[0] <= x <= self.system_panel.strategy_tab_hit[1],
                    self.system_panel.strategy_tab_hit[2] <= y <= self.system_panel.strategy_tab_hit[3])):
                self.strategy_view = True
                self.on_draw()
                return
            add = self.system_panel.add_token_hit
            if self.token_context and self.token_context[1] is None and add and \
                    add[0] <= x <= add[1] and add[2] <= y <= add[3]:
                player = self.player_panel.player
                try:
                    self.movement.add_command_token(player, self.token_context[0])
                except MovementError as error:
                    self.movement_error = str(error)
                self.token_context = None
                self.system_panel.reset()
                return
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
                    if self.turn_order.command_allocation and not self.smoke:
                        raise MovementError('Finish command allocation before taking an action.')
                    if self.turn_order.active_player and self.turn_order.active_player.pending_commands and not self.smoke:
                        raise MovementError('Allocate all new command tokens before taking an action.')
                    if self.turn_order.action_used and not self.smoke:
                        raise MovementError('Your action is complete. Pass the turn to continue.')
                    self.movement.activate(self.player_panel.player, picked)
                    self.movement_panel.reset()
                    self.movement_error = None
                except MovementError as error:
                    self.movement_error = str(error)
                self.last_click = (None, 0.0)
            else:
                self.last_click = (picked, now)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.main_menu_visible:
            return
        if self.transaction_modal:
            return
        if self.dragging_modal:
            if buttons & arcade.MOUSE_BUTTON_LEFT:
                panel = self.strategy_panel if self.dragging_modal == 'strategy' else self.combat_panel
                panel.move(dx, dy)
                if self.dragging_modal == 'strategy':
                    self.strategy_panel.update_hover(x, y)
                    self.strategy_hover_system = self.strategy_panel.hover_system
            else:
                self.dragging_modal = None
            return
        if self.strategy_modal or x < self.roster.WIDTH:
            return
        if not self.focus_view and buttons & (arcade.MOUSE_BUTTON_RIGHT | arcade.MOUSE_BUTTON_MIDDLE) and x < self.width - self.sidebar and y >= self.player_panel.HEIGHT:
            scale = self.fit_scale * self.zoom
            self.map_center[0] -= dx / scale
            self.map_center[1] -= dy / scale

    def on_mouse_release(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            self.dragging_modal = None

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if self.main_menu_visible:
            return
        if self.transaction_modal:
            return
        if self.strategy_modal:
            self.strategy_panel.page = max(0, self.strategy_panel.page - int(scroll_y))
            return
        if x < self.roster.WIDTH and y >= self.player_panel.HEIGHT:
            if self.roster.hovered is not None:
                self.roster.detail_offset = max(0, self.roster.detail_offset - int(scroll_y * 3))
            else:
                self.roster.offset = max(0, self.roster.offset - int(scroll_y))
            return
        if x < self.width - self.sidebar and y < self.player_panel.HEIGHT:
            return
        if x >= self.width - self.sidebar:
            if not self.inspector_visible:
                return
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
        if self.main_menu_visible:
            if symbol in (arcade.key.ENTER, arcade.key.RETURN):
                self.start_from_menu()
            return
        self.sync_turn_action()
        if self.transaction_modal:
            if symbol == arcade.key.ESCAPE:
                self.transaction.cancel()
            return
        if symbol == arcade.key.ESCAPE and self.orbital_drop_mode:
            self.orbital_drop_mode = False
            self.movement_error = None
            return
        if self.strategy_modal or self.strategy.session:
            if symbol == arcade.key.ESCAPE:
                if not self.strategy.session and not self.turn_order.strategy_selection:
                    self.strategy_view = False
                elif self.strategy.session and self.strategy.session.stage == 'leadership':
                    self.strategy.reset_payment()
            return
        if symbol == arcade.key.Z and modifiers & arcade.key.MOD_CTRL:
            if (self.movement.session or self.turn_order.action_used or self.smoke) and self.movement.undo():
                self.movement_error = None
                self.movement_panel.reset()
                self.selected_units = ()
                self.sync_turn_action()
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
    parser.add_argument('--map', type=Path)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--menu-smoke-test', action='store_true')
    args = parser.parse_args()
    map_path = args.map or ROOT / 'maps/three_player.json'
    if args.validate:
        _, board = load_board(map_path)
        for position in board:
            assert nearest_hex(*world(position)) == position
        print(f'PASS: {len(board)} tile objects, unique coordinates, all images present')
        return
    BoardWindow(smoke=args.smoke_test or args.menu_smoke_test, map_path=map_path,
                show_menu=args.menu_smoke_test or (args.map is None and not args.smoke_test),
                menu_smoke_test=args.menu_smoke_test)
    arcade.run()


if __name__ == '__main__':
    main()
