"""Exploration choice modal and the player's fragment/relic inventory."""
from __future__ import annotations

from pathlib import Path
import textwrap

import arcade

from board import RESOURCES
from technology import production_allowed
from units import UNIT_TYPES
from ui_theme import (ACCENT, BORDER, CARD, GOLD, INK, MUTED,
                      PANEL, SELECTED, button, modal, surface)


FRAGMENT_TYPES = ('cultural', 'hazardous', 'industrial', 'frontier')
TRAIT_COLORS = {
    'cultural': (112, 186, 238), 'hazardous': (241, 133, 104),
    'industrial': (151, 210, 130), 'frontier': (194, 177, 240),
}
FRAGMENT_IMAGES = {
    'cultural': 'player_area/pa_fragment_crf.png',
    'hazardous': 'player_area/pa_fragment_hrf.png',
    'industrial': 'player_area/pa_fragment_irf.png',
    'frontier': 'player_area/pa_fragment_urf.png',
}
ACTION_RELICS = {'codex', 'dynamiscore', 'nanoforge', 'stellarconverter',
                 'titanprototype'}
CHOICE_LABELS = {
    'produce_one_unit': 'PRODUCE 1 UNIT', 'decline': 'DECLINE',
    'place_infantry': 'PLACE 1 INFANTRY', 'use_mech': 'USE MECH',
    'remove_infantry': 'REMOVE 1 INFANTRY',
    'gain_commodities': 'GAIN 2 COMMODITIES',
    'convert_commodities': 'CONVERT COMMODITIES',
    'gain_commodity': 'GAIN 1 COMMODITY',
    'draw_card_trade_good': 'PAY 1 TRADE GOOD · DRAW CARD',
    'draw_card_commodity': 'PAY 1 COMMODITY · DRAW CARD',
    'place_mech_trade_good': 'PAY 1 TRADE GOOD · PLACE MECH',
    'place_mech_commodity': 'PAY 1 COMMODITY · PLACE MECH',
    'replenish_commodities': 'REPLENISH COMMODITIES',
    'alpha': 'ALPHA SIDE', 'beta': 'BETA SIDE',
}


