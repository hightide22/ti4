from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import arcade
from ui_theme import (BG, PANEL, SHELL, INK, MUTED, ACCENT, GOLD, DANGER, SIDEBAR_WIDTH, FONT,
                      VARIANT, button)

from board import Board, Tile, load_board
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profile
from unit_view import UnitRenderer
from system_panel import SystemPanel
from player import PlanetCard, create_players
from player_panel import PlayerPanel
from movement import MovementController, MovementError, Snapshot, UndoEntry
from movement_panel import MovementPanel
from combat_panel import CombatPanel
from turn_order import TurnOrder
from strategy_panel import StrategyPanel, STRATEGY_CARDS
from strategic_action import StrategyController
from player_roster import PlayerRoster
from main_menu import MainMenu
from transactions import TransactionController, TransactionError
from transaction_panel import TransactionPanel
from action_card_deck import ActionCardDeck
from technology_panel import TechnologyPanel
from technology_actions import TechnologyActions
from technology import restore_infantry_on_cards
from unit_supply import can_place_infantry

from action_cards import ActionCardController
from action_card_panel import ActionCardPanel
from game_ai import GameAI
from exploration import ExplorationController, ATTACHMENT_TOKEN_STEMS
from exploration_panel import ExplorationPanel
from relic_action_panel import RelicActionPanel
from leader_panel import LeaderPanel
from leaders import (leader_available, ready_leaders, refresh_leader_unlocks,
                     record_scored_objective)
from leader_effects import (clear_round_hero_effects, use_cabal_hero,
                            use_arborec_agent, use_cabal_agent, use_xxcha_agent,
                            use_empyrean_hero, use_keleres_harka_hero,
                            use_ghost_agent, use_l1z1x_agent, use_naaz_agent, use_naaz_hero,
                            use_nomad_hero, use_saar_agent, l1z1x_agent_mech_available,
                            use_hacan_agent, use_muaat_agent, use_titans_hero)
from pok_factions import keleres_council_patronage, naaz_fabrication

ROOT = Path(__file__).resolve().parent
DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
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
        self.source_path = tile.image_path


