from __future__ import annotations

from dataclasses import dataclass
import arcade

from board import RESOURCES

BG = (12, 22, 36)
CARD = (23, 39, 57)
INK = (223, 232, 244)
MUTED = (132, 154, 180)
ACCENT = (100, 207, 224)
GOLD = (245, 194, 103)


@dataclass
class Control:
    action: tuple
    left: float
    bottom: float
    width: float
    height: float

    def contains(self, x, y):
        return self.left <= x <= self.left + self.width and self.bottom <= y <= self.bottom + self.height


class PlayerPanel:
    HEIGHT = 258

    def __init__(self, players):
        self.players = players
        self.active = 0
        self.controls = []
        self.textures = {}
        self.planet_textures = {}
        self.source_pool = None
        self.card_offset = 0

    @property
    def player(self):
        return self.players[self.active] if self.players else None

    def image(self, path, x, y, size, color=None):
        path = RESOURCES / path
        if path not in self.textures:
            self.textures[path] = arcade.load_texture(path)
        texture = self.textures[path]
        rect = arcade.XYWH(x, y, size * texture.width / max(texture.width, texture.height),
                           size * texture.height / max(texture.width, texture.height))
        if color is None:
            arcade.draw_texture_rect(texture, rect)
        else:
            arcade.draw_texture_rect(texture, rect, color=arcade.types.Color(*color))

    def button(self, window, action, label, left, bottom, width, height=25, selected=False):
        arcade.draw_lrbt_rectangle_filled(left, left + width, bottom, bottom + height,
                                         (34, 72, 88) if selected else CARD)
        window.text(('player_button', action), label, left + 8, bottom + 7, 11, ACCENT if selected else INK)
        self.controls.append(Control(action, left, bottom, width, height))

    def handle_click(self, x, y):
        control = next((c for c in reversed(self.controls) if c.contains(x, y)), None)
        if not control:
            return
        action, *args = control.action
        if action == 'player':
            self.active = args[0]
            self.source_pool = None
            self.card_offset = 0
        elif action == 'planet':
            self.player.planets[args[0]].flip()
        elif action == 'currency':
            self.player.change_currency(*args)
        elif action == 'pool':
            target = args[0]
            if self.source_pool is None:
                if self.player.command_pools[target]:
                    self.source_pool = target
            else:
                self.player.transfer_command(self.source_pool, target)
                self.source_pool = None
        elif action == 'cards':
            self.card_offset = max(0, min(len(self.player.planets) - 1, self.card_offset + args[0]))

    def draw(self, window):
        self.controls.clear()
        width = window.width - window.sidebar
        height = self.HEIGHT
        arcade.draw_lrbt_rectangle_filled(0, width, 0, height, BG)
        arcade.draw_line(0, height, width, height, (48, 79, 101), 2)
        if not self.player:
            window.text('no_player', 'No player factions in this map', 24, height - 42, 14, MUTED)
            return
        for index, player in enumerate(self.players):
            self.button(window, ('player', index), ('JOL-NAR' if player.faction == 'jolnar' else player.faction.upper()), 24 + index * 86,
                        height - 40, 78, selected=index == self.active)
        resources, influence = self.player.available_values
        window.text('ready_values', f'Ready planets: {resources} resources / {influence} influence',
                    24, height - 62, 11, MUTED)
        reserve_left = max(width * .58, width - 405)
        reserve_width = width - reserve_left - 24
        self.draw_currencies(window, reserve_left, height - 32, reserve_width)
        self.draw_reserves(window, reserve_left, 46, reserve_width)
        window.text('planet_help', 'PLANETS  ·  Click a card to exhaust / ready', 24, 170, 10, MUTED)
        card_width, gap = 136, 12
        slots = max(1, int((reserve_left - 38) // (card_width + gap)))
        self.card_offset = min(self.card_offset, max(0, len(self.player.planets) - slots))
        if len(self.player.planets) > slots:
            self.button(window, ('cards', -1), '<', reserve_left - 76, 165, 26)
            self.button(window, ('cards', 1), '>', reserve_left - 44, 165, 26)
        for slot, index in enumerate(range(self.card_offset, min(len(self.player.planets), self.card_offset + slots))):
            self.draw_planet(window, index, 24 + slot * (card_width + gap), 16, card_width, 143)

    def draw_currencies(self, window, x, y, width):
        for column, (kind, name, path) in enumerate((
                ('trade_goods', 'TRADE GOODS', 'general/tg1.png'),
                ('commodities', 'COMMODITIES', 'general/Commodity1.png'))):
            left = x + column * width / 2
            self.image(path, left + 18, y - 9, 34)
            value = str(self.player.trade_goods) if kind == 'trade_goods' else f'{self.player.commodities}/{self.player.commodity_limit}'
            window.text(('currency', kind), value, left + 42, y - 9, 23, GOLD)
            window.text(('currency_label', kind), name, left, y - 36, 10, MUTED)
            self.button(window, ('currency', kind, -1), '-', left, y - 70, 29)
            self.button(window, ('currency', kind, 1), '+', left + 35, y - 70, 29)

    def draw_reserves(self, window, x, y, width):
        cell_width = (width - 16) / 3
        for index, pool in enumerate(('tactical', 'fleet', 'strategic')):
            left = x + index * (cell_width + 8)
            selected = pool == self.source_pool
            arcade.draw_lrbt_rectangle_filled(left, left + cell_width, y, y + 89,
                                             (32, 63, 77) if selected else CARD)
            count = self.player.command_pools[pool]
            sprite = 'fleet' if pool == 'fleet' else 'command'
            self.image(f'command_token/{sprite}_{self.player.color_code}.png', left + 27, y + 55, 39)
            self.image(f'factions/{self.player.faction}.png', left + 27, y + 52, 15)
            window.text(('pool_count', pool), str(count), left + 52, y + 45, 24, ACCENT)
            window.text(('pool_name', pool), pool.title(), left + 10, y + 13, 11, INK)
            self.controls.append(Control(('pool', pool), left, y, cell_width, 89))
        hint = 'Choose a destination pool' if self.source_pool else 'Click source, then destination to move a token'
        window.text('pool_help', hint, x, y - 24, 10, MUTED)

    def draw_planet(self, window, index, x, y, width, height):
        card = self.player.planets[index]
        planet = card.planet
        exhausted = card.exhausted
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height, (19, 29, 43) if exhausted else CARD)
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + height,
                                          (64, 74, 90) if exhausted else (67, 133, 154), 1)
        if exhausted:
            self.image(f'factions/{self.player.faction}.png', x + width / 2, y + 79, 66,
                       (160, 172, 188, 145))
            window.text(('planet_state', index), 'EXHAUSTED', x + 27, y + 31, 10, MUTED)
        else:
            if planet.planet_id not in self.planet_textures:
                tile = next(t for t in window.board.values() if planet in t.planets)
                texture = window.tile_sprites[tile].texture
                cx, cy = planet.center
                radius = min(40, int(planet.radius - 4))
                self.planet_textures[planet.planet_id] = texture.crop(int(cx) - radius, int(cy) - radius,
                                                                   radius * 2, radius * 2)
            arcade.draw_texture_rect(self.planet_textures[planet.planet_id], arcade.XYWH(x + width / 2, y + 79, 78, 78))
            window.text(('planet_state', index), 'READY', x + 48, y + 31, 10, ACCENT)
        window.text(('planet_name', index), planet.name, x + 10, y + height - 20, 12, INK)
        suffix = 'exh' if exhausted else 'rdy'
        self.image(f'planet_cards/pc_res_{suffix}.png', x + 17, y + 15, 18)
        self.image(f'planet_cards/pc_inf_{suffix}.png', x + 78, y + 15, 18)
        window.text(('planet_res', index), str(planet.resources), x + 31, y + 9, 13, GOLD if not exhausted else MUTED)
        window.text(('planet_inf', index), str(planet.influence), x + 92, y + 9, 13, ACCENT if not exhausted else MUTED)
        self.controls.append(Control(('planet', index), x, y, width, height))