class ExplorationPanel:
    """Draws UI and returns semantic click tuples through ``hit_test``.

    ``('resolve', choice)`` selects an offered Distant Suns card or resolves a
    card option; ``('assemble_relic', aliases, required)`` purges fragments;
    ``('relic_action', alias)`` asks the caller to begin an action window.
    """

    def __init__(self):
        self.open = False
        self.result = None
        self.result_mode = False
        self.hits = []
        self.bounds = None
        self.offset = [0.0, 0.0]
        self.scroll = 0
        self.scroll_max = 0
        self._textures = {}
        self.fabrication_trait = None
        self.freelancer_offer_key = None
        self.freelancer_kind = None
        self.freelancer_planet = None
        self.freelancer_resource_planets = set()
        self.freelancer_influence_planets = set()
        self.freelancer_trade_goods = 0
        self.freelancer_payment_page = 0

    def is_visible(self, exploration) -> bool:
        return (self.open or exploration.pending is not None or
                exploration.freelancer_offer is not None or
                exploration.terragenesis_pending is not None or
                bool(exploration.secret_objectives.pending_returns))

    def show_result(self, outcome) -> None:
        if outcome.card is not None and outcome.resolved:
            self.result = outcome
            self.result_mode = True
            self.open = True

    def close(self) -> None:
        self.open = False
        self.result = None
        self.result_mode = False
        self.freelancer_offer_key = None
        self.freelancer_kind = None
        self.freelancer_planet = None
        self.freelancer_resource_planets.clear()
        self.freelancer_influence_planets.clear()
        self.freelancer_trade_goods = 0
        self.freelancer_payment_page = 0
        self.hits.clear()
        self.bounds = None

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def drag_header(self, x, y):
        if self.bounds is None:
            return False
        left, right, bottom, top = self.bounds
        return left <= x <= right and top - 57 <= y <= top - 4

    def move(self, dx, dy):
        self.offset[0] += dx
        self.offset[1] += dy

    def scroll_by(self, delta):
        self.scroll = max(0, min(self.scroll_max, self.scroll + int(delta) * 40))

    @staticmethod
    def available_fragment_sets(exploration, faction):
        owned = exploration.fragments[faction]
        required = 2 if faction == 'naaz' else 3
        wild = [alias for alias in owned if exploration.catalog[alias].trait == 'frontier']
        choices = []
        for trait in FRAGMENT_TYPES[:-1]:
            normal = [alias for alias in owned if exploration.catalog[alias].trait == trait]
            selected = tuple((normal + wild)[:required])
            if normal and len(selected) == required:
                choices.append((trait, selected, required))
        if len(wild) >= required:
            choices.append(('frontier', tuple(wild[:required]), required))
        return choices

    def _image(self, path, x, y, width, height):
        path = Path(path)
        if not path.is_file():
            return
        texture = self._textures.get(path)
        if texture is None:
            texture = arcade.load_texture(path)
            self._textures[path] = texture
        ratio = min(width / texture.width, height / texture.height)
        arcade.draw_texture_rect(texture, arcade.XYWH(
            x, y, texture.width * ratio, texture.height * ratio))

    def _lines(self, window, key, text, x, top, width, *, size=10,
               line_height=18, max_lines=11, color=INK):
        clean = ' '.join(text.replace('\\n', ' ').split())
        rows = textwrap.wrap(clean, width=max(21, int(width / (size * .59))))
        for index, line in enumerate(rows[:max_lines]):
            window.text((key, index), line, x, top - index * line_height,
                        size, color, width)
        if len(rows) > max_lines:
            window.text((key, 'more'), '…', x, top - max_lines * line_height,
                        size, MUTED)

    def _window_rect(self, window, width, height):
        dashboard = window.player_panel.HEIGHT
        width = min(width, window.width - 32)
        height = min(height, window.height - dashboard - 24)
        base_x = (window.width - width) / 2
        base_y = dashboard + (window.height - dashboard - height) / 2
        x = max(0, min(window.width - width, base_x + self.offset[0]))
        bottom = max(dashboard, min(window.height - height,
                                    base_y + self.offset[1]))
        self.offset[:] = [x - base_x, bottom - base_y]
        self.bounds = (x, x + width, bottom, bottom + height)
        return x, bottom, width, height

    def _frame(self, window, x, bottom, width, height, title, *, close=True):
        arcade.draw_lrbt_rectangle_filled(0, window.width, window.player_panel.HEIGHT,
                                          window.height, (3, 7, 14, 190))
        modal(x, x + width, bottom, bottom + height)
        top = bottom + height
        window.text('exploration_panel_title', title, x + 24, top - 37, 17, ACCENT)
        window.text('exploration_drag_hint', 'DRAG HEADER TO MOVE', x + width - 200,
                    top - 30, 8, MUTED)
        if close:
            bx, by = x + width - 106, top - 67
            button(window, 'exploration_close', 'CLOSE', bx, by, 78, 29, size=9)
            self.hits.append((('close',), bx, bx + 78, by, by + 29))

    def draw(self, window, player, exploration):
        self.hits.clear()
        if not self.is_visible(exploration):
            self.bounds = None
            return
        if exploration.pending:
            self._draw_pending(window, exploration)
        elif exploration.freelancer_offer:
            self._draw_freelancers(window, player, exploration)
        elif player.faction in exploration.secret_objectives.pending_returns:
            self._draw_secret_return(window, player, exploration)
        elif self.result_mode and self.result:
            self._draw_result(window)
        elif exploration.terragenesis_pending is not None:
            self._draw_terragenesis(window, player, exploration)
        else:
            self._draw_inventory(window, player, exploration)

    def _draw_terragenesis(self, window, player, exploration):
        pending = exploration.terragenesis_pending
        tile = window.board[pending.position]
        planet = next(planet for planet in tile.planets
                      if planet.planet_id == pending.planet_id)
        x, bottom, width, height = self._window_rect(window, 760, 490)
        self._frame(window, x, bottom, width, height,
                    'TERRAGENESIS · PLACE OR MOVE A SLEEPER', close=False)
        top = bottom + height
        window.text('terragenesis_help',
                    f'{player.faction.upper()} · Place a sleeper on {planet.name}, or move one from another planet.',
                    x + 24, top - 91, 10, MUTED, width - 48)
        sources = window.movement.terragenesis_sources()
        row_y = top - 157
        row_step = 48
        for index, (source_tile, source_planet) in enumerate(sources):
            by = row_y - index * row_step
            label = f'MOVE FROM {source_planet.name} · {source_tile.name}'
            button(window, ('move_sleeper', source_planet.planet_id), label,
                   x + 24, by, width - 48, 37, primary=True, size=9)
            self.hits.append((('move_sleeper', source_planet.planet_id),
                              x + 24, x + width - 24, by, by + 37))
        decline_y = bottom + 24
        button(window, 'decline_terragenesis', 'DECLINE',
               x + width - 132, decline_y, 108, 34, size=9)
        self.hits.append((('decline_terragenesis',), x + width - 132,
                          x + width - 24, decline_y, decline_y + 34))

    def _draw_secret_return(self, window, player, exploration):
        deck = exploration.secret_objectives
        x, bottom, width, height = self._window_rect(window, 760, 570)
        self._frame(window, x, bottom, width, height,
                    'SECRET OBJECTIVE · RETURN ONE', close=False)
        top = bottom + height
        excess = deck.pending_returns[player.faction]
        window.text('secret_return_help',
                    f'{player.faction.upper()} must return {excess} unscored secret objective(s).',
                    x + 24, top - 103, 11, GOLD, width - 48)
        for index, card in enumerate(deck.hand_for(player.faction)):
            row_top = top - 137 - index * 98
            row_bottom = row_top - 82
            if row_bottom < bottom + 20:
                break
            surface(x + 24, x + width - 24, row_bottom, row_top, CARD, BORDER)
            window.text(('secret_return_name', index), card.name,
                        x + 38, row_top - 25, 12, INK, width - 210)
            self._lines(window, ('secret_return_text', index), card.text,
                        x + 38, row_top - 44, width - 210,
                        size=8, line_height=13, max_lines=2, color=MUTED)
            bx, by = x + width - 150, row_top - 60
            button(window, ('secret_return', card.alias), 'RETURN',
                   bx, by, 106, 33, primary=True, size=9)
            self.hits.append((('return_secret', card.alias),
                              bx, bx + 106, by, by + 33))

    def _draw_freelancers(self, window, player, exploration):
        offer = exploration.freelancer_offer
        if offer != self.freelancer_offer_key:
            self.freelancer_offer_key = offer
            self.freelancer_kind = None
            self.freelancer_planet = offer[2]
            self.freelancer_resource_planets.clear()
            self.freelancer_influence_planets.clear()
            self.freelancer_trade_goods = 0
            self.freelancer_payment_page = 0
        tile = window.board[offer[1]]
        x, bottom, width, height = self._window_rect(window, 990, 690)
        self._frame(window, x, bottom, width, height,
                    'FREELANCERS · PRODUCE ONE UNIT', close=False)
        top = bottom + height
        window.text('freelancers_help',
                    f'{player.faction.upper()} · {tile.name} · Pay with ready resources, influence, or trade goods.',
                    x + 24, top - 101, 10, MUTED, width - 48)
        left, right = x + 24, x + min(450, width * .47)
        left_width, right_width = right - left - 14, x + width - right - 24
        window.text('freelancers_units_title', 'CHOOSE ONE UNIT', left, top - 132, 10, ACCENT)
        kinds = tuple(kind for kind in (
            'infantry', 'fighter', 'mech', 'destroyer', 'cruiser', 'carrier',
            'dreadnought', 'flagship', 'warsun')
            if kind in window.movement.PRODUCIBLE_KINDS and
            production_allowed(player, kind))
        unit_width = (left_width - 8) / 2
        for index, kind in enumerate(kinds):
            row, col = divmod(index, 2)
            bx = left + col * (unit_width + 8)
            by = top - 187 - row * 55
            price = exploration.freelancers_cost(player, window.movement, kind)
            button(window, ('freelancers_unit', kind),
                   f'{UNIT_TYPES[kind]["name"].upper()} · {price}',
                   bx, by, unit_width, 44,
                   selected=self.freelancer_kind == kind, size=8)
            self.hits.append((('freelancer_kind', kind), bx, bx + unit_width,
                              by, by + 44))
        ground = self.freelancer_kind in ('infantry', 'mech')
        destinations = tuple(planet for planet in tile.planets
                             if tile.planet_owners.get(planet.planet_id) == player.faction and
                             not exploration.planet_is_blocked(planet.planet_id) and
                             planet.planet_id not in exploration.destroyed_planets)
        floating_factory = (player.faction == 'saar' and any(
            unit.owner == player.faction and unit.kind == 'spacedock' and
            unit.location.region.value == 'space' for unit in tile.units))
        if ground and destinations and self.freelancer_planet not in {
                planet.planet_id for planet in destinations} and not (
                    floating_factory and self.freelancer_planet is None):
            self.freelancer_planet = destinations[0].planet_id
        elif ground and not destinations:
            self.freelancer_planet = None
        if ground:
            window.text('freelancers_destination_title', 'PLACE ON',
                        left, bottom + 133, 10, ACCENT)
            places = [(planet.planet_id, planet.name) for planet in destinations[:3]]
            if floating_factory:
                places.append((None, 'SPACE'))
            columns = max(3, len(places))
            for index, (planet_id, label) in enumerate(places):
                bx = left + index * ((left_width + 6) / columns)
                by = bottom + 87
                bw = (left_width - (columns - 1) * 6) / columns
                button(window, ('freelancers_planet', planet_id), label,
                       bx, by, bw, 34,
                       selected=self.freelancer_planet == planet_id, size=8)
                self.hits.append((('freelancer_planet', planet_id),
                                  bx, bx + bw, by, by + 34))
            if not destinations and not floating_factory:
                window.text('freelancers_no_planet', 'No controlled planet in this system.',
                            left, bottom + 99, 9, MUTED, left_width)

        window.text('freelancers_payment_title', 'PAY WITH READY PLANETS',
                    right, top - 132, 10, ACCENT)
        ready = [card for card in player.planets if not card.exhausted]
        ready_ids = {card.planet.planet_id for card in ready}
        self.freelancer_resource_planets.intersection_update(ready_ids)
        self.freelancer_influence_planets.intersection_update(ready_ids)
        self.freelancer_trade_goods = min(self.freelancer_trade_goods,
                                          player.trade_goods)
        pages = max(1, (len(ready) + 7) // 8)
        self.freelancer_payment_page = min(self.freelancer_payment_page, pages - 1)
        for index, card in enumerate(ready[self.freelancer_payment_page * 8:
                                           self.freelancer_payment_page * 8 + 8]):
            row_top = top - 153 - index * 45
            planet = card.planet
            surface(right, right + right_width, row_top - 38, row_top, CARD, BORDER)
            window.text(('freelancer_pay_name', planet.planet_id), planet.name,
                        right + 9, row_top - 22, 9, INK, right_width - 195)
            for mode, value, offset in (
                    ('resources', window.movement.planet_resources(planet), 146),
                    ('influence', window.movement.planet_influence(planet), 73)):
                bx, by = right + right_width - offset, row_top - 32
                selected = planet.planet_id in (self.freelancer_resource_planets
                                                if mode == 'resources'
                                                else self.freelancer_influence_planets)
                button(window, ('freelancer_payment', mode, planet.planet_id),
                       f'{"R" if mode == "resources" else "I"} {value}',
                       bx, by, 65, 26, selected=selected, size=8)
                self.hits.append((('freelancer_payment', mode, planet.planet_id),
                                  bx, bx + 65, by, by + 26))
        if pages > 1:
            by = bottom + 135
            for direction, label, bx in ((-1, 'PREV', right),
                                         (1, 'NEXT', right + 75)):
                button(window, ('freelancer_page', direction), label,
                       bx, by, 68, 27, size=8)
                self.hits.append((('freelancer_page', direction),
                                  bx, bx + 68, by, by + 27))
            window.text('freelancer_payment_page',
                        f'{self.freelancer_payment_page + 1}/{pages}',
                        right + 155, by + 8, 9, MUTED)
        paid = (sum(window.movement.planet_resources(card.planet)
                    for card in player.planets
                    if card.planet.planet_id in self.freelancer_resource_planets) +
                sum(window.movement.planet_influence(card.planet)
                    for card in player.planets
                    if card.planet.planet_id in self.freelancer_influence_planets) +
                self.freelancer_trade_goods)
        cost = (exploration.freelancers_cost(player, window.movement,
                                             self.freelancer_kind)
                if self.freelancer_kind else 0)
        window.text('freelancer_payment_total',
                    f'PAYMENT {paid}/{cost}   ·   TRADE GOODS {self.freelancer_trade_goods}/{player.trade_goods}',
                    right, bottom + 104, 10, GOLD, right_width)
        for direction, label, bx in ((-1, '−', right), (1, '+', right + 40)):
            button(window, ('freelancer_goods', direction), label,
                   bx, bottom + 65, 33, 30, size=10)
            self.hits.append((('freelancer_goods', direction),
                              bx, bx + 33, bottom + 65, bottom + 95))
        can_produce = bool(self.freelancer_kind and paid >= cost and
                           (not ground or self.freelancer_planet in
                            {planet.planet_id for planet in destinations} or
                            (floating_factory and self.freelancer_planet is None)))
        bx, by = x + width - 205, bottom + 24
        button(window, 'freelancer_produce', 'PRODUCE UNIT', bx, by, 181, 36,
               primary=True, enabled=can_produce, size=9)
        if can_produce:
            self.hits.append((('freelancer_produce',), bx, bx + 181, by, by + 36))
        bx = x + 24
        button(window, 'freelancer_decline', 'DECLINE', bx, bottom + 24, 108, 36, size=9)
        self.hits.append((('freelancer_decline',), bx, bx + 108,
                          bottom + 24, bottom + 60))
        if window.movement_error:
            window.text('freelancer_error', window.movement_error,
                        right + 83, bottom + 74, 9, GOLD, right_width - 85)

    def _card_surface(self, window, card, x, y, width, height, *, key):
        color = TRAIT_COLORS.get(card.trait, ACCENT)
        surface(x, x + width, y, y + height, CARD, color)
        arcade.draw_lrbt_rectangle_filled(x, x + width, y + height - 5,
                                          y + height, color)
        window.text((key, 'trait'), f'{card.trait.upper()} · {card.source.upper()}',
                    x + 18, y + height - 31, 9, color, width - 36)
        window.text((key, 'title'), card.name, x + 18, y + height - 62,
                    16, INK, width - 36)
        self._lines(window, (key, 'text'), card.text, x + 18, y + height - 94,
                    width - 36, size=10, line_height=18,
                    max_lines=max(4, int((height - 126) / 18)))

    def _draw_pending(self, window, exploration):
        pending = exploration.pending
        x, bottom, width, height = self._window_rect(window, 880, 590)
        self._frame(window, x, bottom, width, height, 'PLANET EXPLORATION', close=False)
        top = bottom + height
        if pending.offered_aliases:
            window.text('exploration_choose_card',
                        'Distant Suns · Choose one card to resolve. Discard the other.',
                        x + 24, top - 108, 11, MUTED, width - 48)
            card_width = (width - 62) / 2
            for index, alias in enumerate(pending.offered_aliases):
                card = exploration.catalog[alias]
                cx = x + 24 + index * (card_width + 14)
                cy = bottom + 101
                self._card_surface(window, card, cx, cy, card_width, height - 230,
                                   key=('exploration_offer', index))
                by = bottom + 44
                button(window, ('exploration_choose', index), 'CHOOSE THIS CARD',
                       cx, by, card_width, 38, primary=True, size=10)
                self.hits.append((('resolve', alias), cx, cx + card_width, by, by + 38))
            return
        card = exploration.catalog[pending.alias]
        self._card_surface(window, card, x + 24, bottom + 163, width - 48,
                           height - 285, key='exploration_pending_card')
        window.text('exploration_choose_effect', 'CHOOSE AN EFFECT', x + 24,
                    bottom + 132, 10, ACCENT)
        options = pending.choices
        if not options:
            return
        columns = 2 if len(options) > 2 else len(options)
        cell_width = (width - 48 - 12 * (columns - 1)) / columns
        for index, option in enumerate(options):
            row, column = divmod(index, columns)
            bx = x + 24 + column * (cell_width + 12)
            by = bottom + 77 - row * 45
            button(window, ('exploration_option', index),
                   CHOICE_LABELS.get(option, option.replace('_', ' ').upper()),
                   bx, by, cell_width, 36, primary=option != 'decline', size=9)
            self.hits.append((('resolve', option), bx, bx + cell_width,
                              by, by + 36))

    def _draw_result(self, window):
        card = self.result.card
        x, bottom, width, height = self._window_rect(window, 740, 510)
        self._frame(window, x, bottom, width, height, 'EXPLORATION RESULT')
        self._card_surface(window, card, x + 24, bottom + 96, width - 48,
                           height - 205, key='exploration_result_card')
        events = self.result.events
        if events:
            summary = ', '.join(event['type'].replace('_', ' ') for event in events)
            window.text('exploration_result_effects', summary, x + 24,
                        bottom + 59, 10, GOLD, width - 48)

    def _draw_inventory(self, window, player, exploration):
        x, bottom, width, height = self._window_rect(window, 940, 690)
        self._frame(window, x, bottom, width, height,
                    f'{player.faction.upper()} · DISCOVERIES')
        top = bottom + height
        left = x + 24
        left_width = min(300, width * .36)
        right = left + left_width + 22
        right_width = x + width - right - 24
        window.text('exploration_fragments_title', 'RELIC FRAGMENTS', left,
                    top - 111, 12, ACCENT)
        window.text('exploration_fragments_hint',
                    'Combine matching fragments. Unknown fragments are wild.',
                    left, top - 133, 9, MUTED, left_width)
        counts = {trait: sum(exploration.catalog[alias].trait == trait
                             for alias in exploration.fragments[player.faction])
                  for trait in FRAGMENT_TYPES}
        for index, trait in enumerate(FRAGMENT_TYPES):
            row_top = top - 159 - index * 53
            surface(left, left + left_width, row_top - 44, row_top, CARD)
            self._image(RESOURCES / FRAGMENT_IMAGES[trait], left + 19,
                        row_top - 21, 29, 29)
            window.text(('exploration_fragment_name', trait), trait.title(),
                        left + 40, row_top - 18, 10, TRAIT_COLORS[trait])
            window.text(('exploration_fragment_count', trait), str(counts[trait]),
                        left + left_width - (86 if player.faction == 'naaz' else 31),
                        row_top - 18, 12, INK)
            if player.faction == 'naaz' and counts[trait]:
                bx, by = left + left_width - 67, row_top - 37
                button(window, ('fabrication_trait', trait), 'TOKEN', bx, by,
                       59, 28, selected=self.fabrication_trait == trait, size=8)
                self.hits.append((('fabrication_trait', trait), bx, bx + 59,
                                  by, by + 28))

        sets = self.available_fragment_sets(exploration, player.faction)
        required = 2 if player.faction == 'naaz' else 3
        window.text('exploration_assemble_hint',
                    f'ASSEMBLE A RELIC · {required} FRAGMENTS',
                    left, top - 400, 10, GOLD)
        if not sets:
            window.text('exploration_assemble_empty',
                        'Explore planets to collect enough matching fragments.',
                        left, top - 430, 9, MUTED, left_width)
        else:
            for index, (trait, aliases, amount) in enumerate(sets):
                by = top - 441 - index * 42
                if by < bottom + (112 if self.fabrication_trait else 24):
                    break
                button(window, ('exploration_assemble', trait),
                       f'ASSEMBLE · {trait.upper()}', left, by,
                       left_width, 35, primary=True, size=9)
                self.hits.append((('assemble_relic', aliases, amount),
                                  left, left + left_width, by, by + 35))
        if player.faction == 'naaz' and self.fabrication_trait:
            alias = next((alias for alias in exploration.fragments[player.faction]
                          if exploration.catalog[alias].trait == self.fabrication_trait), None)
            if alias is not None:
                window.text('fabrication_token_heading',
                            f'FABRICATE TOKEN · {self.fabrication_trait.upper()} FRAGMENT',
                            left, bottom + 81, 9, GOLD, left_width)
                pools = ('tactical', 'fleet', 'strategic')
                gap = 6
                cell = (left_width - 2 * gap) / 3
                for index, pool in enumerate(pools):
                    bx, by = left + index * (cell + gap), bottom + 32
                    button(window, ('fabricate_token', pool), pool.upper(),
                           bx, by, cell, 34, primary=True, size=8)
                    self.hits.append((('fabricate_token', alias, pool),
                                      bx, bx + cell, by, by + 34))

        window.text('exploration_relics_title', 'RELICS', right,
                    top - 111, 12, ACCENT)
        window.text('exploration_relics_hint',
                    'Readied relics return at the start of the next round.',
                    right, top - 133, 9, MUTED, right_width)
        viewport_top = top - 160
        viewport_bottom = bottom + 28
        aliases = exploration.relics.owned[player.faction]
        row_height = 91
        self.scroll_max = max(0, len(aliases) * row_height -
                              (viewport_top - viewport_bottom))
        self.scroll = min(self.scroll, self.scroll_max)
        old_scissor = window.ctx.scissor
        window.ctx.scissor = (int(right), int(viewport_bottom),
                              int(right_width), int(viewport_top - viewport_bottom))
        try:
            for index, alias in enumerate(aliases):
                row_top = viewport_top - index * row_height + self.scroll
                row_bottom = row_top - 82
                if row_top < viewport_bottom or row_bottom > viewport_top:
                    continue
                card = exploration.relics.catalog[alias]
                ready = exploration.relics.is_ready(player.faction, alias)
                surface(right, right + right_width, row_bottom, row_top,
                        SELECTED if ready else CARD)
                if card.image_path:
                    self._image(card.image_path, right + 33, row_bottom + 41,
                                52, 71)
                detail_x = right + 68
                window.text(('exploration_relic_name', alias), card.name,
                            detail_x, row_top - 21, 11, INK, right_width - 156)
                window.text(('exploration_relic_status', alias),
                            'READY' if ready else 'EXHAUSTED',
                            detail_x, row_top - 39, 8, ACCENT if ready else MUTED)
                self._lines(window, ('exploration_relic_text', alias), card.text,
                            detail_x, row_top - 57, right_width - 84,
                            size=8, line_height=12, max_lines=2, color=MUTED)
                if alias in ACTION_RELICS and ready:
                    bx = right + right_width - 91
                    by = row_bottom + 25
                    button(window, ('exploration_use_relic', alias), 'USE',
                           bx, by, 74, 30, primary=True, size=9)
                    self.hits.append((('relic_action', alias), bx, bx + 74,
                                      max(by, viewport_bottom), min(by + 30, viewport_top)))
        finally:
            window.ctx.scissor = old_scissor
        if not aliases:
            window.text('exploration_no_relics',
                        'Combine fragments or discover a relic to fill this space.',
                        right + 12, viewport_top - 38, 10, MUTED,
                        right_width - 24)