class BoardWindow(arcade.Window):
    def __init__(self, smoke=False, map_path=ROOT / 'maps/three_player.json',
                 show_menu=False, menu_smoke_test=False, technology_smoke_test=False):
        display_width, display_height = arcade.get_display_size()
        super().__init__(min(1440, display_width), min(960, display_height),
                         'Twilight Imperium IV', resizable=True, vsync=True)
        self.set_minimum_size(min(1120, display_width), min(720, display_height))
        self.maximize()
        self.smoke = smoke
        self.menu_smoke_test = menu_smoke_test
        self.technology_smoke_test = technology_smoke_test
        self.main_menu = MainMenu()
        self.main_menu_visible = show_menu
        self.frames = 0
        rng = random.Random(4)
        self.stars = [(rng.random(), rng.random(), rng.randrange(45, 100)) for _ in range(190)]
        self.configure_game(map_path, self.main_menu.active_factions)

    def configure_game(self, map_path, factions):
        self.immediate_command_allocation = False
        self.agent_production_owner = None
        map_config = json.loads(Path(map_path).read_text(encoding='utf-8'))
        slot_count = sum(bool(entry.get('faction')) for entry in map_config['tiles'])
        self.map_config, self.board = load_board(
            map_path, factions if len(factions) == slot_count else None)
        self.tile_sprites = {tile: TileSprite(tile) for tile in self.board.values()}
        self.labels = {}
        self.label_specs = {}
        self.mouse_position = (-1, -1)
        self.unit_renderer = UnitRenderer()
        self.unit_renderer.blocking_layouts = self.smoke
        self.system_panel = SystemPanel()
        self.player_panel = PlayerPanel(create_players(self.board, self.map_config))
        self.action_card_deck = ActionCardDeck()
        self.turn_order = TurnOrder(self.player_panel.players, strategy_enabled=not self.smoke)
        self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
        self.strategy_panel = StrategyPanel()
        self.technology_panel = TechnologyPanel()
        self.technology_view = False
        self.dragging_modal = None
        self.strategy_hover_system = None
        self.strategy_view = False
        self.roster = PlayerRoster()
        self.inspector_visible = True
        self.inspector_toggle_hit = None
        self.show_planet_control = True
        self.control_toggle_hit = None
        self.turn_history_size = 0
        self.turn_history_action_count = 0
        self.last_infantry_return_turn = None
        self.turn_button_hit = None
        self.ai_pause_hit = None
        self.ai_speed_hit = None
        self.action_card_button_hit = None
        self.movement = MovementController(self.board, self.player_panel.players)
        self.technology_actions = TechnologyActions(self.board, self.movement, self.turn_order)
        self.action_cards = ActionCardController(self.player_panel.players, self.movement,
                                                 deck=self.action_card_deck.cards,
                                                 turn=self.turn_order)
        self.action_card_panel = ActionCardPanel()
        self.exploration = ExplorationController(self.board, self.player_panel.players,
                                                 action_cards=self.action_cards)
        self.exploration.secret_objectives.deal_starting_hands()
        self.exploration_panel = ExplorationPanel()
        self.relic_action_panel = RelicActionPanel()
        self.leader_panel = LeaderPanel()
        self.hero_frontier_queue = []
        self.naaz_hero_pending = False
        self.naaz_hero_hits = []
        self.legendary_modal = False
        self.legendary_choice = None
        self.legendary_page = 0
        self.legendary_hits = []
        self.exploration_button_hit = None
        self.leader_button_hit = None
        self.movement.exploration = self.exploration
        self.strategy = StrategyController(self.board, self.turn_order, self.movement,
                                           self.action_cards, exploration=self.exploration)
        self.movement.on_commodities_replenished = self.strategy.note_commodities_replenished
        self.patronage_round = 0
        self.apply_council_patronage()
        self.ai = GameAI(self, set())
        self.movement.exploration_is_bot = lambda player: self.smoke or self.ai.is_bot(player)
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
        self.star_forge_mode = False
        self.star_forge_target = None
        self.star_forge_hits = []
        self.mitosis_player = None
        self.selected = (0, 0) if (0, 0) in self.board else next(iter(self.board))
        self.hover = None
        self.zoom = self.target_zoom = 1.0
        self.map_center = [0.0, 0.0]
        self.target_map_center = [0.0, 0.0]
        self.fit()

    def start_from_menu(self):
        count = self.main_menu.player_count
        map_path = ROOT / ('maps/three_player.json' if count == 3 else 'maps/four_player.json')
        self.configure_game(map_path, self.main_menu.active_factions)
        if self.main_menu.vs_ai:
            human = self.main_menu.active_factions[self.main_menu.human_slot]
            self.ai.bot_factions = {faction for faction in self.main_menu.active_factions
                                    if faction != human}
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
        self.target_map_center = center[:]
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
        self.target_map_center = self.map_center[:]
        self.zoom = self.target_zoom = 1.0

    def player_system_positions(self, player):
        """List systems containing a player's planets, units, home system or command token."""
        if player is None:
            return []
        faction = player.faction
        return [tile.position for tile in self.board.values()
                if (tile.player == faction or faction in tile.planet_owners.values() or
                    faction in tile.command_tokens or
                    any(unit.owner == faction for unit in tile.units))]

    def frame_player_systems(self, player):
        """Smoothly frame every map system that currently contains this player's assets."""
        positions = self.player_system_positions(player)
        if not positions:
            return
        coords = [world(position) for position in positions]
        xs, ys = zip(*coords)
        bounds_width = max(xs) - min(xs) + 2
        bounds_height = max(ys) - min(ys) + math.sqrt(3)
        available_width = max(1, self.width - self.sidebar - self.roster.WIDTH - 80)
        available_height = max(1, self.height - 180 - self.player_panel.HEIGHT)
        required_scale = min(available_width / bounds_width, available_height / bounds_height)
        self.target_zoom = min(1.6, max(.55, required_scale / self.fit_scale))
        self.target_map_center = [(max(xs) + min(xs)) / 2,
                                  (max(ys) + min(ys)) / 2]
        center = self.target_map_center
        self.selected = min(positions, key=lambda position: math.dist(world(position), center))
        self.system_panel.reset()
        self.focus_view = False
        self.focus_zoom = 1.0

    def frame_action_route(self, source_position, target_position):
        """Keep both ends of an AI move visible while its action resolves."""
        start, end = world(source_position), world(target_position)
        self.target_map_center = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
        available_width = max(1, self.width - self.sidebar - self.roster.WIDTH - 100)
        available_height = max(1, self.height - 200 - self.player_panel.HEIGHT)
        required_scale = min(available_width / (abs(start[0] - end[0]) + 2),
                             available_height / (abs(start[1] - end[1]) + math.sqrt(3)))
        self.target_zoom = min(1.7, max(.65, required_scale / self.fit_scale))
        self.selected = target_position
        self.focus_view = False

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

    def text(self, key, value, x, y, size=14, color=INK, width=None, max_width=None):
        heading = isinstance(key, str) and any(part in key for part in ('title', 'header', 'system_name', 'move_target'))
        if key not in self.labels:
            self.labels[key] = arcade.Text('', x, y, color, size, font_name=FONT,
                                          bold=heading, width=width, multiline=width is not None)
        label = self.labels[key]
        label.x, label.y, label.color = x, y, color
        spec = (value, size, width, max_width)
        if self.label_specs.get(key) != spec:
            label.text = value
            label.font_size = size
            label.multiline = width is not None
            if width is not None:
                label.width = max(1, int(width))
            if max_width and label.content_width > max_width:
                label.font_size = max(8, size * max_width / label.content_width)
            self.label_specs[key] = spec
        label.draw()

    def outline(self, position, color, thickness):
        x, y = self.screen(position)
        radius = self.fit_scale * self.zoom * .98
        points = [(x + radius * math.cos(math.pi * i / 3), y + radius * math.sin(math.pi * i / 3)) for i in range(6)]
        arcade.draw_polygon_outline(points, color, thickness)

    def draw_ai_activation_highlight(self):
        """Mark the newly activated system through the movement transition."""
        ai = self.ai
        position = ai.activation_flash_position
        if (position not in self.board or ai.activation_flash_remaining <= 0 or
                ai.activation_flash_duration <= 0):
            return
        fade = min(1.0, ai.activation_flash_remaining / ai.activation_flash_duration)
        x, y = self.screen(position)
        radius = self.fit_scale * self.zoom * .98

        def corners(scale):
            return [(x + radius * scale * math.cos(math.pi * index / 3),
                     y + radius * scale * math.sin(math.pi * index / 3))
                    for index in range(6)]
        arcade.draw_polygon_filled(corners(1), (*GOLD, round(27 * fade)))
        arcade.draw_polygon_outline(corners(1.035), (*GOLD, round(95 * fade)), 7)
        arcade.draw_polygon_outline(corners(1.01), (*GOLD, round(245 * fade)), 3)

    def draw_tile(self, tile, x, y, width, detailed=False, scope='main', interactive=True):
        sprite = self.tile_sprites[tile]
        if sprite.source_path != tile.image_path:
            sprite.texture = arcade.load_texture(tile.image_path)
            sprite.source_path = tile.image_path
        texture = sprite.texture
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
            center_x = tile_left + planet.center[0] * scale
            center_y = tile_top - planet.center[1] * scale
            marker_size = max(8, width * .075)
            if planet.planet_id in tile.sleeper_tokens:
                self.player_panel.image(
                    'tokens/token_sleeper.png',
                    center_x - planet.radius * .62 * scale,
                    center_y - planet.radius * .62 * scale,
                    marker_size)
            if self.exploration:
                for index, alias in enumerate(self.exploration.attachments.get(planet.planet_id, ())):
                    stem = ATTACHMENT_TOKEN_STEMS.get(alias, alias.replace('_', ''))
                    image_path = f'attachment_token/attachment_{stem}.png'
                    if not (ROOT / 'assets/source/asyncti4/src/main/resources' /
                            image_path).is_file():
                        continue
                    self.player_panel.image(
                        image_path,
                        center_x - planet.radius * .62 * scale + (index % 2) * marker_size * .55,
                        center_y + planet.radius * .62 * scale - (index // 2) * marker_size * .55,
                        marker_size)
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
        self.restore_infantry_on_cards()
        if self.smoke:
            return
        history_size = len(self.movement.history)
        action_count = sum(entry.counts_as_action for entry in self.movement.history)
        if action_count > self.turn_history_action_count and self.turn_order.can_take_action:
            self.turn_order.mark_action_completed()
        elif action_count < self.turn_history_action_count and self.turn_order.action_used and not self.movement.session:
            self.turn_order.actions_used = max(0, self.turn_order.actions_used - 1)
            master_plan = self.action_cards._master_plan_waiting
            self.turn_order.action_used = bool(
                self.turn_order.actions_used or
                (master_plan and master_plan[0] == self.turn_order.turn_serial))
        self.turn_history_size = history_size
        self.turn_history_action_count = action_count

    def restore_agent_production_view(self):
        if self.agent_production_owner is None or self.movement.session:
            return
        self.agent_production_owner = None
        active = self.turn_order.active_player
        if active is not None:
            self.player_panel.active = self.player_panel.players.index(active)
            self.player_panel.card_offset = 0
            self.frame_player_systems(active)
            self.system_panel.reset()

    def restore_infantry_on_cards(self):
        turn = self.turn_order
        if (turn.strategy_selection or turn.command_allocation or
                self.last_infantry_return_turn == turn.turn_serial or not turn.active_player):
            return
        self.last_infantry_return_turn = turn.turn_serial
        restore_infantry_on_cards(turn.active_player, self.board)

    @property
    def strategy_modal(self):
        if self.bot_actor:
            return False
        if self.strategy.pending_cabal_replenishments:
            return False
        if self.leader_panel.reaction_owner_faction:
            return False
        return (self.turn_order.strategy_selection or self.turn_order.qdn_pending or
                bool(self.strategy.session and self.strategy.session.stage != 'production') or
                bool(self.strategy_view and not self.movement.session))

    @property
    def transaction_modal(self):
        return bool(self.transaction.session)

    @property
    def technology_modal(self):
        if self.bot_actor:
            return False
        return self.technology_view or bool(self.strategy.session and
                                            self.strategy.session.stage == 'technology')

    @property
    def bot_actor(self):
        """Whether the current decision belongs to an automated player."""
        if self.mitosis_player and not self.ai.is_bot(self.mitosis_player):
            return False
        if self.smoke or not self.ai.bot_factions:
            return False
        actor = (self.movement.session.player if self.movement.session else
                 self.strategy.player if self.strategy.session else
                 self.turn_order.active_player)
        return self.ai.is_bot(actor)

    def human_card_participants(self):
        participants = self.action_cards.participants(self.movement.session)
        return [player for player in participants if not self.ai.is_bot(player)]

    def draw_bot_sidebar(self, left):
        """Show progress without exposing controls for the bot's decisions."""
        session = self.movement.session
        self.movement_panel.hits.clear()
        self.movement_panel.buttons.clear()
        self.movement_panel.route_hits.clear()
        self.movement_panel.source_hits.clear()
        self.system_panel.hits.clear()
        self.system_panel.remove_token_hit = None
        self.system_panel.add_token_hit = None
        self.system_panel.strategy_tab_hit = None
        self.system_panel.technology_tab_hit = None
        self.strategy_panel.hits.clear()
        self.technology_panel.hits.clear()
        if not self.inspector_visible:
            return
        actor = (session.player if session else self.strategy.player if self.strategy.session
                 else self.turn_order.active_player)
        phase = (session.stage if session else self.strategy.session.stage if self.strategy.session else
                 'strategy draft' if self.turn_order.strategy_selection else
                 'command allocation' if self.turn_order.command_allocation else 'choosing action')
        self.text('bot_sidebar_title', 'AI ACTION IN PROGRESS', left + 18,
                  self.height - 143, 14, ACCENT)
        self.text('bot_sidebar_faction', actor.faction.upper(), left + 18,
                  self.height - 175, 11, INK)
        if session:
            self.text('bot_sidebar_target', session.target.name, left + 18,
                      self.height - 203, 15, INK, self.sidebar - 36)
        self.text('bot_sidebar_stage', phase.replace('_', ' ').upper(),
                  left + 18, self.height - 236, 10, MUTED)

    def sync_strategy_actor(self):
        if self.strategy.session and self.strategy.session.stage in (
                'construction', 'diplomacy_system', 'warfare_system'):
            self.focus_view = False
            if getattr(self, 'inspector_visible', True) is False and hasattr(self, 'toggle_inspector'):
                self.toggle_inspector()
        player = self.strategy.player
        index = self.player_panel.players.index(player)
        if self.player_panel.active != index:
            self.player_panel.active = index
            self.player_panel.card_offset = 0
            self.player_panel.source_pool = None
            self.player_panel.hovered_planet = None
        self.strategy_view = bool(self.strategy.session and self.strategy.session.stage != 'production')
        self.turn_history_size = len(self.movement.history)
        self.turn_history_action_count = sum(entry.counts_as_action for entry in self.movement.history)

    def handle_strategy_action(self, action):
        if not action:
            return
        kind, *args = action
        ctl, session = self.strategy, self.strategy.session
        try:
            if kind == 'choose_strategy':
                self.turn_order.choose_strategy_card(args[0])
                self.sync_strategy_actor()
            elif kind == 'qdn_exchange':
                self.turn_order.qdn_exchange(args[0], args[1], args[2])
                self.sync_strategy_actor()
            elif kind == 'qdn_skip':
                self.turn_order.skip_qdn()
                self.sync_strategy_actor()
            elif kind == 'close':
                self.strategy_view = False
            elif kind == 'page':
                self.strategy_panel.page = max(0, self.strategy_panel.page + args[0])
            elif kind == 'start':
                self.action_card_panel.open = False
                ctl.start(args[0])
                self.sync_strategy_actor()
            elif session:
                if kind == 'accept':
                    ctl.accept_secondary()
                elif kind == 'accept_brilliant':
                    ctl.accept_secondary(brilliant=True)
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
                elif kind == 'confirm_warfare_removal':
                    ctl.confirm_warfare_removal()
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
                        session.selected_system = None
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

    def handle_technology_action(self, action):
        if not action:
            return
        kind, *args = action
        panel, ctl = self.technology_panel, self.strategy
        research = bool(ctl.session and ctl.session.stage == 'technology')
        try:
            if kind == 'close' and not research:
                self.technology_actions.cancel_transit()
                self.technology_view = False
            elif kind == 'tab':
                if self.technology_actions.transit:
                    raise ValueError('Finish or close Transit Diodes first.')
                panel.tab = args[0]
                panel.preview_alias = None
                panel.scroll = 0
                panel.action_page = 0
            elif kind in ('unit', 'select'):
                if self.technology_actions.transit and (kind != 'select' or args[0] != 'td'):
                    raise ValueError('Finish or close Transit Diodes first.')
                tech = None
                if kind == 'unit':
                    from technology import unit_upgrade
                    panel.preview_alias = args[0]
                    tech = unit_upgrade(ctl.player if research else self.player_panel.player, args[0])
                else:
                    from technology import technology_catalog
                    panel.preview_alias = args[0]
                    tech = technology_catalog()[args[0]]
                panel.action_page = 0
                if research:
                    ctl.select_technology(tech['alias'] if tech and tech['alias'] not in ctl.player.technologies
                                          else None)
            elif kind == 'mode':
                panel.planet_mode = args[0]
            elif kind == 'riftmeld' and research:
                ctl.toggle_riftmeld()
            elif kind == 'goods' and research:
                ctl.change_goods(args[0])
            elif kind == 'research' and research:
                ctl.research_selected()
                panel.preview_alias = None
                self.sync_strategy_actor()
            elif kind == 'propagation' and research:
                ctl.resolve_propagation()
                panel.preview_alias = None
                self.sync_strategy_actor()
            elif kind == 'skip' and research:
                ctl.continue_stage()
                panel.preview_alias = None
                self.sync_strategy_actor()
            elif kind == 'action_page' and not research:
                panel.action_page = max(0, panel.action_page + args[0])
            elif kind == 'transit_unit' and not research:
                self.technology_actions.select_transit_unit(self.player_panel.player, args[0])
                panel.action_page = 0
            elif kind == 'transit_planet' and not research:
                self.technology_actions.place_transit_unit(self.player_panel.player, args[0])
                panel.action_page = 0
            elif kind == 'transit_back' and not research:
                self.technology_actions.transit.selected_unit = None
                panel.action_page = 0
            elif kind == 'transit_finish' and not research:
                self.technology_actions.finish_transit(self.player_panel.player)
                self.technology_view = False
                self.sync_turn_action()
            elif kind == 'use' and not research:
                self.technology_actions.use(self.player_panel.player, args[0], args[1])
                self.technology_view = False
                self.sync_turn_action()
            self.movement_error = None
        except (ValueError, StopIteration) as error:
            self.movement_error = str(error) or 'This choice is no longer available.'

    def exploration_decision_owner(self):
        """The player who must answer the currently open exploration decision."""
        if self.exploration.terragenesis_pending is not None:
            return self.exploration.players[self.exploration.terragenesis_pending.faction]
        if self.exploration.pending is not None:
            return self.exploration.players[self.exploration.pending.faction]
        if self.exploration.freelancer_offer is not None:
            return self.exploration.players[self.exploration.freelancer_offer[0]]
        for player in self.turn_order.players:
            if player.faction in self.exploration.secret_objectives.pending_returns:
                return player
        return self.turn_order.active_player

    def _component_action_snapshot(self):
        player = self.turn_order.active_player
        if (player is None or self.ai.is_bot(player) or not self.turn_order.can_take_action or
                self.movement.session or self.strategy.session or self.exploration.pending or
                self.turn_order.strategy_selection or self.turn_order.command_allocation):
            raise ValueError('Finish the current decision before using this action.')
        return Snapshot.capture(self.board, player, self.player_panel.players,
                                self.action_cards, self.exploration)

    def _finish_component_action(self, snapshot):
        self.movement.history.append(UndoEntry(snapshot))
        self.sync_turn_action()
        for player in self.player_panel.players:
            refresh_leader_unlocks(player, self.board, self.player_panel.players,
                                   self.exploration)

    def use_relic_action(self, alias):
        try:
            self._component_action_snapshot()
            if not self.exploration.relics.is_ready(self.turn_order.active_player.faction, alias):
                raise ValueError('This relic is unavailable.')
            command = self.relic_action_panel.begin(alias, self.turn_order.active_player)
            self.exploration_panel.close()
            if command:
                self.resolve_relic_command(command)
            self.movement_error = None
        except ValueError as error:
            self.movement_error = str(error)

    def resolve_relic_command(self, command):
        if command and command[0] == 'discard_to_limit':
            player = self.turn_order.active_player
            chosen = command[2]
            required = max(0, len(player.action_cards) - 7)
            if (player.faction != command[1] or player.faction == 'yssaril' or
                    required == 0 or len(chosen) != required or
                    len({index for index, _ in chosen}) != required or
                    any(index < 0 or index >= len(player.action_cards) or
                        player.action_cards[index] != alias for index, alias in chosen)):
                self.relic_action_panel.begin_discard(player)
                self.movement_error = 'Choose exactly the excess action cards to discard.'
                return
            for index, alias in sorted(chosen, reverse=True):
                player.action_cards.pop(index)
                self.action_cards.discard.append(alias)
            self.movement_error = None
            return
        if not command or command[0] != 'use_relic':
            return
        player = self.turn_order.active_player
        snapshot = self._component_action_snapshot()
        alias = command[1]
        try:
            if not self.exploration.relics.is_ready(player.faction, alias):
                raise ValueError('This relic is unavailable.')
            if alias == 'dynamiscore':
                self.exploration.relics.use_dynamis_core(player)
            elif alias == 'codex':
                self.exploration.relics.use_codex(player, self.action_cards, command[2])
            elif alias == 'nanoforge':
                self.exploration.use_nano_forge(player, command[2])
            elif alias == 'stellarconverter':
                self.exploration.use_stellar_converter(player, command[2])
            elif alias == 'titanprototype':
                recipient = self.exploration.players[command[2]]
                self.exploration.use_titan_prototype(
                    player, recipient, planet_id=command[3], kind=command[4],
                    planet_ids=command[5], trade_goods=command[6])
            else:
                raise ValueError('This relic has no action window.')
        except (ValueError, KeyError) as error:
            snapshot.restore()
            self.movement_error = str(error)
            return
        self.relic_action_panel.close()
        self._finish_component_action(snapshot)
        if alias == 'codex':
            self.relic_action_panel.begin_discard(player)
        self.movement_error = None

    def advance_hero_frontier(self):
        while (self.hero_frontier_queue and self.exploration.pending is None and
               self.exploration.freelancer_offer is None and
               not self.exploration.secret_objectives.pending_returns and
               not (self.movement.session and self.movement.session.stage in
                    ('capacity_overflow', 'fleet_overflow'))):
            player, position = self.hero_frontier_queue.pop(0)
            outcome = self.exploration.explore_frontier(player, self.board[position])
            self.movement.exploration_outcomes.append((player.faction, outcome))

    def prompt_activation_agent(self, session):
        """Open printed agent windows triggered by activating this system."""
        from leaders import leader_available
        if not session.activation_agent_queue and not session.activation_agent_resolved:
            active = session.player
            mahact = next((player for player in self.player_panel.players
                           if player.faction == 'mahact'), None)
            # Il Na Viroset suppresses all "when/after a player activates" abilities
            # for a Mahact activation.
            if mahact and active.faction == 'mahact' and leader_available(
                    mahact, 'mahactcommander'):
                session.activation_agent_resolved = True
                return False
            owners = {player.faction: player for player in self.player_panel.players}
            infantry = any(unit.owner == active.faction and unit.kind == 'infantry' and
                           unit.location.region in (Region.PLANET, Region.TRANSPORT)
                           for unit in session.target.units)
            active_ships = any(unit.owner == active.faction and
                               UNIT_TYPES[unit.kind]['ship'] and
                               unit.location.region == Region.SPACE
                               for tile in self.board.values() for unit in tile.units)
            for alias, faction, eligible in (
                    ('saaragent', 'saar', active_ships),
                    ('ghostagent', 'ghost', any(str(wormhole).upper() != 'DELTA'
                                                for wormhole in session.target.wormholes)),
                    ('l1z1xagent', 'l1z1x', infantry and
                     l1z1x_agent_mech_available(active, session.target, self.board))):
                owner = owners.get(faction)
                if owner and eligible and leader_available(owner, alias):
                    session.activation_agent_queue.append((faction, alias))
        if not session.activation_agent_queue:
            session.activation_agent_owner = None
            session.activation_agent_alias = None
            session.activation_agent_resolved = True
            return False
        faction, alias = session.activation_agent_queue[0]
        owner = next((player for player in self.player_panel.players
                      if player.faction == faction), None)
        if owner is None:
            session.activation_agent_queue.pop(0)
            return self.prompt_activation_agent(session)
        session.activation_agent_owner = faction
        session.activation_agent_alias = alias
        if self.ai.is_bot(owner):
            return False
        self.leader_panel.open_for_reaction(owner, alias)
        return True

    def skip_activation_agent_window(self):
        """Decline the pending agent reaction and resume the activation."""
        session = self.movement.session
        if session and session.activation_agent_owner:
            if session.activation_agent_queue:
                session.activation_agent_queue.pop(0)
            session.activation_agent_owner = None
            session.activation_agent_alias = None
            self.leader_panel.close()
            self.prompt_activation_agent(session)
        elif (self.strategy.pending_cabal_replenishments and
              self.strategy.pending_cabal_replenishments[0][0] == 'cabal'):
            self.strategy.pending_cabal_replenishments.pop(0)
            self.leader_panel.close()

    def sync_cabal_agent_reaction(self):
        """Pause for a human Cabal agent reaction after commodity replenishment."""
        pending = self.strategy.pending_cabal_replenishments
        if not pending or self.leader_panel.reaction_owner_faction:
            return
        owner = next((candidate for candidate in self.player_panel.players
                      if candidate.faction == pending[0][0]), None)
        if owner is None or self.ai.is_bot(owner):
            return
        self.leader_panel.open_for_reaction(owner, 'cabalagent')

    def finish_activation_agent_reaction(self, session):
        if session.activation_agent_queue:
            session.activation_agent_queue.pop(0)
        session.activation_agent_owner = None
        session.activation_agent_alias = None
        self.leader_panel.close()
        for player in self.player_panel.players:
            refresh_leader_unlocks(player, self.board, self.player_panel.players,
                                   self.exploration)
        self.prompt_activation_agent(session)

    def resolve_leader_action(self, command):
        if not command:
            return
        if command[0] == 'record_objective':
            player = self.turn_order.active_player
            if player is None or self.ai.is_bot(player) or self.movement.session:
                self.movement_error = 'Record an already scored objective for the active human player.'
                return
            snapshot = Snapshot.capture(self.board, player, self.player_panel.players,
                                        self.action_cards, self.exploration)
            record_scored_objective(player, secret=bool(command[1]))
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            self.movement_error = None
            return
        if command[0] != 'use':
            return
        alias = command[1]
        player = next((candidate for candidate in self.player_panel.players
                       if candidate.faction == self.leader_panel.reaction_owner_faction),
                      self.turn_order.active_player)
        if alias == 'cabalagent':
            pending = self.strategy.pending_cabal_replenishments
            if (player is None or player.faction != 'cabal' or not pending or
                    len(command) < 3):
                self.movement_error = 'The Cabal agent reaction is no longer available.'
                return
            _owner_faction, target_faction, commodity_value = pending[0]
            target = next((candidate for candidate in self.player_panel.players
                           if candidate.faction == target_faction), None)
            if target is None:
                self.movement_error = 'The replenishing player is no longer available.'
                return
            snapshot = None
            try:
                snapshot = Snapshot.capture(
                    self.board, player, self.player_panel.players,
                    self.action_cards, self.exploration)
                use_cabal_agent(player, target, command[2], self.board,
                                commodity_value=commodity_value)
            except (ValueError, KeyError) as error:
                if snapshot is not None:
                    snapshot.restore()
                self.movement_error = str(error)
                return
            pending.pop(0)
            self.leader_panel.close()
            self.movement_error = None
            self.sync_strategy_actor()
            return
        if alias == 'hacanagent':
            snapshot = None
            try:
                if player is None or player.faction != 'hacan' or len(command) < 3:
                    raise ValueError('Carth can be used by the active Hacan player.')
                snapshot = self._component_action_snapshot()
                effect = command[2]
                if not isinstance(effect, tuple) or len(effect) < 2 or effect[0] != 'hacan':
                    raise ValueError('Choose a Hacan agent effect.')
                beneficiary = None
                if effect[1] == 'replenish' and len(effect) == 3:
                    beneficiary = next(candidate for candidate in self.player_panel.players
                                       if candidate.faction == effect[2])
                elif effect[1] != 'gain':
                    raise ValueError('Choose whether to gain or replenish commodities.')
                use_hacan_agent(
                    player, replenish_player=beneficiary,
                    commodity_limit=self.exploration.relics.commodity_limit)
            except (ValueError, KeyError, StopIteration) as error:
                if snapshot is not None:
                    snapshot.restore()
                self.movement_error = str(error) or 'That Hacan agent choice is no longer available.'
                return
            if beneficiary is not None:
                self.strategy.note_commodities_replenished(beneficiary)
            self.leader_panel.close()
            self._finish_component_action(snapshot)
            self.movement_error = None
            return
        if alias == 'muaatagent':
            if (player is None or player.faction != 'muaat' or len(command) < 3 or
                    not isinstance(command[2], tuple) or len(command[2]) != 3):
                self.movement_error = 'Umbat needs a player and a system containing their War Sun or flagship.'
                return
            snapshot = None
            try:
                snapshot = self._component_action_snapshot()
                _effect, beneficiary_faction, position = command[2]
                beneficiary = next(candidate for candidate in self.player_panel.players
                                   if candidate.faction == beneficiary_faction)
                target = self.board[position]
                use_muaat_agent(player, beneficiary, target)
                self.movement.start_agent_production(beneficiary, target, snapshot)
            except (ValueError, KeyError, StopIteration) as error:
                if snapshot is not None:
                    snapshot.restore()
                self.movement_error = str(error) or 'Umbat target is no longer available.'
                return
            self.agent_production_owner = player.faction
            self.player_panel.active = self.player_panel.players.index(beneficiary)
            self.player_panel.card_offset = 0
            self.frame_player_systems(beneficiary)
            self.selected = target.position
            self.leader_panel.close()
            self.movement_error = None
            return
        if alias == 'l1z1xagent':
            session = self.movement.session
            if (player is None or player.faction != 'l1z1x' or session is None or
                    session.stage != 'movement'):
                self.movement_error = 'I48S can be used after you activate a system.'
                return
            snapshot = Snapshot.capture(self.board, session.player, self.player_panel.players,
                                        self.action_cards, self.exploration)
            try:
                use_l1z1x_agent(player, session.player, session.target,
                                command[2], self.board)
            except (ValueError, KeyError) as error:
                snapshot.restore()
                self.movement_error = str(error)
                return
            self.movement.refresh_activation_routes(session)
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            self.finish_activation_agent_reaction(session)
            self.movement_error = None
            return
        if alias == 'ghostagent':
            session = self.movement.session
            if (player is None or player.faction != 'ghost' or session is None or
                    session.stage != 'movement'):
                self.movement_error = 'The Ghost agent can be used after a system with a wormhole is activated.'
                return
            snapshot = Snapshot.capture(self.board, session.player, self.player_panel.players,
                                        self.action_cards, self.exploration)
            try:
                use_ghost_agent(player, session.target, self.board)
                session.ghost_wormhole_adjacency = True
                self.movement.refresh_activation_routes(session)
            except (ValueError, KeyError) as error:
                snapshot.restore()
                self.movement_error = str(error)
                return
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            self.finish_activation_agent_reaction(session)
            self.movement_error = None
            return
        if alias == 'saaragent':
            session = self.movement.session
            if (player is None or player.faction != 'saar' or session is None or
                    session.stage != 'movement'):
                self.movement_error = 'The Saar agent can be used when a player activates a system.'
                return
            snapshot = Snapshot.capture(self.board, session.player, self.player_panel.players,
                                        self.action_cards, self.exploration)
            try:
                move_value = use_saar_agent(player, session.player, self.board, command[2])
                ship = next(unit for tile in self.board.values() for unit in tile.units
                            if unit.unit_id == command[2])
                session.activation_move_values[ship.unit_id] = move_value
                self.movement.refresh_activation_routes(session)
            except (ValueError, KeyError, StopIteration) as error:
                snapshot.restore()
                self.movement_error = str(error)
                return
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            self.finish_activation_agent_reaction(session)
            self.movement_error = None
            return
        if alias in ('arborecagent', 'xxchaagent'):
            snapshot = None
            try:
                snapshot = self._component_action_snapshot()
                if alias == 'arborecagent':
                    _effect, faction, ship_id, replacement_kind = command[2]
                    target = next(candidate for candidate in self.player_panel.players
                                  if candidate.faction == faction)
                    use_arborec_agent(player, target, self.board, ship_id,
                                      replacement_kind)
                else:
                    _effect, planet_id, infantry_id = command[2]
                    use_xxcha_agent(player, self.board, planet_id,
                                    remove_infantry_id=infantry_id)
            except (ValueError, KeyError, StopIteration) as error:
                if snapshot is not None:
                    snapshot.restore()
                self.movement_error = str(error) or 'That target is no longer available.'
                return
            self.leader_panel.close()
            self._finish_component_action(snapshot)
            self.movement_error = None
            return
        if alias == 'naazagent':
            if (player is None or player.faction != 'naaz' or not self.turn_order.action_used or
                    self.movement.session or self.strategy.session):
                self.movement_error = 'Use this agent at the end of your turn.'
                return
            snapshot = Snapshot.capture(self.board, player, self.player_panel.players,
                                        self.action_cards, self.exploration)
            try:
                faction, position, planet_id = command[2]
                beneficiary = self.exploration.players[faction]
                outcome = use_naaz_agent(player, beneficiary, self.exploration,
                                         self.board[position], planet_id)
                self.movement.exploration_outcomes.append((beneficiary.faction, outcome))
                if outcome.resolved:
                    self.movement.after_planet_explored(
                        beneficiary, self.board[position], planet_id)
            except (ValueError, KeyError) as error:
                snapshot.restore()
                self.movement_error = str(error)
                return
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            self.leader_panel.close()
            self.movement_error = None
            return
        snapshot = None
        try:
            snapshot = self._component_action_snapshot()
            if alias == 'cabalhero':
                results = use_cabal_hero(player, self.board, self.exploration.rng)
                self.movement_error = f'Cabal hero rolled for {len(results)} ships.'
            elif alias == 'empyreanhero':
                tiles = use_empyrean_hero(player, self.exploration, self.board)
                self.hero_frontier_queue = [(player, tile.position) for tile in tiles]
                self.advance_hero_frontier()
            elif alias == 'keleresheroharka':
                use_keleres_harka_hero(player, self.action_card_deck)
            elif alias == 'naazhero':
                use_naaz_hero(player, self.exploration)
                self.naaz_hero_pending = True
            elif alias == 'nomadhero':
                use_nomad_hero(player)
            elif alias == 'titanshero':
                use_titans_hero(player)
            else:
                raise ValueError('This leader has no action effect in the current phase.')
        except (ValueError, KeyError) as error:
            if snapshot is not None:
                snapshot.restore()
            self.hero_frontier_queue.clear()
            self.movement_error = str(error)
            return
        self.leader_panel.close()
        self._finish_component_action(snapshot)
        if alias == 'keleresheroharka':
            self.relic_action_panel.begin_discard(player)
        if alias != 'cabalhero':
            self.movement_error = None

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
        elif session.stage == 'technology' and kind == 'planet':
            self.strategy.toggle_technology_planet(
                session.player.planets[args[0]].planet.planet_id,
                specialty=self.technology_panel.planet_mode == 'specialty')
        elif session.stage in ('allocate', 'warfare_allocate') and kind == 'pool':
            self.handle_strategy_action(('allocate', args[0]))

    def strategy_map_click(self, x, y):
        session = self.strategy.session
        if not session or session.stage not in (
                'construction', 'diplomacy_system', 'warfare_system'):
            return
        position = self.pick(x, y)
        if position is None:
            return
        if position not in self.strategy.selectable_systems():
            return
        try:
            self.selected = position
            self.focus_view = False
            self.system_panel.reset()
            if session.stage == 'warfare_system':
                token = next((hit for hit in reversed(self.token_hits)
                              if hit[0] == position and hit[1] == session.player.faction and
                              math.dist((x, y), (hit[2], hit[3])) <= hit[4]), None)
                if token:
                    if session.selected_system != position:
                        self.strategy.select_system(position)
                    self.strategy.select_warfare_token(position, token[1])
                else:
                    self.strategy.select_system(position)
            else:
                self.strategy.select_system(position)
            self.movement_error = None
            self.sync_strategy_actor()
        except ValueError as error:
            self.movement_error = str(error)

    def pass_turn(self):
        self.strategy_view = False
        self.hero_frontier_queue.clear()
        self.naaz_hero_pending = False
        self.legendary_modal = False
        self.leader_panel.close()
        self.relic_action_panel.close()
        if self.exploration.pending is None:
            self.exploration_panel.close()
        try:
            round_complete = self.turn_order.end_turn()
        except ValueError as error:
            self.movement_error = str(error)
            return
        if not self.turn_order.players:
            return
        if round_complete:
            self.clear_round_tokens()
            for tile in self.board.values():
                for unit in tile.units:
                    unit.damaged = False
            self.movement.history.clear()
            for player in self.turn_order.players:
                player.trade_goods += player.commodities
                player.commodities = 0
                self.movement.resolve_pillage(player)
                for card in player.planets:
                    card.exhausted = False
                player.exhausted_technologies.clear()
                player.exhausted_legendary_abilities.clear()
                ready_leaders(player)
                clear_round_hero_effects(player)
                for _ in range(2 if 'nm' in player.technologies else 1):
                    self.action_cards.draw(player, 1)
            self.exploration.relics.ready_all()
            arborec = next((player for player in self.turn_order.players
                            if player.faction == 'arborec' and
                            any(tile.planet_owners.get(planet.planet_id) == 'arborec' and
                                can_place_infantry(
                                    player, self.board, tile,
                                    UnitLocation(Region.PLANET, planet.planet_id))
                                for tile in self.board.values() for planet in tile.planets)), None)
            if arborec:
                if self.ai.is_bot(arborec):
                    options = [(tile, planet) for tile in self.board.values() for planet in tile.planets
                               if tile.planet_owners.get(planet.planet_id) == 'arborec' and
                               can_place_infantry(
                                   arborec, self.board, tile,
                                   UnitLocation(Region.PLANET, planet.planet_id))]
                    if options:
                        tile, planet = min(options, key=lambda entry: sum(
                            unit.owner == 'arborec' and unit.kind == 'infantry' and
                            unit.location.planet_id == entry[1].planet_id
                            for unit in entry[0].units))
                        self.movement.mitosis(arborec, planet.planet_id)
                else:
                    self.mitosis_player = arborec
                    self.player_panel.active = self.player_panel.players.index(arborec)
                    self.player_panel.card_offset = 0
                    self.frame_player_systems(arborec)
                    self.movement_error = None
                    self.system_panel.reset()
                    self.movement_panel.reset()
                    return
            self.finish_status_phase()
        self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
        self.frame_player_systems(self.turn_order.active_player)
        self.player_panel.source_pool = None
        self.player_panel.card_offset = 0
        self.player_panel.hovered_planet = None
        self.turn_history_size = len(self.movement.history)
        self.turn_history_action_count = sum(entry.counts_as_action for entry in self.movement.history)
        self.selected_units = ()
        self.token_context = None
        self.movement_error = None
        self.system_panel.reset()
        self.movement_panel.reset()

    def finish_status_phase(self):
        for player in self.turn_order.players:
            player.receive_round_commands()
        self.turn_order.begin_command_allocation()
        self.strategy_view = False
        if not self.turn_order.command_allocation and self.turn_order.strategy_enabled:
            self.turn_order.begin_strategy_phase()
            self.apply_council_patronage()
            self.strategy_view = True
        if self.turn_order.active_player:
            self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
            self.frame_player_systems(self.turn_order.active_player)

    def apply_council_patronage(self):
        if (not self.turn_order.strategy_selection or
                self.patronage_round == self.turn_order.round_number):
            return
        for player in self.turn_order.players:
            if player.faction.startswith('keleres'):
                keleres_council_patronage(player, self.exploration)
                self.strategy.note_commodities_replenished(player)
        self.patronage_round = self.turn_order.round_number

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
        self.board.flip_wormhole_nexus()
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
        if self.ai.activation_flash_remaining > 0 and self.ai.activation_flash_route:
            movement_route = self.ai.activation_flash_route
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
            self.draw_ai_activation_highlight()
        if self.strategy.session and self.strategy.session.stage in (
                'construction', 'diplomacy_system', 'ready_planets', 'warfare_system'):
            for position in self.strategy.selectable_systems():
                self.outline(position, GOLD, 4)
            selected_system = self.strategy.session.selected_system
            if selected_system is not None:
                self.outline(selected_system, ACCENT, 5)
            hovered_system = self.strategy_hover_system
            if hovered_system is not None and hovered_system != selected_system:
                self.outline(hovered_system, (112, 230, 245), 5)
            pending_system = self.strategy.session.pending_system
            if pending_system is not None:
                self.outline(pending_system, (255, 230, 128), 6)
        if self.relic_action_panel.open and self.relic_action_panel.hovered_position is not None:
            self.outline(self.relic_action_panel.hovered_position, GOLD, 5)
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
        arcade.draw_lrbt_rectangle_filled(0, left, self.height - 80, self.height, SHELL)
        self.text('title', 'TWILIGHT / IV', 22, self.height - 31, 18)
        self.text('subtitle', f'{VARIANT}  /  {len(self.board.home_tiles)} players', 22, self.height - 55, 10, MUTED)
        self.text('zoom', f'{self.focus_zoom if self.focus_view else self.zoom:.1f}×', left - 62, self.height - 75, 11, ACCENT)
        turn_left, turn_right = left - 250, left - 115
        control_left, control_right = turn_right + 8, turn_right + 46
        control_bottom = self.height - 58
        button(self, 'control_toggle', '', control_left, control_bottom,
               control_right - control_left, 34, selected=self.show_planet_control)
        icon_color = ACCENT if self.show_planet_control else MUTED
        icon_x, icon_y, icon_radius = (control_left + control_right) / 2, control_bottom + 17, 8
        icon_points = [(icon_x + icon_radius * math.cos(math.pi / 3 * index),
                        icon_y + icon_radius * math.sin(math.pi / 3 * index)) for index in range(6)]
        arcade.draw_polygon_outline(icon_points, icon_color, 2)
        if self.show_planet_control:
            arcade.draw_circle_filled(icon_x, icon_y, 2.5, icon_color)
        self.control_toggle_hit = (control_left, control_right, self.height - 56, self.height - 24)
        active_player = self.turn_order.active_player
        active_faction = active_player.faction.upper() if active_player else 'NO PLAYER'
        allocation_status = (' · RESOLVING ' + self.strategy.player.faction.upper() if self.strategy.session else
                             ' · COMMAND ALLOCATION' if self.turn_order.command_allocation else '')
        self.text('turn_status', f'ROUND {self.turn_order.round_number} · {active_faction}{allocation_status}',
                  left - 370, self.height - 19, 9, ACCENT if self.turn_order.action_used else MUTED, max_width=364)
        turn_bottom, turn_top = self.height - 58, self.height - 27
        pending_commands = active_player.pending_commands if active_player else 0
        allocation_available = (pending_commands > 0 and
                                not self.turn_order.command_allocation and
                                not self.turn_order.strategy_selection and
                                not self.strategy.session)
        enabled = (not self.immediate_command_allocation and
                   (allocation_available or not self.movement.session) and
                   not self.turn_order.command_allocation and
                   not self.turn_order.strategy_selection and not self.strategy.session)
        button_label = ('STRATEGY PHASE' if self.turn_order.strategy_selection else
                        'STRATEGY ACTION' if self.strategy.session else
                        'ALLOCATING COMMANDS' if self.immediate_command_allocation else
                        'ALLOCATE COMMANDS' if pending_commands else
                        'END TURN' if self.turn_order.action_used else 'PASS')
        if not self.bot_actor:
            button(self, 'pass_turn_button', button_label, turn_left, turn_bottom,
                   turn_right - turn_left, turn_top - turn_bottom, primary=True,
                   enabled=enabled, size=10)
        self.turn_button_hit = (turn_left, turn_right, turn_bottom, turn_top) if enabled and not self.bot_actor else None
        if self.ai.bot_factions:
            ai_left = 260
            button(self, 'ai_pause', 'RESUME AI' if self.ai.paused else 'PAUSE AI',
                   ai_left, turn_bottom, 94, turn_top - turn_bottom,
                   selected=self.ai.paused, size=9)
            button(self, 'ai_speed', f'{self.ai.speed:g}×',
                   ai_left + 100, turn_bottom, 44, turn_top - turn_bottom, size=9)
            self.ai_pause_hit = (ai_left, ai_left + 94, turn_bottom, turn_top)
            self.ai_speed_hit = (ai_left + 100, ai_left + 144, turn_bottom, turn_top)
        else:
            self.ai_pause_hit = self.ai_speed_hit = None
        card_participants = self.human_card_participants()
        card_player = (card_participants[0] if self.bot_actor and card_participants else
                       None if self.bot_actor else
                       self.strategy.player if self.strategy.session else active_player)
        card_count = len(card_player.action_cards) if card_player else 0
        playable_count = sum(len(self.action_cards.playable(player, self.movement.session))
                             for player in card_participants)
        card_left, card_right = left - 370, left - 258
        if card_player:
            button(self, 'action_card_hand_button', f'CARDS · {card_count}',
                   card_left, turn_bottom, card_right - card_left, turn_top - turn_bottom,
                   primary=bool(playable_count), selected=self.action_card_panel.open, size=9)
            self.action_card_button_hit = (card_left, card_right, turn_bottom, turn_top)
            discovery_left = card_left - 120
            button(self, 'discoveries_button', 'DISCOVERIES', discovery_left, turn_bottom,
                   112, turn_top - turn_bottom,
                   selected=self.exploration_panel.open, size=9)
            self.exploration_button_hit = (discovery_left, discovery_left + 112,
                                           turn_bottom, turn_top)
        else:
            self.action_card_button_hit = None
            self.exploration_button_hit = None
        if self.bot_actor:
            self.draw_bot_sidebar(left)
        elif not self.inspector_visible:
            self.system_panel.hits.clear()
            self.system_panel.remove_token_hit = None
            self.system_panel.add_token_hit = None
            self.system_panel.strategy_tab_hit = None
            self.system_panel.technology_tab_hit = None
            self.movement_panel.hits.clear()
            self.movement_panel.buttons.clear()
            self.movement_panel.route_hits.clear()
            self.movement_panel.source_hits.clear()
        elif self.movement.session:
            self.movement_panel.draw(self, self.movement.session, left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 155, 9, DANGER, self.sidebar - 36)
        else:
            self.system_panel.draw(self, self.board[self.selected], left)
            if self.movement_error:
                self.text('movement_error', self.movement_error, left + 18, 34, 10,
                          DANGER, self.sidebar - 36)
        arcade.draw_lrbt_rectangle_filled(toggle_left, toggle_right, tab_bottom, tab_bottom + 36,
                                           (25, 45, 63))
        arcade.draw_lrbt_rectangle_outline(toggle_left, toggle_right, tab_bottom, tab_bottom + 36,
                                            (77, 151, 151), 1)
        self.text('inspector_toggle_glyph', toggle_glyph,
                  (toggle_left + toggle_right) / 2 - 5, tab_bottom + 7, 19, ACCENT)
        self.inspector_toggle_hit = (toggle_left, toggle_right, tab_bottom, tab_bottom + 36)
        self.player_panel.production_planets = (
            set(self.strategy.session.payment_planets | self.strategy.session.technology_planets)
            if self.strategy.session and self.strategy.session.stage == 'technology' else
            set(self.strategy.session.payment_planets) if self.strategy.session and self.strategy.session.stage == 'leadership'
            else set(self.movement.session.production_planets)
            if self.movement.session and self.movement.session.stage == 'production' else set())
        self.player_panel.draw(self, show_details=False, interactive=not self.bot_actor)
        self.roster.draw(self)
        if not self.bot_actor and self.turn_order.active_player and self.turn_order.active_player.leaders:
            leader_left = self.roster.WIDTH - 57
            leader_bottom = self.height - 113
            button(self, 'leaders_button', 'LDR', leader_left, leader_bottom,
                   47, 24, selected=self.leader_panel.open, size=8)
            self.leader_button_hit = (leader_left, leader_left + 47,
                                      leader_bottom, leader_bottom + 24)
        else:
            self.leader_button_hit = None
        if self.ai.bot_factions:
            feed_left = self.roster.WIDTH + 12
            feed_right = min(feed_left + 465, left - 12)
            focus_positions = (self.ai.focus_positions if self.movement.session and
                               self.ai.is_bot(self.movement.session.player) and
                               self.ai.focus_positions else (self.selected,))
            focus_ys = [self.screen(position)[1] for position in focus_positions]
            top_box = (self.height - 187, self.height - 89)
            bottom_box = (self.player_panel.HEIGHT + 13, self.player_panel.HEIGHT + 111)
            overlap = lambda box: sum(box[0] - 30 <= y <= box[1] + 30 for y in focus_ys)
            feed_top = (bottom_box[1] if overlap(bottom_box) < overlap(top_box)
                        else top_box[1])
            arcade.draw_lrbt_rectangle_filled(feed_left, feed_right, feed_top - 98,
                                               feed_top, (10, 21, 34, 235))
            arcade.draw_lrbt_rectangle_outline(feed_left, feed_right, feed_top - 98,
                                                feed_top, (67, 133, 154), 1)
            self.text('ai_feed_title',
                      f'AI ACTIONS · {"PAUSED" if self.ai.paused else f"{self.ai.speed:g}×"}',
                      feed_left + 10, feed_top - 17, 10, ACCENT)
            for index, (faction, event) in enumerate(self.ai.events):
                self.text(('ai_feed', index), f'{faction} · {event}', feed_left + 10,
                          feed_top - 38 - index * 19, 9, INK if index == 0 else MUTED,
                          max_width=feed_right - feed_left - 20)
        if not self.strategy_modal:
            self.roster.draw_details(self)
        if self.player_panel.hovered_planet is not None:
            self.player_panel.draw_details(self)
        if self.movement.session and self.movement.session.stage in ('space_cannon_choose_target', 'space_cannon_assign', 'space_combat', 'ground_combat', 'combat_end', 'retreat_selection', 'retreat_token_pool', 'assault_choice', 'space_combat_won'):
            self.combat_panel.draw(self, self.movement.session)
        if self.strategy_modal and not self.technology_modal:
            self.strategy_panel.draw(self, self.turn_order)
        if self.technology_modal:
            self.technology_panel.draw(self,
                                       self.strategy.player if self.strategy.session and
                                       self.strategy.session.stage == 'technology' else self.player_panel.player,
                                       self.strategy if self.strategy.session and
                                       self.strategy.session.stage == 'technology' else None)
        if self.transaction_modal:
            self.transaction_panel.draw(self, self.transaction)
        elif self.action_card_panel.open:
            self.action_card_panel.draw(self)
        if self.star_forge_target is not None:
            self.draw_star_forge_modal()
        terragenesis_result_waiting = (
            self.exploration.terragenesis_pending is not None and
            self.exploration.pending is None and self.exploration.freelancer_offer is None and
            not self.exploration.secret_objectives.pending_returns and
            not self.exploration_panel.open and not self.exploration_panel.result_mode)
        if (not self.exploration_panel.is_visible(self.exploration) or
                terragenesis_result_waiting):
            while self.movement.exploration_outcomes:
                faction, outcome = self.movement.exploration_outcomes.pop(0)
                player = next((p for p in self.player_panel.players if p.faction == faction), None)
                if player is not None and not self.smoke and not self.ai.is_bot(player):
                    self.exploration_panel.show_result(outcome)
                    break
        if self.exploration_panel.is_visible(self.exploration):
            owner = self.exploration_decision_owner()
            if owner is not None and (self.smoke or not self.ai.is_bot(owner)):
                self.exploration_panel.draw(self, owner, self.exploration)
        if self.leader_panel.open and self.turn_order.active_player:
            player = next((candidate for candidate in self.player_panel.players
                           if candidate.faction == self.leader_panel.reaction_owner_faction),
                          self.turn_order.active_player)
            available = set()
            if (player.faction == 'cabal' and
                    self.strategy.pending_cabal_replenishments and
                    self.strategy.pending_cabal_replenishments[0][0] == player.faction and
                    leader_available(player, 'cabalagent')):
                available.add('cabalagent')
            if (player.faction == 'naaz' and self.turn_order.action_used and
                    self.movement.session is None and not self.strategy.session):
                available.add('naazagent')
            session = self.movement.session
            if (session is not None and session.stage == 'movement' and
                    player.faction == session.activation_agent_owner and
                    self.leader_panel.selected_id == session.activation_agent_alias):
                if session.activation_agent_alias == 'saaragent':
                    available.add('saaragent')
                elif session.activation_agent_alias == 'ghostagent':
                    available.add('ghostagent')
            if (player.faction == 'l1z1x' and session is not None and
                    session.stage == 'movement' and
                    player.faction == session.activation_agent_owner and
                    session.activation_agent_alias == 'l1z1xagent' and
                    leader_available(player, 'l1z1xagent') and
                    l1z1x_agent_mech_available(session.player, session.target, self.board) and
                    any(unit.owner == session.player.faction and unit.kind == 'infantry' and
                        unit.location.region in (Region.PLANET, Region.TRANSPORT)
                        for unit in session.target.units)):
                available.add('l1z1xagent')
            if (not self.movement.session and not self.strategy.session and
                    self.turn_order.can_take_action):
                if player.faction == 'hacan' and leader_available(player, 'hacanagent'):
                    available.add('hacanagent')
                if player.faction == 'muaat' and leader_available(player, 'muaatagent'):
                    available.add('muaatagent')
                if player.faction == 'arborec' and leader_available(player, 'arborecagent'):
                    available.add('arborecagent')
                if (player.faction == 'xxcha' and leader_available(player, 'xxchaagent') and
                        any(card.exhausted for card in player.planets)):
                    available.add('xxchaagent')
            self.leader_panel.draw(self, player, self.board, self.movement,
                                   available_actions=available)
        if self.relic_action_panel.open and self.turn_order.active_player:
            self.relic_action_panel.draw(self, self.turn_order.active_player,
                                         self.exploration, self.action_cards)
        if self.naaz_hero_pending and self.strategy.session is None:
            self.draw_naaz_hero_secondary_modal()
        if self.legendary_modal:
            self.draw_legendary_modal()

    def draw_naaz_hero_secondary_modal(self):
        from ui_theme import modal
        width = min(620, self.width - 40)
        height = min(420, self.height - 40)
        left = (self.width - width) / 2
        bottom = (self.height - height) / 2
        modal(left, left + width, bottom, bottom + height)
        self.text('naaz_hero_title', 'NAAZ-ROKHA HERO',
                  left + 20, bottom + height - 38, 17, ACCENT)
        player = self.turn_order.active_player
        chosen = self.strategy.hero_secondary_choices.get(
            (player.faction, self.turn_order.turn_serial), set())
        self.text('naaz_hero_help',
                  f'Choose up to two different strategy-card secondary abilities · {len(chosen)}/2',
                  left + 20, bottom + height - 66, 10, MUTED, width - 40)
        if self.movement_error:
            self.text('naaz_hero_error', self.movement_error,
                      left + 20, bottom + 19, 9, DANGER, width - 40)
        self.naaz_hero_hits.clear()
        cell_width = (width - 52) / 2
        for card in range(1, 8):
            row, col = divmod(card - 1, 2)
            x = left + 20 + col * (cell_width + 12)
            y = bottom + height - 118 - row * 67
            available = card not in chosen
            button(self, ('naaz_hero_card', card),
                   f'{card} · {STRATEGY_CARDS[card][0].upper()}', x, y,
                   cell_width, 48, primary=available, enabled=available, size=10)
            if available:
                self.naaz_hero_hits.append((card, x, x + cell_width, y, y + 48))
        x, y = left + cell_width + 32, bottom + height - 118 - 3 * 67
        button(self, 'naaz_hero_done', 'DONE', x, y, cell_width, 48, size=10)
        self.naaz_hero_hits.append(('done', x, x + cell_width, y, y + 48))

    def draw_legendary_modal(self):
        from ui_theme import modal
        player = self.turn_order.active_player
        options = self.movement.legendary_planet_options(player)
        width = min(650, self.width - 40)
        height = min(510, self.height - 40)
        left, bottom = (self.width - width) / 2, (self.height - height) / 2
        modal(left, left + width, bottom, bottom + height)
        self.text('legendary_title', 'LEGENDARY PLANET ABILITIES',
                  left + 22, bottom + height - 39, 16, ACCENT)
        self.text('legendary_help', 'New abilities are ready immediately; use them at the end of your turn.',
                  left + 22, bottom + height - 66, 10, MUTED, width - 44)
        self.legendary_hits.clear()

        def add(key, label, x, y, w, h, *, primary=False):
            button(self, ('legendary', key), label, x, y, w, h,
                   primary=primary, size=9)
            self.legendary_hits.append((key, x, x + w, y, y + h))

        if self.legendary_choice is None:
            for index, source in enumerate(options):
                label = ('PRIMOR · PLACE INFANTRY' if source == 'primor' else
                         "HOPE'S END · MECH OR ACTION CARD" if source == 'hopesend' else
                         'MALLICE · EXTERRIX HEADQUARTERS')
                add(('select', source), label, left + 22,
                    bottom + height - 127 - index * 58, width - 44, 43,
                    primary=True)
            if not options:
                self.text('legendary_empty', 'No ready legendary ability remains.',
                          left + 22, bottom + height - 132, 11, MUTED)
        else:
            source, choice, count = self.legendary_choice
            if source not in options:
                self.legendary_choice = None
                return
            add(('back',), 'BACK', left + 22, bottom + height - 126, 84, 32)
            if source == 'primor':
                for quantity in range(1, options[source]['maximum'] + 1):
                    add(('count', quantity), f'{quantity} INFANTRY',
                        left + 119 + (quantity - 1) * 139,
                        bottom + height - 126, 130, 32,
                        primary=count == quantity)
                destinations = options[source]['infantry']
            elif source == 'hopesend':
                if options[source].get('card'):
                    add(('use', source, 'card', None, 1), 'DRAW ACTION CARD',
                        left + 119, bottom + height - 126, 180, 32, primary=True)
                destinations = options[source].get('mech', ())
            else:
                add(('use', source, 'trade_goods', None, 2), 'GAIN 2 TRADE GOODS',
                    left + 119, bottom + height - 126, 180, 32, primary=True)
                if options[source]['convert_commodities']:
                    add(('use', source, 'convert_commodities', None, 0),
                        'CONVERT ALL COMMODITIES', left + 306,
                        bottom + height - 126, width - 328, 32)
                destinations = ()
            if destinations:
                self.text('legendary_destination', 'CHOOSE A CONTROLLED PLANET',
                          left + 22, bottom + height - 173, 10, GOLD)
                pages = max(1, (len(destinations) + 7) // 8)
                self.legendary_page = min(self.legendary_page, pages - 1)
                for index, planet_id in enumerate(destinations[self.legendary_page * 8:
                                                                self.legendary_page * 8 + 8]):
                    row, col = divmod(index, 2)
                    bx = left + 22 + col * ((width - 34) / 2)
                    by = bottom + height - 222 - row * 46
                    planet = next(planet for tile in self.board.values()
                                  for planet in tile.planets if planet.planet_id == planet_id)
                    add(('use', source, 'infantry' if source == 'primor' else 'mech',
                         planet_id, count if source == 'primor' else 1),
                        planet.name, bx, by, (width - 56) / 2, 35, primary=True)
                if pages > 1:
                    add(('page', -1), 'PREV', left + 22, bottom + 71, 75, 28)
                    self.text('legendary_page', f'{self.legendary_page + 1}/{pages}',
                              left + 109, bottom + 81, 9, MUTED)
                    add(('page', 1), 'NEXT', left + 145, bottom + 71, 75, 28)
        add(('finish',), 'FINISH TURN', left + width - 181, bottom + 23,
            159, 37, primary=True)
        if self.movement_error:
            self.text('legendary_error', self.movement_error,
                      left + 22, bottom + 29, 9, DANGER, width - 220)

    def draw_star_forge_modal(self):
        left, right = self.width / 2 - 220, self.width / 2 + 220
        bottom, top = self.height / 2 - 92, self.height / 2 + 92
        from ui_theme import modal
        modal(left, right, bottom, top)
        self.text('star_forge_title', 'STAR FORGE', left + 22, top - 36, 17, ACCENT)
        target = self.board[self.star_forge_target]
        self.text('star_forge_target', f'{target.name} · Choose units', left + 22,
                  top - 62, 10, MUTED)
        self.star_forge_hits.clear()
        for index, (kind, label) in enumerate((('fighter', '2 FIGHTERS'),
                                                ('destroyer', '1 DESTROYER'))):
            x, y, width, height = left + 22 + index * 202, bottom + 30, 184, 42
            button(self, ('star_forge_choice', kind), label, x, y, width, height,
                   primary=True, size=11)
            self.star_forge_hits.append((kind, x, x + width, y, y + height))
        x, y, width, height = right - 92, top - 40, 72, 24
        button(self, ('star_forge_choice', 'cancel'), 'CANCEL', x, y, width, height,
               size=8)
        self.star_forge_hits.append(('cancel', x, x + width, y, y + height))

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
                # Cycle away and back so the game fixture still includes Sol.
                hit = next(hit for hit in self.main_menu.hits if hit[0] == ('faction', 0, -1))
                self.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                assert self.main_menu.active_factions[0] == 'sol'
                self.on_draw()
                hit = next(hit for hit in self.main_menu.hits if hit[0] == ('start',))
                self.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                                    arcade.MOUSE_BUTTON_LEFT, 0)
                assert not self.main_menu_visible and len(self.board) == 38
                assert tuple(player.faction for player in self.player_panel.players) == self.main_menu.active_factions
                mecatol = next(tile for tile in self.board.values() if tile.number == 18)
                self.selected = mecatol.position
                self.on_draw()
                assert 'CUSTODIANS: 6 INFLUENCE' in self.labels['system_id'].text
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
        self.sync_cabal_agent_reaction()
        self.zoom += (self.target_zoom - self.zoom) * min(1, delta_time * 14)
        for axis in range(2):
            shift = (self.target_map_center[axis] - self.map_center[axis]) * min(1, delta_time * 7)
            self.map_center[axis] += shift
        self.frames += 1
        if self.ai.bot_factions and not self.smoke:
            self.ai.update(delta_time)
        self.restore_agent_production_view()
        if self.technology_smoke_test and self.frames == 3:
            preview_dir = ROOT / 'previews'
            preview_dir.mkdir(exist_ok=True)
            player = self.turn_order.active_player
            player.technologies = player.technologies | {'gd', 'lwd'}
            self.turn_order.strategy_selection = False
            self.turn_order.strategy_assignments[player.faction] = [7]
            self.strategy.start(7)
            self.on_draw()
            assert self.technology_modal and self.technology_panel.hits
            arcade.get_image().save(preview_dir / 'technology-research-preview.png')
            carrier = next(hit for hit in self.technology_panel.hits
                           if hit[0] == ('unit', 'carrier'))
            self.on_mouse_press((carrier[1] + carrier[2]) / 2,
                                (carrier[3] + carrier[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            arcade.get_image().save(preview_dir / 'technology-unit-upgrade-preview.png')
            assert self.strategy.session.technology_selected == 'ac2'
            research = next(hit for hit in self.technology_panel.hits
                            if hit[0] == ('research',))
            self.on_mouse_press((research[1] + research[2]) / 2,
                                (research[3] + research[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert 'ac2' in player.technologies
            assert all(unit.profile_id == 'sol_carrier2'
                       for tile in self.board.values() for unit in tile.units
                       if unit.owner == player.faction and unit.kind == 'carrier')
            self.on_draw()
            arcade.get_image().save(preview_dir / 'technology-upgraded-preview.png')
            while self.strategy.session:
                if self.strategy.session.stage == 'offer':
                    self.strategy.decline_secondary()
                else:
                    self.strategy.continue_stage()
            self.turn_order.actions_used = 0
            self.turn_order.action_used = False
            hacan = next(other for other in self.turn_order.players if other.faction == 'hacan')
            hacan.technologies |= {'qdn'}
            hacan.trade_goods = 3
            self.turn_order.strategy_assignments = {
                'sol': [1, 7], 'jolnar': [2, 3], 'hacan': [4, 5]}
            self.turn_order.qdn_pending = True
            self.strategy_view = True
            self.on_draw()
            arcade.get_image().save(preview_dir / 'technology-qdn-preview.png')
            swap = next(hit for hit in self.strategy_panel.hits
                        if hit[0][:2] == ('qdn_exchange', 'sol'))
            self.on_mouse_press((swap[1] + swap[2]) / 2,
                                (swap[3] + swap[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert not self.turn_order.qdn_pending and hacan.trade_goods == 0
            assert self.turn_order.active_player is hacan
            from movement import Session, Snapshot
            player.technologies = player.technologies - {'asc'}
            hacan.technologies |= {'asc'}
            battle_tile = next(tile for tile in self.board.values() if not tile.units)
            for index, kind in enumerate(('carrier', 'cruiser', 'destroyer')):
                battle_tile.units.append(Unit(f'smoke-assault-sol-{index}', kind,
                                              player.faction, player.color_code,
                                              UnitLocation(Region.SPACE)))
            for kind in ('carrier', 'cruiser', 'destroyer'):
                battle_tile.units.append(Unit(f'smoke-assault-hacan-{kind}', kind,
                                              hacan.faction, hacan.color_code,
                                              UnitLocation(Region.SPACE)))
            battle = Session(hacan, battle_tile, {}, Snapshot.capture(
                self.board, hacan, self.player_panel.players))
            self.movement.session = battle
            self.movement.start_combat(battle)
            assert battle.stage == 'assault_choice'
            self.ai.bot_factions = {'hacan'}
            self.ai.resolve_movement()
            assert battle.stage == 'assault_choice'
            assert any(unit.unit_id == 'smoke-assault-sol-1' for unit in battle_tile.units)
            self.on_draw()
            arcade.get_image().save(preview_dir / 'technology-assault-choice-preview.png')
            victim = next(hit for hit in self.combat_panel.action_hits
                          if hit[0] == ('assault_victim', 'smoke-assault-sol-1'))
            self.on_mouse_press((victim[1] + victim[2]) / 2,
                                (victim[3] + victim[4]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert battle.stage == 'space_combat'
            assert all(unit.unit_id != 'smoke-assault-sol-1' for unit in battle_tile.units)
            print('PASS: base-game research, unit upgrade, QDN and human Assault Cannon casualty selection checked')
            self.close()
            return
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
            smoke_mouse_scale = getattr(self, '_mouse_scale', None)
            smoke_had_mouse_scale = hasattr(self, '_mouse_scale')
            self._mouse_scale = 1.0
            self.on_mouse_scroll(self.roster.WIDTH + 100, 400, 0, 100)
            assert self.target_zoom == 3.5
            self.on_mouse_scroll(self.roster.WIDTH + 100, 400, 0, -100)
            assert self.target_zoom == .55
            self.fit()
            tile_position = next(iter(self.board))
            tile_x, tile_y = self.screen(tile_position)
            assert self.roster.WIDTH <= tile_x < self.width - self.sidebar
            assert self.player_panel.HEIGHT <= tile_y < self.height - 80
            self.on_mouse_scroll(tile_x, tile_y, 0, 1)
            assert self.target_zoom > 1.0
            self.fit()
            center_x, center_y = self.viewport_center
            pointer_x, pointer_y = center_x + 90, center_y + 45
            pointer_world_before = (
                self.target_map_center[0] + (pointer_x - center_x) / self.fit_scale,
                self.target_map_center[1] + (pointer_y - center_y) / self.fit_scale,
            )
            self.on_mouse_scroll(pointer_x, pointer_y, 0, 1)
            assert abs(self.target_zoom - 1.2) < 1e-9
            pointer_world_after = (
                self.target_map_center[0] + (pointer_x - center_x) /
                (self.fit_scale * self.target_zoom),
                self.target_map_center[1] + (pointer_y - center_y) /
                (self.fit_scale * self.target_zoom),
            )
            assert all(abs(before - after) < 1e-9 for before, after in
                       zip(pointer_world_before, pointer_world_after))
            assert self.zoom == self.target_zoom
            self.on_mouse_scroll(pointer_x, pointer_y, 0, .5)
            self.on_mouse_scroll(pointer_x, pointer_y, 0, .5)
            self.on_mouse_scroll(pointer_x, pointer_y, 0, -1)
            assert abs(self.target_zoom - 1.2) < 1e-9
            assert self.zoom == self.target_zoom
            self.on_mouse_scroll(pointer_x, pointer_y, 0, .25)
            assert self.target_zoom > 1.2
            if smoke_had_mouse_scale:
                self._mouse_scale = smoke_mouse_scale
            else:
                del self._mouse_scale
            # Regression: Win32 Pyglet reports wheel coordinates in the
            # unscaled space on high-DPI stretch displays. Check the right side
            # of the map, where those coordinates used to be mistaken for the
            # inspector and zoom stopped working.
            self.fit()
            map_right = self.width - self.sidebar
            right_map_x = self.roster.WIDTH + (map_right - self.roster.WIDTH) * .82
            right_map_y = self.player_panel.HEIGHT + (
                self.height - 80 - self.player_panel.HEIGHT) * .55
            assert right_map_x < map_right
            old_mouse_scale = getattr(self, '_mouse_scale', None)
            had_mouse_scale = hasattr(self, '_mouse_scale')
            self._mouse_scale = 2.0
            self.on_mouse_scroll(right_map_x * 2, right_map_y * 2, 0, 1)
            assert abs(self.target_zoom - 1.2) < 1e-9
            if had_mouse_scale:
                self._mouse_scale = old_mouse_scale
            else:
                del self._mouse_scale
            self.focus_view = True
            self.focus_zoom = 1.0
            self.on_mouse_scroll(pointer_x, pointer_y, 0, 1)
            assert abs(self.focus_zoom - 1.2) < 1e-9
            self.focus_view = False
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
            assert self.show_planet_control
            bounds = self.control_toggle_hit
            self.on_mouse_press((bounds[0] + bounds[1]) / 2,
                                (bounds[2] + bounds[3]) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            assert not self.show_planet_control
            self.on_draw()
            assert not self.show_planet_control
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
                transit_target = next(tile for tile in self.board.values() if tile.name == 'Lodor')
                transit_carrier = next(unit for unit in home.units
                                       if unit.owner == 'sol' and unit.kind == 'carrier')
                old_technologies = sol_player.technologies
                old_carrier_profile = transit_carrier.profile_id
                sol_player.technologies = frozenset(set(old_technologies) | {'cv2'})
                transit_carrier.profile_id = 'carrier2'
                transit_path = self.movement.route(home, transit_target, transit_carrier, sol_player)
                assert transit_path and len(transit_path) >= 3
                transit_tile = self.board[transit_path[1]]
                transit_infantry = Unit('smoke-transit-pickup', 'infantry', 'sol',
                                        sol_player.color_code,
                                        UnitLocation(Region.PLANET, planet_id=transit_tile.planets[0].planet_id))
                transit_tile.units.append(transit_infantry)
                try:
                    self.movement.activate(sol_player, transit_target.position)
                    self.selected = transit_target.position
                    self.movement_panel.reset()
                    self.on_draw()
                    carrier_row = next(hit for hit in self.movement_panel.hits
                                       if hit[0] == ('unit', transit_carrier.unit_id))
                    for _ in range(12):
                        if self.movement_panel.hit_test((carrier_row[1] + carrier_row[2]) / 2,
                                                        (carrier_row[3] + carrier_row[4]) / 2) == carrier_row[0]:
                            break
                        self.movement_panel.scroll_by(80)
                        self.on_draw()
                        carrier_row = next(hit for hit in self.movement_panel.hits
                                           if hit[0] == ('unit', transit_carrier.unit_id))
                    self.on_mouse_press((carrier_row[1] + carrier_row[2]) / 2,
                                        (carrier_row[3] + carrier_row[4]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                    self.on_draw()
                    pickup_row = next((hit for hit in self.movement_panel.hits
                                       if hit[0] == ('unit', transit_infantry.unit_id)), None)
                    for _ in range(12):
                        if pickup_row and self.movement_panel.hit_test(
                                (pickup_row[1] + pickup_row[2]) / 2,
                                (pickup_row[3] + pickup_row[4]) / 2) == pickup_row[0]:
                            break
                        self.movement_panel.scroll_by(80)
                        self.on_draw()
                        pickup_row = next((hit for hit in self.movement_panel.hits
                                           if hit[0] == ('unit', transit_infantry.unit_id)), None)
                    assert pickup_row is not None, 'Route cargo row should be reachable by scrolling.'
                    self.on_mouse_press((pickup_row[1] + pickup_row[2]) / 2,
                                        (pickup_row[3] + pickup_row[4]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                    confirm_row = next(button for button in self.movement_panel.buttons
                                       if button[0] == ('confirm',))
                    self.on_mouse_press((confirm_row[1] + confirm_row[2]) / 2,
                                        (confirm_row[3] + confirm_row[4]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                    assert self.movement.session and transit_infantry in transit_target.units
                    assert transit_infantry.location.region == Region.TRANSPORT
                    self.movement.cancel()
                finally:
                    if self.movement.session:
                        self.movement.cancel()
                    if transit_infantry in transit_tile.units:
                        transit_tile.units.remove(transit_infantry)
                    transit_carrier.profile_id = old_carrier_profile
                    sol_player.technologies = old_technologies
                self.movement_panel.reset()
                target = next(tile for tile in self.board.neighbors(home.position)
                              if tile.planets and any(self.movement.route(home, tile, unit, sol_player)
                                     for unit in home.units if unit.owner == 'sol'))
                before_units = list(home.units)
                before_tactical = sol_player.command_pools['tactical']
                self.movement.activate(sol_player, target.position)
                self.selected = target.position
                self.on_draw()
                assert all(status == 'Wait' for _, status in self.movement_panel.timeline_rows[3:])
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
                step_statuses = [status for _, status in self.movement_panel.timeline_rows]
                assert step_statuses.count('Current') == 1
                assert self.movement_panel.timeline_rows[11][0] == 'STEP 5 · PRODUCTION'
                assert all(self.movement_panel.timeline_rows[index][1] == 'Skipped' for index in (4, 6))
                assert self.movement_panel.timeline_rows[5][1] == 'In progress'
                assert self.movement_panel.timeline_rows[7][1] == 'Current'
                assert all(self.movement_panel.timeline_rows[index][1] == 'Wait' for index in (9, 10, 11, 12))
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
                step_statuses = [status for _, status in self.movement_panel.timeline_rows]
                assert step_statuses.count('Current') == 1
                assert self.movement_panel.timeline_rows[11][0] == 'STEP 5 · PRODUCTION'
                assert self.movement_panel.timeline_rows[11][1] == 'In progress'
                hacan_blockader = next(player for player in self.player_panel.players
                                       if player.faction == 'hacan')
                blockade_ship = Unit('smoke-production-blockade', 'fighter', 'hacan',
                                      hacan_blockader.color_code, UnitLocation(Region.SPACE))
                target.units.append(blockade_ship)
                self.on_draw()
                assert 'ship production blocked' in self.labels['production_blockade'].text
                assert not any(hit[0] == ('production_unit', 'carrier', 1)
                               for hit in self.movement_panel.hits)
                try:
                    self.movement.adjust_production('carrier', 1)
                except MovementError:
                    pass
                else:
                    raise AssertionError('Ship production should be blocked by an enemy fighter.')
                target.units.remove(blockade_ship)
                self.on_draw()
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

                    # The Space Cannon assignment modal lets the player save a loaded carrier.
                    from movement import Session, Snapshot
                    cannon_target = next(tile for tile in self.board.values()
                                         if tile.planets and tile is not combat_target)
                    cannon_carrier = Unit('smoke-cannon-carrier', 'carrier', 'hacan',
                                           hacan_player.color_code, UnitLocation(Region.SPACE))
                    cannon_fighter = Unit('smoke-cannon-fighter', 'fighter', 'hacan',
                                          hacan_player.color_code, UnitLocation(Region.SPACE))
                    cannon_infantry = Unit(
                        'smoke-cannon-infantry', 'infantry', 'hacan', hacan_player.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=cannon_carrier.unit_id))
                    cannon_target.units.extend((cannon_carrier, cannon_fighter, cannon_infantry))
                    cannon_session = Session(
                        hacan_player, cannon_target, {},
                        Snapshot.capture(self.board, hacan_player, self.player_panel.players))
                    cannon_session.stage = 'space_cannon_assign'
                    cannon_session.space_cannon_next_stage = 'invasion'
                    cannon_session.space_cannon_events.append({
                        'owner': 'sol',
                        'candidates': [cannon_carrier.unit_id, cannon_fighter.unit_id],
                        'graviton': False,
                    })
                    self.movement.session = cannon_session
                    self.player_panel.active = self.player_panel.players.index(hacan_player)
                    self.on_draw()
                    assert self.labels['combat_modal_title'].text == 'SPACE CANNON DEFENSE'
                    assert 'Infantry' in self.labels[
                        ('space_cannon_ship_detail', cannon_carrier.unit_id)].text
                    arcade.get_image().save(preview_dir / 'space-cannon-assignment-preview.png')
                    fighter_hit = next(hit for hit in self.combat_panel.action_hits
                                       if hit[0] == ('space_cannon_target', cannon_fighter.unit_id))
                    self.on_mouse_press((fighter_hit[1] + fighter_hit[2]) / 2,
                                        (fighter_hit[3] + fighter_hit[4]) / 2,
                                        arcade.MOUSE_BUTTON_LEFT, 0)
                    assert cannon_fighter not in cannon_target.units
                    assert cannon_carrier in cannon_target.units and cannon_infantry in cannon_target.units
                    self.movement.cancel()
                    cannon_target.units.remove(cannon_carrier)
                    cannon_target.units.remove(cannon_infantry)
                    self.player_panel.active = self.player_panel.players.index(sol_player)

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
            assert all(len(player.action_cards) == (2 if 'nm' in player.technologies else 1)
                       for player in expected_players)
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
            previous_active = self.turn_order.active_index
            self.turn_order.active_index = next(index for index, player in
                                                enumerate(expected_players) if player is not sol_player)
            self.player_panel.active = expected_players.index(sol_player)
            self.on_draw()
            assert self.labels[('player_button', ('orbital_drop',))].text == 'ORBITAL DROP'
            assert not any(control.action == ('orbital_drop',) for control in self.player_panel.controls)
            self.turn_order.active_index = previous_active
            self.on_draw()
            assert self.labels[('player_button', ('orbital_drop',))].text == 'ORBITAL DROP'
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
            self.turn_order.actions_used = 1
            self.turn_order.action_used = True
            self.on_draw()
            assert self.labels[('player_button', ('orbital_drop',))].text == 'ORBITAL DROP'
            assert not any(control.action == ('orbital_drop',) for control in self.player_panel.controls)
            self.turn_order.actions_used = 0
            self.turn_order.action_used = False
            # Smoke-test the new factions' interactive abilities as well.
            self.configure_game(ROOT / 'maps/four_player.json',
                                ('muaat', 'arborec', 'sol', 'jolnar'))
            muaat = self.turn_order.active_player
            self.on_draw()
            forge = next(control for control in self.player_panel.controls
                         if control.action == ('star_forge',))
            home = next(tile for tile in self.board.values()
                        if any(unit.owner == 'muaat' and unit.kind == 'warsun'
                               and unit.location.region == Region.SPACE for unit in tile.units))
            fighters_before = sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                                  for unit in home.units)
            tokens_before = muaat.command_pools['strategic']
            self.on_mouse_press(forge.left + forge.width / 2, forge.bottom + forge.height / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            sx, sy = self.screen(home.position)
            self.on_mouse_press(sx, sy, arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            choice = next(hit for hit in self.star_forge_hits if hit[0] == 'fighter')
            self.on_mouse_press((choice[1] + choice[2]) / 2, (choice[3] + choice[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            assert muaat.command_pools['strategic'] == tokens_before - 1
            assert sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                       for unit in home.units) == fighters_before + 2

            arborec = next(player for player in self.turn_order.players if player.faction == 'arborec')
            self.mitosis_player = arborec
            self.player_panel.active = self.player_panel.players.index(arborec)
            planet_id = arborec.planets[0].planet.planet_id
            infantry_before = sum(unit.owner == 'arborec' and unit.kind == 'infantry' and
                                  unit.location.planet_id == planet_id
                                  for tile in self.board.values() for unit in tile.units)
            self.on_draw()
            planet_control = next(control for control in self.player_panel.controls
                                  if control.action == ('planet', 0))
            self.on_mouse_press(planet_control.left + planet_control.width / 2,
                                planet_control.bottom + planet_control.height / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            assert self.mitosis_player is None
            assert sum(unit.owner == 'arborec' and unit.kind == 'infantry' and
                       unit.location.planet_id == planet_id
                       for tile in self.board.values() for unit in tile.units) == infantry_before + 1
            self.configure_game(ROOT / 'maps/four_player.json',
                                ('hacan', 'sol', 'jolnar', 'letnev'))
            hacan = self.turn_order.active_player
            hacan.commodities = hacan.commodity_limit - 1
            self.leader_panel.open = True
            self.leader_panel.select('hacanagent', hacan)
            self.on_draw()
            use_hit = next(hit for hit in self.leader_panel.hits
                           if hit[0] == ('use', 'hacanagent'))
            self.on_mouse_press((use_hit[1] + use_hit[2]) / 2,
                                (use_hit[3] + use_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            self.on_draw()
            gain_hit = next(hit for hit in self.leader_panel.hits
                            if hit[0] == ('use', 'hacanagent', ('hacan', 'gain')))
            self.on_mouse_press((gain_hit[1] + gain_hit[2]) / 2,
                                (gain_hit[3] + gain_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            assert hacan.commodities == hacan.commodity_limit
            assert not leader_available(hacan, 'hacanagent')
            self.configure_game(ROOT / 'maps/four_player.json',
                                ('sol', 'cabal', 'hacan', 'jolnar'))
            sol = next(player for player in self.turn_order.players
                       if player.faction == 'sol')
            cabal = next(player for player in self.turn_order.players
                         if player.faction == 'cabal')
            self.turn_order.strategy_assignments[sol.faction] = [5]
            self.turn_order.active_index = self.turn_order.players.index(sol)
            self.strategy.start(5)
            self.strategy.confirm_trade()
            assert self.strategy.pending_cabal_replenishments == [
                ('cabal', 'sol', sol.commodity_limit)]
            self.on_update(0)
            assert self.leader_panel.reaction_owner_faction == 'cabal'
            assert not self.strategy_modal
            self.on_draw()
            use_hit = next(hit for hit in self.leader_panel.hits
                           if hit[0] == ('use', 'cabalagent'))
            self.on_mouse_press((use_hit[1] + use_hit[2]) / 2,
                                (use_hit[3] + use_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            self.leader_panel.target_page = 1
            self.on_draw()
            capture_hit = next(hit for hit in self.leader_panel.hits
                               if hit[0] == ('use', 'cabalagent', 'dreadnought'))
            self.on_mouse_press((capture_hit[1] + capture_hit[2]) / 2,
                                (capture_hit[3] + capture_hit[4]) / 2,
                                arcade.MOUSE_BUTTON_LEFT, 0)
            assert not self.strategy.pending_cabal_replenishments
            assert cabal.captured_units.get('dreadnought') == 1
            assert sol.commodities == 0 and sol.trade_goods == 3 + sol.commodity_limit
            print(f'PASS: {len(self.board)} tile objects; movement, combat, round passing, command allocation, faction abilities, Hacan agent, and Cabal trade reaction checked')
            self.close()

    def on_mouse_motion(self, x, y, dx, dy):
        if self.main_menu_visible:
            return
        self.mouse_position = (x, y)
        self.roster.hover(x, y)
        self.player_panel.hover(x, y)
        if self.strategy_modal and not self.technology_modal:
            self.strategy_panel.update_hover(x, y)
            session = self.strategy.session
            map_stage = bool(session and session.stage in (
                'construction', 'diplomacy_system', 'warfare_system'))
            bounds = self.strategy_panel.bounds
            over_modal = bounds and bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3]
            over_strategy_card = self.strategy_panel.drag_card(x, y)
            position = self.pick(x, y) if map_stage and not over_modal and not over_strategy_card else None
            self.strategy_hover_system = (position if position in self.strategy.selectable_systems()
                                          else self.strategy_panel.hover_system)
        else:
            self.strategy_hover_system = None
        if self.movement.session and self.movement.session.stage == 'movement' and not self.bot_actor:
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
                        self.main_menu.human_slot = min(self.main_menu.human_slot, action[1] - 1)
                    elif action[0] == 'faction':
                        self.main_menu.select_faction(action[1], action[2])
                    elif action[0] == 'toggle_ai':
                        self.main_menu.vs_ai = not self.main_menu.vs_ai
                    elif action[0] == 'human_slot' and self.main_menu.vs_ai:
                        self.main_menu.human_slot = action[1]
                    elif action[0] == 'start':
                        self.start_from_menu()
            return
        if self.strategy.pending_cabal_replenishments:
            self.sync_cabal_agent_reaction()
            if not self.leader_panel.reaction_owner_faction:
                return
        if self.exploration_panel.is_visible(self.exploration):
            owner = self.exploration_decision_owner()
            if owner is None or self.ai.is_bot(owner):
                return
            if button != arcade.MOUSE_BUTTON_LEFT:
                return
            action = self.exploration_panel.hit_test(x, y)
            if action is None and self.exploration_panel.drag_header(x, y):
                self.dragging_modal = 'exploration'
                return
            try:
                if action and action[0] == 'resolve':
                    self.movement.resolve_exploration_choice(action[1])
                    self.advance_hero_frontier()
                    self.exploration_panel.close()
                elif action and action[0] == 'move_sleeper':
                    self.movement.resolve_terragenesis(action[1])
                    self.exploration_panel.close()
                elif action and action[0] == 'decline_terragenesis':
                    self.movement.resolve_terragenesis()
                    self.exploration_panel.close()
                elif action and action[0] == 'freelancer_kind':
                    self.exploration_panel.freelancer_kind = action[1]
                elif action and action[0] == 'freelancer_planet':
                    self.exploration_panel.freelancer_planet = action[1]
                elif action and action[0] == 'freelancer_payment':
                    _, mode, planet_id = action
                    panel = self.exploration_panel
                    chosen = (panel.freelancer_resource_planets if mode == 'resources'
                              else panel.freelancer_influence_planets)
                    other = (panel.freelancer_influence_planets if mode == 'resources'
                             else panel.freelancer_resource_planets)
                    if planet_id in chosen:
                        chosen.remove(planet_id)
                    else:
                        chosen.add(planet_id)
                        other.discard(planet_id)
                elif action and action[0] == 'freelancer_goods':
                    panel = self.exploration_panel
                    panel.freelancer_trade_goods = max(0, min(
                        owner.trade_goods, panel.freelancer_trade_goods + action[1]))
                elif action and action[0] == 'freelancer_page':
                    panel = self.exploration_panel
                    pages = max(1, (sum(not card.exhausted for card in owner.planets) + 7) // 8)
                    panel.freelancer_payment_page = max(0, min(
                        pages - 1, panel.freelancer_payment_page + action[1]))
                elif action and action[0] == 'freelancer_produce':
                    panel = self.exploration_panel
                    position = self.exploration.freelancer_offer[1]
                    snapshot = Snapshot.capture(self.board, owner, self.player_panel.players,
                                                self.action_cards, self.exploration)
                    self.exploration.produce_freelancers(
                        owner, self.movement, panel.freelancer_kind,
                        planet_id=panel.freelancer_planet,
                        resource_planets=panel.freelancer_resource_planets,
                        influence_planets=panel.freelancer_influence_planets,
                        trade_goods=panel.freelancer_trade_goods)
                    panel.close()
                    self.movement.check_freelancers_limits(owner, position, snapshot)
                    self.advance_hero_frontier()
                elif action and action[0] == 'freelancer_decline':
                    self.exploration.produce_freelancers(owner, self.movement, None)
                    self.exploration_panel.close()
                    self.movement.resolve_exploration_queue()
                    self.advance_hero_frontier()
                elif action and action[0] == 'return_secret':
                    self.exploration.secret_objectives.return_unscored(
                        owner.faction, action[1])
                    if owner.faction not in self.exploration.secret_objectives.pending_returns:
                        self.exploration_panel.close()
                        self.movement.resolve_exploration_queue()
                        self.advance_hero_frontier()
                elif action and action[0] == 'assemble_relic':
                    snapshot = self._component_action_snapshot()
                    player = self.turn_order.active_player
                    self.exploration.purge_fragments_for_relic(
                        player.faction, action[1], required=action[2])
                    self._finish_component_action(snapshot)
                elif action and action[0] == 'fabrication_trait':
                    self.exploration_panel.fabrication_trait = action[1]
                elif action and action[0] == 'fabricate_token':
                    snapshot = self._component_action_snapshot()
                    naaz_fabrication(self.turn_order.active_player, self.exploration,
                                     (action[1],), board=self.board,
                                     command_pool=action[2])
                    self._finish_component_action(snapshot)
                    self.exploration_panel.fabrication_trait = None
                    self.exploration_panel.close()
                elif action and action[0] == 'relic_action':
                    self.use_relic_action(action[1])
                elif (action and action[0] == 'close' and
                      self.exploration.pending is None and
                      self.exploration.freelancer_offer is None and
                      not self.exploration.secret_objectives.pending_returns):
                    self.exploration_panel.close()
                self.movement_error = None
            except ValueError as error:
                self.movement_error = str(error)
            return
        if self.immediate_command_allocation:
            if button == arcade.MOUSE_BUTTON_LEFT:
                panel_control = next((control for control in reversed(self.player_panel.controls)
                                      if control.contains(x, y)), None)
                if (panel_control and panel_control.action[0] in ('pool', 'pending') and
                        self.player_panel.player is self.turn_order.active_player):
                    self.player_panel.handle_click(x, y)
                    if self.turn_order.active_player.pending_commands == 0:
                        self.immediate_command_allocation = False
                        self.player_panel.source_pool = None
            return
        if self.relic_action_panel.open:
            if button == arcade.MOUSE_BUTTON_LEFT:
                action = self.relic_action_panel.hit_test(x, y)
                if action is None and self.relic_action_panel.drag_header(x, y):
                    self.dragging_modal = 'relic'
                else:
                    command = self.relic_action_panel.handle(
                        action, self.exploration, self.action_cards)
                    if command:
                        self.resolve_relic_command(command)
            return
        if self.leader_panel.open:
            if button == arcade.MOUSE_BUTTON_LEFT:
                action = self.leader_panel.hit_test(x, y)
                if action is None and self.leader_panel.drag_header(x, y):
                    self.dragging_modal = 'leader'
                else:
                    reaction = self.leader_panel.reaction_owner_faction
                    self.resolve_leader_action(self.leader_panel.handle_click(
                        x, y, next((candidate for candidate in self.player_panel.players
                                    if candidate.faction == reaction),
                                   self.turn_order.active_player)))
                    if action == ('close',) and reaction:
                        self.skip_activation_agent_window()
            return
        if self.legendary_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                action = next((key for key, left, right, bottom, top in self.legendary_hits
                               if left <= x <= right and bottom <= y <= top), None)
                if action:
                    try:
                        if action[0] == 'select':
                            self.legendary_choice = (action[1], None, 2)
                            self.legendary_page = 0
                        elif action[0] == 'count' and self.legendary_choice:
                            self.legendary_choice = (self.legendary_choice[0], None, action[1])
                        elif action[0] == 'back':
                            self.legendary_choice = None
                        elif action[0] == 'page':
                            self.legendary_page = max(0, self.legendary_page + action[1])
                        elif action[0] == 'use':
                            self.movement.use_legendary_planet(
                                self.turn_order.active_player, action[1],
                                destination_planet_id=action[3], choice=action[2],
                                count=action[4])
                            self.legendary_choice = None
                            if not self.movement.legendary_planet_options(
                                    self.turn_order.active_player):
                                self.legendary_modal = False
                                self.pass_turn()
                        elif action[0] == 'finish':
                            self.legendary_modal = False
                            self.pass_turn()
                        self.movement_error = None
                    except (ValueError, MovementError) as error:
                        self.movement_error = str(error)
            return
        if self.naaz_hero_pending and self.strategy.session is None:
            if button == arcade.MOUSE_BUTTON_LEFT:
                choice = next((card for card, left, right, bottom, top in self.naaz_hero_hits
                               if left <= x <= right and bottom <= y <= top), None)
                if choice == 'done':
                    self.naaz_hero_pending = False
                elif isinstance(choice, int):
                    try:
                        self.strategy.start_hero_secondary(self.turn_order.active_player, choice)
                        selected = self.strategy.hero_secondary_choices.get(
                            (self.turn_order.active_player.faction, self.turn_order.turn_serial), set())
                        if len(selected) >= 2:
                            self.naaz_hero_pending = False
                        self.sync_strategy_actor()
                        self.movement_error = None
                    except ValueError as error:
                        self.movement_error = str(error)
            return
        if self.ai.bot_factions and button == arcade.MOUSE_BUTTON_LEFT:
            if self.ai_pause_hit and all((self.ai_pause_hit[0] <= x <= self.ai_pause_hit[1],
                                          self.ai_pause_hit[2] <= y <= self.ai_pause_hit[3])):
                self.ai.paused = not self.ai.paused
                return
            if self.ai_speed_hit and all((self.ai_speed_hit[0] <= x <= self.ai_speed_hit[1],
                                          self.ai_speed_hit[2] <= y <= self.ai_speed_hit[3])):
                self.ai.cycle_speed()
                return
        if self.ai.bot_factions and not self.smoke:
            turn = self.turn_order
            if ((turn.strategy_selection or turn.command_allocation) and self.ai.is_bot(turn.active_player)):
                return
            if self.strategy.session and self.ai.is_bot(self.strategy.player) and not self.movement.session:
                return
            if self.movement.session and self.ai.is_bot(self.movement.session.player):
                foresight_human = (self.movement.session.stage == 'foresight' and
                                   self.movement.session.foresight_faction not in self.ai.bot_factions)
                combat_stage = self.movement.session.stage in (
                    'space_cannon_choose_target', 'space_cannon_assign', 'space_combat', 'ground_combat', 'combat_end', 'retreat_selection', 'retreat_token_pool',
                    'assault_choice', 'space_combat_won')
                human_card = any(not self.ai.is_bot(player) for player in
                                 self.action_cards.participants(self.movement.session))
                card_button = (self.action_card_button_hit and
                               self.action_card_button_hit[0] <= x <= self.action_card_button_hit[1] and
                               self.action_card_button_hit[2] <= y <= self.action_card_button_hit[3])
                if not combat_stage and not foresight_human and not (
                        human_card and (self.action_card_panel.open or card_button)):
                    return
                if combat_stage and not self.action_card_panel.open:
                    response = self.combat_panel.hit_test(x, y)
                    retreating_player = (next((player for player in self.player_panel.players
                                               if player.faction == self.movement.session.retreat_announced), None)
                                         if self.movement.session.stage in ('retreat_selection', 'retreat_token_pool') else None)
                    cannon_event = (self.movement.session.space_cannon_events[0]
                                    if self.movement.session.space_cannon_events else {})
                    cannon_target = next((player for player in self.player_panel.players
                                          if player.faction == cannon_event.get(
                                              'target', self.movement.session.player.faction)), None)
                    assault_victim_owner = (next((unit.owner for unit in
                        self.movement.assault_victims(self.movement.session)
                        if unit.unit_id == response[1]), None)
                        if response and response[0] == 'assault_victim' and len(response) > 1
                        else None)
                    allowed = bool(response and (
                        (response[0] in ('action_cards', 'advance') and human_card) or
                        (response[0] == 'space_cannon_target' and cannon_target and
                         not self.ai.is_bot(cannon_target)) or
                        (response[0] == 'assault_victim' and assault_victim_owner is not None and
                         assault_victim_owner not in self.ai.bot_factions) or
                        (response[0] == 'retreat_to' and retreating_player and
                         not self.ai.is_bot(retreating_player)) or
                        (response[0] == 'retreat_token_pool' and retreating_player and
                         len(response) > 1 and response[1] == retreating_player.faction and
                         not self.ai.is_bot(retreating_player)) or
                        (response[0] in ('announce_retreat', 'decline_retreat') and
                         len(response) > 1 and response[1] not in self.ai.bot_factions) or
                        (response[0] in ('assign_hit', 'spend_munitions', 'reroll_die',
                                         'supercharge',
                                         'reroll_dice', 'fire_team_die', 'fire_team_resolve',
                                         'agent_bonus_die') and
                         len(response) > 1 and response[1] not in self.ai.bot_factions)))
                    if not allowed:
                        return
            elif not self.movement.session and not self.strategy.session and self.ai.is_bot(turn.active_player):
                return
        self.sync_turn_action()
        left = self.width - self.sidebar
        if self.mitosis_player:
            if button == arcade.MOUSE_BUTTON_LEFT and x < left and y < self.player_panel.HEIGHT:
                control = next((control for control in reversed(self.player_panel.controls)
                                if control.contains(x, y) and
                                control.action[0] in ('planet', 'cards')), None)
                if control and control.action[0] == 'planet':
                    planet_id = self.mitosis_player.planets[control.action[1]].planet.planet_id
                    try:
                        self.movement.mitosis(self.mitosis_player, planet_id)
                        self.mitosis_player = None
                        self.finish_status_phase()
                        self.player_panel.active = self.player_panel.players.index(
                            self.turn_order.active_player)
                        self.movement_error = None
                    except MovementError as error:
                        self.movement_error = str(error)
                elif control and control.action[0] == 'cards':
                    self.player_panel.handle_click(x, y)
            return
        if self.star_forge_target is not None:
            if button == arcade.MOUSE_BUTTON_LEFT:
                choice = next((kind for kind, left_hit, right_hit, bottom_hit, top_hit
                               in self.star_forge_hits
                               if left_hit <= x <= right_hit and bottom_hit <= y <= top_hit), None)
                if choice == 'cancel':
                    self.star_forge_target = None
                elif choice in ('fighter', 'destroyer'):
                    try:
                        self.movement.star_forge(self.turn_order.active_player,
                                                 self.star_forge_target, choice)
                        self.star_forge_target = None
                        self.movement_error = None
                    except MovementError as error:
                        self.movement_error = str(error)
            return
        if self.transaction_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                self.handle_transaction_action(self.transaction_panel.hit_test(x, y))
            return
        if self.technology_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                if self.technology_panel.drag_header(x, y):
                    self.dragging_modal = 'technology'
                    return
                action = self.technology_panel.hit_test(x, y)
                if action:
                    self.handle_technology_action(action)
                elif self.strategy.session and self.strategy.session.stage == 'technology':
                    try:
                        self.strategy_tray_click(x, y)
                        self.movement_error = None
                    except ValueError as error:
                        self.movement_error = str(error)
            return
        if self.strategy_modal:
            if button == arcade.MOUSE_BUTTON_LEFT:
                if self.strategy_panel.drag_card(x, y):
                    self.dragging_modal = 'strategy_card'
                    return
                if self.strategy_panel.drag_header(x, y):
                    self.dragging_modal = 'strategy'
                    return
                action = self.strategy_panel.hit_test(x, y)
                if action:
                    self.handle_strategy_action(action)
                else:
                    self.strategy_tray_click(x, y)
                    bounds = self.strategy_panel.bounds
                    if not bounds or not (bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3]):
                        self.strategy_map_click(x, y)
            return
        if self.action_card_panel.open:
            if button == arcade.MOUSE_BUTTON_LEFT:
                action = self.action_card_panel.hit_test(x, y)
                if action and action[0] == 'player':
                    self.action_card_panel.player_faction = action[1]
                    self.action_card_panel.stall_mode = False
                elif action and action[0] == 'stall_mode':
                    self.action_card_panel.stall_mode = not self.action_card_panel.stall_mode
                elif action and action[0] == 'stall_discard':
                    player = next((p for p in self.turn_order.players
                                   if p.faction == self.action_card_panel.player_faction), None)
                    try:
                        self.action_cards.resolve_stall_tactics(player, action[1])
                        self.action_card_panel.stall_mode = False
                        self.action_card_panel.open = False
                        self.movement_error = None
                    except ValueError as error:
                        self.movement_error = str(error)
                elif action and action[0] == 'play':
                    participants = self.action_cards.participants(self.movement.session)
                    if not participants:
                        participants = [self.turn_order.active_player]
                    player = next((p for p in participants
                                   if p and p.faction == self.action_card_panel.player_faction),
                                  participants[0])
                    if self.ai.is_bot(player):
                        return
                    try:
                        self.action_cards.play(player, action[1])
                        self.movement_error = None
                    except ValueError as error:
                        self.movement_error = str(error)
                    self.action_card_panel.open = bool(self.action_cards.pending)
                elif action and action[0] == 'pending_choice':
                    try:
                        self.action_cards.resolve_pending(action[1])
                        self.movement_error = None
                    except (ValueError, StopIteration) as error:
                        self.movement_error = str(error)
                    self.action_card_panel.open = bool(self.action_cards.pending)
                elif action and action[0] == 'cancel_pending':
                    self.action_cards.cancel_pending()
                    self.action_card_panel.open = False
                elif action and action[0] == 'close':
                    self.action_card_panel.stall_mode = False
                    self.action_card_panel.open = False
                elif not (self.action_card_panel.bounds and
                          self.action_card_panel.bounds[0] <= x <= self.action_card_panel.bounds[1] and
                          self.action_card_panel.bounds[2] <= y <= self.action_card_panel.bounds[3]):
                    self.action_card_panel.open = False
                self.sync_turn_action()
            return
        if self.movement.session and self.movement.session.stage in ('space_cannon_choose_target', 'space_cannon_assign', 'space_combat', 'ground_combat', 'combat_end', 'retreat_selection', 'retreat_token_pool', 'assault_choice', 'space_combat_won'):
            if button != arcade.MOUSE_BUTTON_LEFT:
                return
            if self.combat_panel.drag_header(x, y):
                self.dragging_modal = 'combat'
                return
            action = self.combat_panel.hit_test(x, y)
            if action:
                try:
                    if action[0] == 'agent_bonus_die':
                        if action[1] in self.ai.bot_factions:
                            return
                        self.movement.use_combat_agent_die(action[1], action[3])
                    elif action[0] == 'assign_hit':
                        if action[1] in self.ai.bot_factions:
                            return
                        self.movement.assign_combat_hit(action[1], action[2])
                    elif action[0] == 'spend_munitions':
                        self.movement.spend_munitions(action[1])
                    elif action[0] == 'supercharge':
                        self.movement.use_supercharge(action[1])
                    elif action[0] == 'reroll_die':
                        self.movement.toggle_combat_reroll(action[1], action[2])
                    elif action[0] == 'reroll_dice':
                        self.movement.reroll_selected_combat_dice(action[1])
                    elif action[0] == 'fire_team_die':
                        self.action_cards.toggle_fire_team_die(action[1], action[2])
                    elif action[0] == 'fire_team_resolve':
                        self.action_cards.resolve_fire_team(action[1])
                    elif action[0] == 'advance':
                        session = self.movement.session
                        if (session and not session.combat_needs_resolution and
                                session.stage in ('space_combat', 'ground_combat')):
                            for faction in session.combat_factions:
                                if faction not in self.ai.bot_factions:
                                    continue
                                options = self.movement.combat_agent_die_options(faction)
                                if options:
                                    unit, _alias = min(options, key=lambda option: (
                                        self.movement.combat_threshold(
                                            faction, unit_profile(option[0])),
                                        option[0].unit_id))
                                    self.movement.use_combat_agent_die(faction, unit.unit_id)
                        self.movement.advance_combat()
                    elif action[0] == 'announce_retreat':
                        self.movement.announce_retreat(action[1])
                    elif action[0] == 'decline_retreat':
                        self.movement.decline_retreat(action[1])
                    elif action[0] == 'retreat_to':
                        self.movement.resolve_retreat(action[1])
                        self.movement_panel.reset()
                    elif action[0] == 'retreat_token_pool':
                        self.movement.resolve_retreat(
                            session.retreat_destination_pending, action[2])
                        self.movement_panel.reset()
                    elif action[0] == 'assault_victim':
                        self.movement.choose_assault_victim(action[1])
                        self.combat_panel.assault_page = 0
                    elif action[0] == 'space_cannon_target':
                        self.movement.assign_space_cannon_hit(action[1])
                        self.combat_panel.space_cannon_page = 0
                    elif action[0] == 'space_cannon_choose':
                        self.movement.choose_space_cannon_target(action[1])
                        self.combat_panel.space_cannon_page = 0
                    elif action[0] == 'space_cannon_page':
                        self.combat_panel.space_cannon_page += action[1]
                    elif action[0] == 'assault_page':
                        self.combat_panel.assault_page += action[1]
                    elif action[0] == 'action_cards':
                        self.action_card_panel.open = True
                    self.movement_error = None
                except ValueError as error:
                    self.movement_error = str(error)
            return
        if button == arcade.MOUSE_BUTTON_LEFT and self.action_card_button_hit and \
                self.action_card_button_hit[0] <= x <= self.action_card_button_hit[1] and \
                self.action_card_button_hit[2] <= y <= self.action_card_button_hit[3]:
            self.action_card_panel.open = True
            return
        if button == arcade.MOUSE_BUTTON_LEFT and self.exploration_button_hit and all((
                self.exploration_button_hit[0] <= x <= self.exploration_button_hit[1],
                self.exploration_button_hit[2] <= y <= self.exploration_button_hit[3])):
            self.exploration_panel.open = True
            return
        if button == arcade.MOUSE_BUTTON_LEFT and self.leader_button_hit and all((
                self.leader_button_hit[0] <= x <= self.leader_button_hit[1],
                self.leader_button_hit[2] <= y <= self.leader_button_hit[3])):
            self.leader_panel.open = True
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
        left = self.width - self.sidebar
        if self.turn_button_hit and self.turn_button_hit[0] <= x <= self.turn_button_hit[1] and \
                self.turn_button_hit[2] <= y <= self.turn_button_hit[3]:
            if self.turn_order.active_player and self.turn_order.active_player.pending_commands:
                self.immediate_command_allocation = True
                self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
                self.player_panel.source_pool = 'pending'
            elif self.movement.session:
                self.movement_error = 'Finish the current activation before ending your turn.'
            elif self.movement.legendary_planet_options(self.turn_order.active_player):
                self.legendary_modal = True
                self.legendary_choice = None
                self.legendary_page = 0
            else:
                self.pass_turn()
            return
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
                        if action[0] == 'timeline':
                            self.movement_panel.timeline_expanded = not self.movement_panel.timeline_expanded
                            self.movement_panel.scroll = 0
                        elif action[0] == 'unit':
                            self.movement.session.toggle(action[1])
                            self.movement_error = None
                        elif action[0] == 'cargo':
                            self.movement.session.toggle_cargo(action[1])
                            self.movement_error = None
                        elif action[0] == 'spatial_conduit':
                            self.movement.use_spatial_conduit()
                        elif action[0] == 'scanlink':
                            self.movement.explore_with_scanlink(action[1])
                        elif action[0] == 'awaken_sleepers':
                            self.movement.awaken_titans_sleepers()
                            self.movement_error = None
                        elif action[0] == 'action_cards':
                            self.action_card_panel.open = True
                        elif action[0] == 'foresight':
                            self.movement.resolve_foresight(action[1])
                        elif action[0] == 'foresight_decline':
                            self.movement.resolve_foresight()
                        elif action[0] == 'space_cannon_continue':
                            session = self.movement.session
                            session.experimental_window_closed = True
                            self.movement.continue_after_movement(
                                session, session.space_cannon_next_stage or 'invasion')
                        elif action[0] == 'space_cannon_resolve':
                            session = self.movement.session
                            self.movement.continue_after_space_cannon(session)
                        elif action[0] == 'space_cannon_direct_hit_continue':
                            self.movement.continue_after_space_cannon(self.movement.session)
                        elif action[0] == 'invasion_continue':
                            self.movement.continue_invasion_start(self.movement.session)
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
                            self.advance_hero_frontier()
                            self.movement_panel.reset()
                        elif action[0] == 'capacity_overflow':
                            self.movement.toggle_capacity_overflow_unit(action[1])
                        elif action[0] == 'resolve_capacity_overflow':
                            self.movement.resolve_capacity_overflow()
                            self.advance_hero_frontier()
                            self.movement_panel.reset()
                        elif action[0] == 'production_unit':
                            self.movement.adjust_production(action[1], action[2])
                        elif action[0] == 'production_reclaim':
                            self.movement.toggle_production_reclaim(action[1], action[2])
                        elif action[0] == 'production_trade_goods':
                            self.movement.change_production_trade_goods(action[1])
                        elif action[0] == 'aida_discount':
                            self.movement.toggle_ai_development_discount()
                        elif action[0] == 'sar_planet':
                            self.movement.choose_self_assembly_planet(action[1])
                        elif action[0] == 'amalgamation':
                            self.movement.adjust_amalgamation(action[1], action[2])
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
                    except ValueError as error:
                        self.movement_error = str(error)
                self.restore_agent_production_view()
                self.sync_turn_action()
            return
        if x < left and y < self.player_panel.HEIGHT:
            if self.star_forge_mode:
                panel_control = next((control for control in reversed(self.player_panel.controls)
                                      if control.contains(x, y)), None)
                if panel_control and panel_control.action[0] == 'star_forge':
                    self.star_forge_mode = False
                return
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
            if (panel_control and panel_control.action[0] in ('pool', 'pending') and
                    not self.turn_order.command_allocation and not self.immediate_command_allocation and
                    not self.smoke):
                return
            if panel_control and panel_control.action[0] == 'orbital_drop':
                self.orbital_drop_mode = True
                return
            if panel_control and panel_control.action[0] == 'star_forge':
                self.star_forge_mode = True
                self.star_forge_target = None
                return
            if panel_control and panel_control.action[0] == 'trade':
                try:
                    self.transaction.open()
                    self.movement_error = None
                except TransactionError as error:
                    self.movement_error = str(error)
                return
            self.player_panel.handle_click(x, y)
            if self.immediate_command_allocation:
                if self.turn_order.active_player.pending_commands == 0:
                    self.immediate_command_allocation = False
                    self.player_panel.source_pool = None
                return
            if self.turn_order.command_allocation and self.player_panel.player and \
                    self.player_panel.player.pending_commands == 0:
                finished = self.turn_order.finish_player_command_allocation()
                if finished and self.turn_order.strategy_enabled:
                    self.turn_order.begin_strategy_phase()
                    self.apply_council_patronage()
                    self.strategy_view = True
                self.player_panel.active = self.player_panel.players.index(self.turn_order.active_player)
                self.frame_player_systems(self.turn_order.active_player)
                self.player_panel.source_pool = None
                self.player_panel.card_offset = 0
            return
        if self.control_toggle_hit and self.control_toggle_hit[0] <= x <= self.control_toggle_hit[1] and \
                self.control_toggle_hit[2] <= y <= self.control_toggle_hit[3]:
            self.show_planet_control = not self.show_planet_control
            return
        if x >= left:
            if self.system_panel.technology_tab_hit and all((
                    self.system_panel.technology_tab_hit[0] <= x <= self.system_panel.technology_tab_hit[1],
                    self.system_panel.technology_tab_hit[2] <= y <= self.system_panel.technology_tab_hit[3])):
                self.technology_view = True
                return
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
        if self.star_forge_mode and button == arcade.MOUSE_BUTTON_LEFT:
            position = self.pick(x, y)
            if position is not None:
                tile = self.board[position]
                if any(unit.owner == self.turn_order.active_player.faction and unit.kind == 'warsun' and
                       unit.location.region == Region.SPACE for unit in tile.units):
                    self.selected = position
                    self.star_forge_target = position
                    self.star_forge_mode = False
                    self.movement_error = None
                else:
                    self.movement_error = 'Choose a system with one of your War Suns.'
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
                    if not self.turn_order.can_take_action and not self.smoke:
                        raise MovementError('Your action is complete. Pass the turn to continue.')
                    session = self.movement.activate(self.player_panel.player, picked)
                    self.movement_panel.reset()
                    self.movement_error = None
                    self.prompt_activation_agent(session)
                except MovementError as error:
                    self.movement_error = str(error)
                self.last_click = (None, 0.0)
            else:
                self.last_click = (picked, now)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.main_menu_visible or self.transaction_modal:
            return
        if (not self.focus_view and buttons & (arcade.MOUSE_BUTTON_RIGHT | arcade.MOUSE_BUTTON_MIDDLE) and
                self.roster.WIDTH <= x < self.width - self.sidebar and
                self.player_panel.HEIGHT <= y < self.height - 80):
            scale = self.fit_scale * self.zoom
            self.map_center[0] -= dx / scale
            self.map_center[1] -= dy / scale
            self.target_map_center[0] -= dx / scale
            self.target_map_center[1] -= dy / scale
            return
        if self.dragging_modal:
            if buttons & arcade.MOUSE_BUTTON_LEFT:
                if self.dragging_modal == 'strategy_card':
                    self.strategy_panel.move_card(dx, dy)
                elif self.dragging_modal == 'exploration':
                    self.exploration_panel.move(dx, dy)
                elif self.dragging_modal == 'relic':
                    self.relic_action_panel.move(dx, dy)
                elif self.dragging_modal == 'leader':
                    self.leader_panel.move(dx, dy)
                else:
                    panel = (self.strategy_panel if self.dragging_modal == 'strategy' else
                             self.technology_panel if self.dragging_modal == 'technology' else self.combat_panel)
                    panel.move(dx, dy)
                if self.dragging_modal in ('strategy', 'strategy_card'):
                    self.strategy_panel.update_hover(x, y)
                    self.strategy_hover_system = self.strategy_panel.hover_system
            else:
                self.dragging_modal = None
            return
        if (self.exploration_panel.is_visible(self.exploration) or
                self.relic_action_panel.open or self.leader_panel.open or
                self.strategy_modal or self.technology_modal or
                (self.movement.session and self.movement.session.stage in
                 ('space_cannon_choose_target', 'space_cannon_assign', 'space_combat', 'ground_combat', 'combat_end', 'retreat_selection', 'retreat_token_pool', 'assault_choice', 'space_combat_won')) or
                x < self.roster.WIDTH):
            return

    def on_mouse_release(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            self.dragging_modal = None

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        # On Windows, Pyglet's WM_MOUSEWHEEL path reports the cached pointer
        # position before applying _mouse_scale, while motion and button events
        # are already in window coordinates. Normalize it before hit testing.
        mouse_scale = float(getattr(self, '_mouse_scale', 1.0) or 1.0)
        if mouse_scale != 1.0:
            x /= mouse_scale
            y /= mouse_scale
        if self.main_menu_visible:
            return
        if self.exploration_panel.is_visible(self.exploration):
            self.exploration_panel.scroll_by(-scroll_y)
            return
        if self.relic_action_panel.open:
            self.relic_action_panel.scroll_by(-scroll_y)
            return
        if self.leader_panel.open:
            self.leader_panel.scroll_by(-scroll_y)
            return
        if self.transaction_modal:
            return
        if self.technology_modal:
            self.technology_panel.scroll_by(-scroll_y)
        if self.movement.session and self.movement.session.stage == 'space_cannon_assign':
            candidates = self.movement.space_cannon_assignment_candidates(self.movement.session)
            pages = max(1, (len(candidates) + 7) // 8)
            self.combat_panel.space_cannon_page = max(
                0, min(pages - 1, self.combat_panel.space_cannon_page - int(scroll_y)))
            return
        if self.movement.session and self.movement.session.stage in ('space_combat', 'ground_combat', 'combat_end', 'retreat_selection', 'retreat_token_pool', 'space_combat_won'):
            self.combat_panel.scroll_by(x, -scroll_y * 42)
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
            self.player_panel.scroll_planets(x, y, scroll_y, self.width - self.sidebar)
            return
        if x >= self.width - self.sidebar:
            if not self.inspector_visible:
                return
            if self.movement.session:
                self.movement_panel.scroll_by(-scroll_y * 40)
            else:
                self.system_panel.scroll -= scroll_y * 40
                self.system_panel.clamp()
        elif x < self.width - self.sidebar:
            self.zoom_map_at(x, y, scroll_y)

    def zoom_map_at(self, x, y, scroll_y):
        """Apply each wheel event immediately, anchored to the current pointer position."""
        if not scroll_y:
            return
        # Pyglet can report fractional wheel steps for high-resolution wheels
        # and touchpads. Exponential scaling preserves those partial steps.
        factor = 1.2 ** max(-32, min(32, scroll_y))
        if self.focus_view:
            self.focus_zoom = max(.7, min(1.4, self.focus_zoom * factor))
            return

        cx, cy = self.viewport_center
        current_scale = self.fit_scale * self.zoom
        pointer_world = (self.map_center[0] + (x - cx) / current_scale,
                         self.map_center[1] + (y - cy) / current_scale)
        next_zoom = max(.55, min(3.5, self.zoom * factor))
        if next_zoom == self.zoom:
            return
        next_scale = self.fit_scale * next_zoom
        next_center = [
            pointer_world[0] - (x - cx) / next_scale,
            pointer_world[1] - (y - cy) / next_scale,
        ]
        # Do not ease scale and camera position at different rates: that makes
        # the map slide away from the cursor while a wheel zoom is in progress.
        self.zoom = self.target_zoom = next_zoom
        self.map_center = next_center
        self.target_map_center = next_center[:]

    def on_key_press(self, symbol, modifiers):
        if self.main_menu_visible:
            if symbol in (arcade.key.ENTER, arcade.key.RETURN):
                self.start_from_menu()
            return
        if self.bot_actor:
            return
        if (self.ai.bot_factions and symbol == arcade.key.Z and modifiers & arcade.key.MOD_CTRL and
                self.ai.is_bot(self.turn_order.active_player)):
            return
        if self.exploration_panel.is_visible(self.exploration):
            if symbol == arcade.key.Z and modifiers & arcade.key.MOD_CTRL:
                if self.movement.undo():
                    self.exploration_panel.close()
                    self.hero_frontier_queue.clear()
                    self.naaz_hero_pending = False
                    self.movement_panel.reset()
            elif (symbol == arcade.key.ESCAPE and
                  self.exploration.pending is None and
                  self.exploration.freelancer_offer is None and
                  not self.exploration.secret_objectives.pending_returns):
                self.exploration_panel.close()
            return
        if self.relic_action_panel.open:
            if symbol == arcade.key.ESCAPE and self.relic_action_panel.alias != 'hand_discard':
                self.relic_action_panel.close()
            return
        if self.leader_panel.open:
            if symbol == arcade.key.ESCAPE:
                if self.leader_panel.reaction_owner_faction:
                    self.skip_activation_agent_window()
                else:
                    self.leader_panel.close()
            return
        if self.legendary_modal:
            if symbol == arcade.key.ESCAPE:
                self.legendary_modal = False
                self.legendary_choice = None
            return
        if self.naaz_hero_pending and self.strategy.session is None:
            if symbol == arcade.key.ESCAPE:
                self.naaz_hero_pending = False
            elif symbol == arcade.key.Z and modifiers & arcade.key.MOD_CTRL:
                if self.movement.undo():
                    self.naaz_hero_pending = False
                    self.strategy.hero_secondary_choices.pop(
                        (self.turn_order.active_player.faction, self.turn_order.turn_serial), None)
                    self.sync_turn_action()
            return
        self.sync_turn_action()
        if self.transaction_modal:
            if symbol == arcade.key.ESCAPE:
                self.transaction.cancel()
            return
        if self.technology_modal:
            if symbol == arcade.key.ESCAPE and not (self.strategy.session and
                                                    self.strategy.session.stage == 'technology'):
                self.technology_actions.cancel_transit()
                self.technology_view = False
            return
        if symbol == arcade.key.ESCAPE and self.orbital_drop_mode:
            self.orbital_drop_mode = False
            self.movement_error = None
            return
        if symbol == arcade.key.ESCAPE and (self.star_forge_mode or self.star_forge_target is not None):
            self.star_forge_mode = False
            self.star_forge_target = None
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
                self.hero_frontier_queue.clear()
                self.naaz_hero_pending = False
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
    parser.add_argument('--technology-smoke-test', action='store_true')
    args = parser.parse_args()
    map_path = args.map or ROOT / 'maps/three_player.json'
    if args.validate:
        _, board = load_board(map_path)
        for position in board:
            assert nearest_hex(*world(position)) == position
        print(f'PASS: {len(board)} tile objects, unique coordinates, all images present')
        return
    BoardWindow(smoke=args.smoke_test or args.menu_smoke_test, map_path=map_path,
                show_menu=args.menu_smoke_test or
                (args.map is None and not args.smoke_test and not args.technology_smoke_test),
                menu_smoke_test=args.menu_smoke_test,
                technology_smoke_test=args.technology_smoke_test)
    arcade.run()


if __name__ == '__main__':
    main()
