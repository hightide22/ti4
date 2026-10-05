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
    HEIGHT = 160

    def __init__(self, players):
        self.players = players
        self.active = 0
        self.controls = []
        self.textures = {}
        self.hovered_planet = None
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
            self.hovered_planet = None
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

    def hover(self, x, y):
        control = next((c for c in self.controls if c.action[0] == 'planet' and c.contains(x, y)), None)
        self.hovered_planet = control.action[1] if control else None

    @property
    def hovered_planet_id(self):
        if self.player and self.hovered_planet is not None and 0 <= self.hovered_planet < len(self.player.planets):
            return self.player.planets[self.hovered_planet].planet.planet_id
        return None

    def draw(self, window):
        self.controls.clear()
        width = window.width - window.sidebar
        height = self.HEIGHT
        arcade.draw_lrbt_rectangle_filled(0, width, 0, height, BG)
        arcade.draw_line(0, height, width, height, (48, 79, 101), 1)
        if not self.player:
            window.text('no_player', 'No player factions in this map', 16, height - 30, 13, MUTED)
            return
        for index, player in enumerate(self.players):
            label = 'JOL-NAR' if player.faction == 'jolnar' else player.faction.upper()
            self.button(window, ('player', index), label, 16 + index * 79,
                        height - 32, 72, height=24, selected=index == self.active)
        resources, influence = self.player.available_values
        self.image('planet_cards/pc_res_rdy.png', 267, height - 20, 16)
        self.image('planet_cards/pc_inf_rdy.png', 311, height - 20, 16)
        window.text('ready_resource_count', str(resources), 280, height - 25, 13, GOLD)
        window.text('ready_influence_count', str(influence), 324, height - 25, 13, ACCENT)
        reserve_left = width - 344
        reserve_width = 328
        self.draw_currencies(window, reserve_left, height - 17, reserve_width)
        self.draw_reserves(window, reserve_left, 32, reserve_width)
        window.text('planet_help', 'PLANETS  ·  Click to flip / hover for details', 16, 108, 11, MUTED)
        card_width, gap = 112, 8
        slots = max(1, int((reserve_left - 28) // (card_width + gap)))
        self.card_offset = min(self.card_offset, max(0, len(self.player.planets) - slots))
        if len(self.player.planets) > slots:
            self.button(window, ('cards', -1), '<', reserve_left - 65, 103, 24, height=22)
            self.button(window, ('cards', 1), '>', reserve_left - 37, 103, 24, height=22)
        for slot, index in enumerate(range(self.card_offset, min(len(self.player.planets), self.card_offset + slots))):
            self.draw_planet(window, index, 16 + slot * (card_width + gap), 12, card_width, 86)
        if self.hovered_planet is not None and self.hovered_planet < len(self.player.planets):
            self.draw_details(window)

    def draw_currencies(self, window, x, y, width):
        for column, (kind, name, path) in enumerate((
                ('trade_goods', 'Trade Goods', 'general/tg1.png'),
                ('commodities', 'Commodities', 'general/Commodity1.png'))):
            left = x + column * (width / 2 + 4)
            self.image(path, left + 13, y - 9, 24)
            value = str(self.player.trade_goods) if kind == 'trade_goods' else f'{self.player.commodities}/{self.player.commodity_limit}'
            window.text(('currency', kind), value, left + 33, y - 14, 18, GOLD)
            window.text(('currency_label', kind), name, left, y - 36, 11, MUTED)
            self.button(window, ('currency', kind, -1), '-', left + 92, y - 26, 25, height=24)
            self.button(window, ('currency', kind, 1), '+', left + 122, y - 26, 25, height=24)

    def draw_reserves(self, window, x, y, width):
        cell_width = (width - 12) / 3
        for index, pool in enumerate(('tactical', 'fleet', 'strategic')):
            left = x + index * (cell_width + 6)
            selected = pool == self.source_pool
            arcade.draw_lrbt_rectangle_filled(left, left + cell_width, y, y + 61,
                                             (32, 63, 77) if selected else CARD)
            count = self.player.command_pools[pool]
            sprite = 'fleet' if pool == 'fleet' else 'command'
            self.image(f'command_token/{sprite}_{self.player.color_code}.png', left + 23, y + 40, 29)
            self.image(f'factions/{self.player.faction}.png', left + 23, y + 38, 12)
            window.text(('pool_count', pool), str(count), left + 48, y + 31, 20, ACCENT)
            window.text(('pool_name', pool), pool.title(), left + 9, y + 9, 11, INK)
            self.controls.append(Control(('pool', pool), left, y, cell_width, 61))
        hint = 'Choose destination' if self.source_pool else 'Commands: source → destination'
        window.text('pool_help', hint, x, 13, 11, MUTED)

    def trait_image(self, planet):
        if planet.faction_homeworld:
            return f'factions/{planet.faction_homeworld}.png'
        trait = (planet.planet_type or 'none').lower()
        if trait == 'faction':
            return f'factions/{self.player.faction}.png'
        return f'planet_cards/pc_attribute_{trait}.png'

    def draw_planet(self, window, index, x, y, width, height):
        card = self.player.planets[index]
        planet = card.planet
        exhausted = card.exhausted
        hit_y = y
        hovered = index == self.hovered_planet
        if hovered:
            y += 3
            arcade.draw_lrbt_rectangle_filled(x - 3, x + width + 3, y - 3, y + height + 3, (*ACCENT, 24))
        background = (29, 53, 68) if hovered else (19, 29, 43) if exhausted else CARD
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height, background)
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + height,
                                          ACCENT if hovered else (64, 74, 90) if exhausted else (67, 133, 154), 2 if hovered else 1)
        window.text(('planet_name', index), planet.name, x + 8, y + height - 19, 12, INK, width - 16)
        tint = (140, 150, 164, 170) if exhausted else None
        self.image(self.trait_image(planet), x + 21, y + 43, 25, tint)
        window.text(('planet_state', index), 'Exhausted' if exhausted else 'Ready',
                    x + 39, y + 38, 11, MUTED if exhausted else ACCENT)
        suffix = 'exh' if exhausted else 'rdy'
        self.image(f'planet_cards/pc_res_{suffix}.png', x + 16, y + 15, 18)
        self.image(f'planet_cards/pc_inf_{suffix}.png', x + 70, y + 15, 18)
        window.text(('planet_res', index), str(planet.resources), x + 30, y + 9, 14, GOLD if not exhausted else MUTED)
        window.text(('planet_inf', index), str(planet.influence), x + 84, y + 9, 14, ACCENT if not exhausted else MUTED)
        if planet.tech_specialties:
            self.image(f'planet_cards/pc_tech_{planet.tech_specialties[0].lower()}_{suffix}.png', x + width - 12, y + 59, 13)
        if planet.legendary_ability_name:
            self.image(f'planet_cards/pc_legendary_{suffix}.png', x + width - 12, y + 58, 14)
        self.controls.append(Control(('planet', index), x, hit_y, width, height))

    def draw_details(self, window):
        card = self.player.planets[self.hovered_planet]
        planet = card.planet
        x, y = 16, self.HEIGHT + 12
        arcade.draw_lrbt_rectangle_filled(x, x + 306, y, y + 111, (16, 31, 48, 250))
        arcade.draw_lrbt_rectangle_outline(x, x + 306, y, y + 111, (67, 133, 154), 1)
        self.image(self.trait_image(planet), x + 24, y + 85, 28)
        window.text('planet_detail_name', planet.name, x + 47, y + 82, 16, INK)
        state = 'Exhausted' if card.exhausted else 'Ready'
        kind = 'Homeworld' if planet.faction_homeworld else (planet.planet_type or 'Planet').title()
        window.text('planet_detail_state', f'{kind} · {state}', x + 15, y + 58, 12, MUTED)
        window.text('planet_detail_values', f'Resources {planet.resources}   Influence {planet.influence}', x + 15, y + 35, 13, ACCENT)
        extra = ', '.join(planet.tech_specialties) or 'No technology specialty'
        window.text('planet_detail_extra', extra, x + 15, y + 13, 11, MUTED)
