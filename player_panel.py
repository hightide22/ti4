from __future__ import annotations

from dataclasses import dataclass
import arcade
from ui_theme import (PANEL, SHELL, CARD, INK, MUTED, ACCENT, GOLD, BORDER, SELECTED, DISABLED,
                      DASHBOARD_HEIGHT, VARIANT, surface, button)

from board import RESOURCES



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
    HEIGHT = DASHBOARD_HEIGHT

    def __init__(self, players):
        self.players = players
        self.active = 0
        self.controls = []
        self.textures = {}
        self.hovered_planet = None
        self.production_planets = set()
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
        button(window, ('player_button', action), label, left, bottom, width, height,
               selected=selected, size=11)
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
            return
        elif action == 'currency':
            self.player.change_currency(*args)
        elif action == 'pool':
            target = args[0]
            if self.source_pool is None:
                if self.player.command_pools[target]:
                    self.source_pool = target
            elif self.source_pool == 'pending':
                if self.player.allocate_command(target):
                    self.source_pool = 'pending' if self.player.pending_commands else None
            else:
                self.player.transfer_command(self.source_pool, target)
                self.source_pool = None
        elif action == 'pending':
            if self.source_pool == 'pending':
                self.source_pool = None
            elif self.player.pending_commands:
                self.source_pool = 'pending'
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

    def draw(self, window, show_details=True, interactive=True):
        self.controls.clear()
        self.interactive = interactive
        width = window.width - window.sidebar
        height = self.HEIGHT
        arcade.draw_lrbt_rectangle_filled(0, width, 0, height, SHELL)
        arcade.draw_line(0, height, width, height, BORDER, 1)
        if not self.player:
            window.text('no_player', 'No player factions in this map', 16, height - 30, 13, MUTED)
            return
        window.text('dashboard_player', f'{self.player.faction.upper()} / PLAYER RESOURCES',
                    16, height - 25, 11, ACCENT)
        resources, influence = self.player.available_values
        self.image('planet_cards/pc_res_rdy.png', 267, height - 20, 16)
        self.image('planet_cards/pc_inf_rdy.png', 311, height - 20, 16)
        window.text('ready_resource_count', str(resources), 280, height - 25, 13, GOLD)
        window.text('ready_influence_count', str(influence), 324, height - 25, 13, ACCENT)
        reserve_left = width - 344
        reserve_width = 328
        self.draw_currencies(window, reserve_left, height - 17, reserve_width)
        self.draw_reserves(window, reserve_left, 32, reserve_width)
        leadership = window.strategy.session and window.strategy.session.stage == 'leadership'
        help_text = ('AI TURN · Planet cards are read-only' if not interactive else
                     'ORBITAL DROP · Click a controlled planet' if window.orbital_drop_mode else
                     'TECHNOLOGY · Click a planet to pay or use its specialty'
                     if window.strategy.session and window.strategy.session.stage == 'technology' else
                     'PLANETS · Click to pay influence' if leadership else
                     'PLANETS · Click to pay resources' if window.movement.session and window.movement.session.stage == 'production'
                     else 'PLANETS · Hover for details')
        active = window.turn_order.active_player
        can_use_orbital_drop = (self.player is active and self.player.faction == 'sol' and
                                self.player.command_pools['strategic'] > 0 and
                                window.turn_order.can_take_action and not window.turn_order.command_allocation and
                                not window.turn_order.strategy_selection and not window.strategy.session and
                                not window.movement.session)
        # Keep the faction action visible whenever the Sol dashboard is open.
        # It stays disabled outside Sol's turn, but this makes the ability
        # discoverable instead of making the button disappear entirely.
        sol_action_window = interactive and self.player.faction == 'sol'
        star_forge_action_window = interactive and self.player.faction == 'muaat'
        war_sun_available = any(unit.owner == self.player.faction and unit.kind == 'warsun' and
                                unit.location.region.value == 'space'
                                for tile in window.board.values() for unit in tile.units)
        can_use_star_forge = (self.player is active and war_sun_available and
                              self.player.command_pools['strategic'] > 0 and
                              window.turn_order.can_take_action and not window.turn_order.command_allocation and
                              not window.turn_order.strategy_selection and not window.strategy.session and
                              not window.movement.session)
        can_trade = (self.player is active and not window.turn_order.command_allocation and
                     not window.turn_order.strategy_selection and not window.strategy.session and
                     not window.movement.session and not window.orbital_drop_mode)
        if sol_action_window:
            help_text = ('ORBITAL DROP · Click the button, then a controlled planet'
                         if can_use_orbital_drop or window.orbital_drop_mode else
                         'ORBITAL DROP · Available after strategy selection and command allocation'
                         if window.turn_order.strategy_selection or window.turn_order.command_allocation else
                         'ORBITAL DROP · Available on Sol’s turn' if self.player is not active else
                         'ORBITAL DROP · No token in the strategy pool'
                         if self.player.command_pools['strategic'] <= 0 else
                         'ORBITAL DROP · Finish the current action first'
                         if window.strategy.session or window.movement.session else
                         'ORBITAL DROP · Your action has already been used')
        if interactive and can_trade:
            if self.player.faction == 'sol':
                self.button(window, ('trade',), 'TRADE', width - 592, height - 45, 52, 24)
            else:
                self.button(window, ('trade',), 'TRADE', width - 469, height - 45, 111, 24)
        if sol_action_window:
            available = can_use_orbital_drop or window.orbital_drop_mode
            label = 'CANCEL ORBITAL DROP' if window.orbital_drop_mode else 'ORBITAL DROP'
            button(window, ('player_button', ('orbital_drop',)), label,
                   width - 534, height - 45, 108, 24,
                   selected=window.orbital_drop_mode, enabled=available, size=9)
            if available:
                self.controls.append(Control(('orbital_drop',), width - 534, height - 45, 108, 24))
        if star_forge_action_window:
            available = can_use_star_forge or window.star_forge_mode
            if window.star_forge_mode:
                help_text = 'STAR FORGE · Click a system with your War Sun'
            elif self.player is not active:
                help_text = 'STAR FORGE · Available on Muaat’s turn'
            elif not war_sun_available:
                help_text = 'STAR FORGE · No War Sun on the board'
            elif self.player.command_pools['strategic'] <= 0:
                help_text = 'STAR FORGE · No token in the strategy pool'
            elif not window.turn_order.can_take_action:
                help_text = 'STAR FORGE · Your action has already been used'
            else:
                help_text = 'STAR FORGE · Place 2 fighters or 1 destroyer by your War Sun'
            button(window, ('player_button', ('star_forge',)),
                   'CANCEL STAR FORGE' if window.star_forge_mode else 'STAR FORGE',
                   width - 534, height - 45, 108, 24, selected=window.star_forge_mode,
                   enabled=available, size=9)
            if available:
                self.controls.append(Control(('star_forge',), width - 534, height - 45, 108, 24))
        if window.mitosis_player is self.player:
            help_text = 'MITOSIS · Choose one controlled planet for 1 infantry'
        window.text('planet_help', help_text, 16, height - 64, 11, MUTED)
        card_width, gap = (138 if VARIANT == 'Atlas' else 126), 8
        slots = max(1, int((reserve_left - 28) // (card_width + gap)))
        self.card_offset = min(self.card_offset, max(0, len(self.player.planets) - slots))
        if interactive and len(self.player.planets) > slots:
            self.button(window, ('cards', -1), '<', reserve_left - 65, height - 69, 24, height=22)
            self.button(window, ('cards', 1), '>', reserve_left - 37, height - 69, 24, height=22)
        for slot, index in enumerate(range(self.card_offset, min(len(self.player.planets), self.card_offset + slots))):
            self.draw_planet(window, index, 16 + slot * (card_width + gap), 12, card_width, height - 86)
        if show_details and self.hovered_planet is not None and self.hovered_planet < len(self.player.planets):
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
            if self.interactive:
                self.button(window, ('currency', kind, -1), '-', left + 92, y - 26, 25, height=24)
                self.button(window, ('currency', kind, 1), '+', left + 122, y - 26, 25, height=24)

    def draw_reserves(self, window, x, y, width):
        cell_width = (width - 12) / 3
        cell_height = self.HEIGHT - 111
        for index, pool in enumerate(('tactical', 'fleet', 'strategic')):
            left = x + index * (cell_width + 6)
            selected = pool == self.source_pool
            arcade.draw_lrbt_rectangle_filled(left, left + cell_width, y, y + cell_height,
                                             SELECTED if selected else CARD)
            count = self.player.command_pools[pool]
            sprite = 'fleet' if pool == 'fleet' else 'command'
            self.image(f'command_token/{sprite}_{self.player.color_code}.png', left + 23, y + cell_height - 21, 29)
            self.image(f'factions/{self.player.faction}.png', left + 23, y + cell_height - 23, 12)
            count_label = f'{count} + 2' if pool == 'fleet' and self.player.faction == 'letnev' else str(count)
            pool_label = 'Fleet limit' if pool == 'fleet' and self.player.faction == 'letnev' else pool.title()
            window.text(('pool_count', pool), count_label, left + 48, y + cell_height - 30, 20, ACCENT)
            window.text(('pool_name', pool), pool_label, left + 9, y + 9, 11, INK)
            if self.interactive:
                self.controls.append(Control(('pool', pool), left, y, cell_width, cell_height))
        if self.player.pending_commands:
            pending_bottom, pending_height = 3, 24
            selected_pending = self.source_pool == 'pending'
            arcade.draw_lrbt_rectangle_filled(x, x + width, pending_bottom, pending_bottom + pending_height,
                                              SELECTED if selected_pending else CARD)
            arcade.draw_lrbt_rectangle_outline(x, x + width, pending_bottom, pending_bottom + pending_height,
                                               ACCENT if selected_pending else BORDER, 1)
            instruction = ('AI is allocating' if not self.interactive else
                           'choose a pool' if window.strategy.session else 'click to allocate to a pool')
            window.text('pending_command_help',
                        f'New commands: {self.player.pending_commands} · {instruction}',
                        x + 8, pending_bottom + 7, 10, ACCENT if selected_pending else INK, width - 16)
            if self.interactive:
                self.controls.append(Control(('pending',), x, pending_bottom, width, pending_height))
        else:
            hint = ('AI command pools' if not self.interactive else
                    ('Choose destination' if self.source_pool else 'Commands: source → destination')
                    if window.turn_order.command_allocation else 'Command pools')
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
        paying_for_production = planet.planet_id in self.production_planets
        hit_y = y
        hovered = index == self.hovered_planet
        if hovered:
            y += 3
            arcade.draw_lrbt_rectangle_filled(x - 3, x + width + 3, y - 3, y + height + 3, (*ACCENT, 24))
        orbital_target = (window.orbital_drop_mode and self.player is window.turn_order.active_player)
        background = (SELECTED if paying_for_production else
                      SELECTED if orbital_target or hovered else DISABLED if exhausted else CARD)
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height, background)
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + height,
                                          GOLD if paying_for_production or orbital_target else ACCENT if hovered else
                                          BORDER if exhausted else ACCENT,
                                          2 if paying_for_production or orbital_target or hovered else 1)
        window.text(('planet_name', index), planet.name, x + 8, y + height - 19, 12, INK, width - 16)
        tint = (140, 150, 164, 170) if exhausted else None
        self.image(self.trait_image(planet), x + 21, y + 43, 25, tint)
        window.text(('planet_state', index), 'Paid' if paying_for_production else 'Exhausted' if exhausted else 'Ready',
                    x + 39, y + 38, 10, MUTED if exhausted else ACCENT)
        suffix = 'exh' if exhausted else 'rdy'
        self.image(f'planet_cards/pc_res_{suffix}.png', x + 16, y + 15, 18)
        self.image(f'planet_cards/pc_inf_{suffix}.png', x + 70, y + 15, 18)
        window.text(('planet_res', index), str(planet.resources), x + 30, y + 9, 14, GOLD if not exhausted else MUTED)
        window.text(('planet_inf', index), str(planet.influence), x + 84, y + 9, 14, ACCENT if not exhausted else MUTED)
        if planet.tech_specialties:
            self.image(f'planet_cards/pc_tech_{planet.tech_specialties[0].lower()}_{suffix}.png', x + width - 12, y + 59, 13)
        self.controls.append(Control(('planet', index), x, hit_y, width, height))

    def draw_details(self, window):
        card = self.player.planets[self.hovered_planet]
        planet = card.planet
        x, y = 16, self.HEIGHT + 12
        surface(x, x + 330, y, y + 125, PANEL, ACCENT)

        self.image(self.trait_image(planet), x + 24, y + 85, 28)
        window.text('planet_detail_name', planet.name, x + 47, y + 82, 16, INK, max_width=270)
        state = 'Exhausted' if card.exhausted else 'Ready'
        kind = 'Homeworld' if planet.faction_homeworld else (planet.planet_type or 'Planet').title()
        window.text('planet_detail_state', f'{kind} · {state}', x + 15, y + 58, 12, MUTED)
        window.text('planet_detail_values', f'Resources {planet.resources}   Influence {planet.influence}', x + 15, y + 35, 13, ACCENT)
        extra = ', '.join(planet.tech_specialties) or 'No technology specialty'
        window.text('planet_detail_extra', extra, x + 15, y + 13, 11, MUTED)
