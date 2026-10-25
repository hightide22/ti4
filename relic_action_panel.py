"""Interactive targeting and payment modal for action relics.

This panel only collects choices.  The game controller validates timing and
applies the semantic ``('use_relic', alias, ...)`` command returned by
``begin`` or ``handle``.
"""
from __future__ import annotations

from collections import Counter

import arcade

from ui_theme import ACCENT, BORDER, CARD, GOLD, INK, MUTED, SELECTED, button, modal, surface
from units import Region, unit_profile
from unit_supply import can_place_structure


ACTION_RELICS = frozenset({'codex', 'dynamiscore', 'nanoforge',
                           'stellarconverter', 'titanprototype'})


class RelicActionPanel:
    """Choose relic targets without mutating game state.

    Call ``begin`` when a player clicks USE.  The dynamis core returns its
    command immediately.  Otherwise show the modal while ``open`` is true,
    pass ``hit_test`` results to ``handle``, and process the returned command.
    """

    def __init__(self):
        self.open = False
        self.alias: str | None = None
        self.owner_faction: str | None = None
        self.recipient_faction: str | None = None
        self.planet_id: str | None = None
        self.kind: str | None = None
        self.payment_planets: set[str] = set()
        self.trade_goods = 0
        self.selected_cards: set[int] = set()
        self.required_discard_count = 0
        self.scroll: dict[str, int] = {}
        self.scroll_max: dict[str, int] = {}
        self.scroll_focus = 'main'
        self.offset = [0.0, 0.0]
        self.bounds = None
        self.hits: list[tuple[tuple, float, float, float, float]] = []
        self.hovered_position = None

    def begin(self, alias: str, player):
        if alias not in ACTION_RELICS:
            raise ValueError('This relic has no action window.')
        self.close()
        if alias == 'dynamiscore':
            return ('use_relic', alias)
        self.open = True
        self.alias = alias
        self.owner_faction = player.faction
        self.recipient_faction = player.faction
        return None

    def begin_discard(self, player) -> bool:
        """Open an unclosable choice when a Codex draw exceeds the hand limit.

        Returns ``True`` if a discard choice is required.  Yssaril has no
        action-card hand limit.  The caller applies the returned command after
        validating that the hand has not changed in the meantime.
        """
        excess = 0 if player.faction == 'yssaril' else max(0, len(player.action_cards) - 7)
        if not excess:
            return False
        self.close()
        self.open = True
        self.alias = 'hand_discard'
        self.owner_faction = player.faction
        self.required_discard_count = excess
        return True

    def close(self):
        self.open = False
        self.alias = self.owner_faction = self.recipient_faction = None
        self.planet_id = self.kind = None
        self.payment_planets.clear()
        self.trade_goods = 0
        self.selected_cards.clear()
        self.required_discard_count = 0
        self.scroll.clear()
        self.scroll_max.clear()
        self.scroll_focus = 'main'
        self.bounds = None
        self.hits.clear()
        self.hovered_position = None

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def drag_header(self, x, y):
        if self.bounds is None:
            return False
        left, right, bottom, top = self.bounds
        return left <= x <= right and top - 58 <= y <= top - 5

    def move(self, dx, dy):
        self.offset[0] += dx
        self.offset[1] += dy

    def scroll_by(self, delta):
        key = self.scroll_focus
        self.scroll[key] = max(0, min(self.scroll_max.get(key, 0),
                                      self.scroll.get(key, 0) + int(delta) * 43))

    def _recipient(self, exploration):
        return exploration.players[self.recipient_faction]

    @staticmethod
    def _planet_matches(exploration, player, alias):
        for tile in exploration.board.values():
            for planet in tile.planets:
                planet_id = planet.planet_id
                if planet_id in exploration.destroyed_planets:
                    continue
                if alias == 'nanoforge':
                    allowed = (tile.planet_owners.get(planet_id) == player.faction and
                               not planet.faction_homeworld and
                               not exploration.planet_is_legendary(planet))
                elif alias == 'stellarconverter':
                    allowed = (not planet.faction_homeworld and
                               planet_id != 'mecatolrex' and
                               not exploration.planet_is_legendary(planet) and
                               tile.planet_owners.get(planet_id) != player.faction and
                               any(unit.owner == player.faction and
                                   unit.location.region == Region.SPACE and
                                   unit_profile(unit).get('bombardHitsOn')
                                   for neighbor in exploration.board.neighbors(tile.position)
                                   for unit in neighbor.units))
                else:
                    allowed = (tile.planet_owners.get(planet_id) == player.faction and
                               not exploration.planet_is_blocked(planet_id))
                if allowed:
                    yield tile, planet

    @staticmethod
    def _structure_allowed(tile, planet_id, player, kind, exploration):
        if exploration.planet_is_blocked(planet_id):
            return False
        total = sum(unit.owner == player.faction and unit.kind == kind
                    for system in exploration.board.values() for unit in system.units)
        if total >= {'pds': 6, 'spacedock': 3}[kind]:
            return False
        if kind == 'spacedock' and player.faction == 'saar':
            return True
        return can_place_structure(tile, planet_id, player.faction, kind)

    def _targets(self, exploration):
        player = (exploration.players[self.owner_faction]
                  if self.alias != 'titanprototype' else self._recipient(exploration))
        if self.alias == 'titanprototype':
            matches = self._planet_matches(exploration, player, 'structure')
            return [(tile, planet) for tile, planet in matches
                    if any(self._structure_allowed(tile, planet.planet_id, player,
                                                   kind, exploration)
                           for kind in ('pds', 'spacedock'))]
        return list(self._planet_matches(exploration, player, self.alias))

    def _selected_target(self, exploration):
        return next(((tile, planet) for tile, planet in self._targets(exploration)
                     if planet.planet_id == self.planet_id), None)

    def _payment_value(self, exploration):
        recipient = self._recipient(exploration)
        ready = {card.planet.planet_id: card for card in recipient.planets
                 if not card.exhausted}
        return self.trade_goods + sum(exploration.planet_values(ready[planet_id].planet)[0]
                                      for planet_id in self.payment_planets
                                      if planet_id in ready)

    def _payment_cards(self, exploration):
        return [card for card in self._recipient(exploration).planets
                if not card.exhausted]

    def _sync_hand_limit(self, player):
        required = 0 if player.faction == 'yssaril' else max(0, len(player.action_cards) - 7)
        if required != self.required_discard_count:
            self.required_discard_count = required
            self.selected_cards.clear()
        if required == 0:
            self.close()

    def handle(self, action, exploration, action_cards):
        """Consume one UI action and return a complete semantic command, if any."""
        if not self.open or action is None:
            return None
        if self.alias == 'hand_discard':
            self._sync_hand_limit(exploration.players[self.owner_faction])
            if not self.open:
                return None
        kind = action[0]
        if kind == 'close':
            if self.alias != 'hand_discard':
                self.close()
            return None
        if kind == 'card':
            index = action[1]
            cards = (exploration.players[self.owner_faction].action_cards
                     if self.alias == 'hand_discard' else action_cards.discard)
            maximum = (self.required_discard_count if self.alias == 'hand_discard' else 3)
            if 0 <= index < len(cards):
                if index in self.selected_cards:
                    self.selected_cards.remove(index)
                elif len(self.selected_cards) < maximum:
                    self.selected_cards.add(index)
            return None
        if kind == 'recipient':
            if action[1] in exploration.players:
                self.recipient_faction = action[1]
                self.planet_id = self.kind = None
                self.payment_planets.clear()
                self.trade_goods = 0
                self.scroll.clear()
            return None
        if kind == 'planet':
            if action[1] in {planet.planet_id for _, planet in self._targets(exploration)}:
                self.planet_id = action[1]
                self.kind = None
            return None
        if kind == 'structure':
            target = self._selected_target(exploration)
            if target is not None and action[1] in ('pds', 'spacedock') and \
                    self._structure_allowed(target[0], self.planet_id,
                                            self._recipient(exploration), action[1],
                                            exploration):
                self.kind = action[1]
            return None
        if kind == 'pay_planet':
            planet_id = action[1]
            if planet_id in {card.planet.planet_id for card in self._payment_cards(exploration)}:
                if planet_id in self.payment_planets:
                    self.payment_planets.remove(planet_id)
                else:
                    self.payment_planets.add(planet_id)
            return None
        if kind == 'trade_good':
            self.trade_goods = max(0, min(self._recipient(exploration).trade_goods,
                                         self.trade_goods + action[1]))
            return None
        if kind == 'decline':
            command = ('use_relic', 'titanprototype', self.recipient_faction,
                       None, None, (), 0)
            self.close()
            return command
        if kind != 'confirm':
            return None
        alias = self.alias
        if alias == 'hand_discard':
            player = exploration.players[self.owner_faction]
            required = max(0, len(player.action_cards) - 7)
            if (player.faction == 'yssaril' or required != self.required_discard_count or
                    len(self.selected_cards) != required or
                    any(index >= len(player.action_cards) for index in self.selected_cards)):
                return None
            selected = tuple((index, player.action_cards[index])
                             for index in sorted(self.selected_cards))
            command = ('discard_to_limit', player.faction, selected)
        elif alias == 'codex':
            aliases = tuple(action_cards.discard[index]
                            for index in sorted(self.selected_cards)
                            if index < len(action_cards.discard))
            if not aliases:
                return None
            command = ('use_relic', alias, aliases)
        elif alias in ('nanoforge', 'stellarconverter'):
            if self._selected_target(exploration) is None:
                return None
            command = ('use_relic', alias, self.planet_id)
        elif alias == 'titanprototype':
            target = self._selected_target(exploration)
            if target is None or self.kind is None or self._payment_value(exploration) < 3:
                return None
            if not self._structure_allowed(target[0], self.planet_id,
                                           self._recipient(exploration), self.kind,
                                           exploration):
                return None
            command = ('use_relic', alias, self.recipient_faction, self.planet_id,
                       self.kind, tuple(sorted(self.payment_planets)), self.trade_goods)
        else:
            return None
        self.close()
        return command

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

    def _hit(self, action, x, y, width, height):
        self.hits.append((action, x, x + width, y, y + height))

    def _row(self, window, action, label, detail, x, y, width, *, selected=False):
        surface(x, x + width, y, y + 44, SELECTED if selected else CARD,
                ACCENT if selected else BORDER)
        window.text((action, 'label'), label, x + 12, y + 24, 10,
                    GOLD if selected else INK, width - 24)
        if detail:
            window.text((action, 'detail'), detail, x + 12, y + 7, 8, MUTED,
                        width - 24)
        self._hit(action, x, y, width, 44)

    def _scrolling_rows(self, window, rows, x, top, bottom, width, *, key='main'):
        row_step = 50
        viewport_height = max(0, top - bottom)
        self.scroll_max[key] = max(0, len(rows) * row_step - viewport_height)
        self.scroll[key] = min(self.scroll.get(key, 0), self.scroll_max[key])
        mx, my = window.mouse_position
        if x <= mx <= x + width and bottom <= my <= top:
            self.scroll_focus = key
        old_scissor = window.ctx.scissor
        window.ctx.scissor = (int(x), int(bottom), int(width), int(viewport_height))
        try:
            for index, (action, label, detail, selected, position) in enumerate(rows):
                y = top - 44 - index * row_step + self.scroll[key]
                if y < bottom or y + 44 > top:
                    continue
                self._row(window, action, label, detail, x, y, width,
                          selected=selected)
                if x <= mx <= x + width and y <= my <= y + 44:
                    self.hovered_position = position
        finally:
            window.ctx.scissor = old_scissor

    def draw(self, window, player, exploration, action_cards):
        self.hits.clear()
        self.hovered_position = None
        if not self.open:
            self.bounds = None
            return
        if self.alias == 'hand_discard':
            self._sync_hand_limit(player)
            if not self.open:
                return
        x, bottom, width, height = self._window_rect(window, 920, 670)
        top = bottom + height
        arcade.draw_lrbt_rectangle_filled(0, window.width,
                                          window.player_panel.HEIGHT, window.height,
                                          (3, 7, 14, 190))
        modal(x, x + width, bottom, top)
        title = ('ACTION CARD HAND LIMIT' if self.alias == 'hand_discard' else
                 exploration.relics.catalog[self.alias].name.upper())
        window.text('relic_action_title', title, x + 24,
                    top - 36, 17, ACCENT, width - 230)
        window.text('relic_action_drag', 'DRAG HEADER TO MOVE', x + width - 204,
                    top - 31, 8, MUTED)
        if self.alias != 'hand_discard':
            button(window, 'relic_action_close', 'CLOSE', x + width - 105,
                   top - 66, 80, 28, size=9)
            self._hit(('close',), x + width - 105, top - 66, 80, 28)
        if self.alias == 'hand_discard':
            self._draw_hand_discard(window, player, action_cards, x, bottom, width, height)
        elif self.alias == 'codex':
            self._draw_codex(window, action_cards, x, bottom, width, height)
        elif self.alias == 'titanprototype':
            self._draw_titan(window, exploration, x, bottom, width, height)
        else:
            self._draw_planet_action(window, exploration, x, bottom, width, height)

    def _draw_codex(self, window, action_cards, x, bottom, width, height):
        top = bottom + height
        window.text('codex_instructions',
                    'Recover up to 3 action cards from the discard pile, then purge The Codex.',
                    x + 24, top - 112, 10, MUTED, width - 48)
        counts = Counter()
        rows = []
        for index, alias in enumerate(action_cards.discard):
            counts[alias] += 1
            name = action_cards.name_for(alias)
            detail = f'{alias} · copy {counts[alias]}' if counts[alias] > 1 else alias
            rows.append((('card', index), name, detail,
                         index in self.selected_cards, None))
        if rows:
            self._scrolling_rows(window, rows, x + 24, top - 147,
                                 bottom + 97, width - 48)
        else:
            window.text('codex_empty', 'The action-card discard pile is empty.',
                        x + 24, top - 178, 11, MUTED)
        count = len(self.selected_cards)
        window.text('codex_count', f'SELECTED {count} / 3', x + 24,
                    bottom + 55, 11, GOLD)
        button(window, 'codex_confirm', 'RECOVER CARDS', x + width - 206,
               bottom + 35, 182, 36, primary=True, enabled=count > 0, size=10)
        if count:
            self._hit(('confirm',), x + width - 206, bottom + 35, 182, 36)

    def _draw_hand_discard(self, window, player, action_cards,
                           x, bottom, width, height):
        top = bottom + height
        required = self.required_discard_count
        window.text('hand_limit_instructions',
                    f'The Codex raised your hand above 7 cards. Choose exactly {required} '
                    f'card{"s" if required != 1 else ""} to discard.',
                    x + 24, top - 112, 10, MUTED, width - 48)
        rows = [(('card', index), action_cards.name_for(alias), alias,
                 index in self.selected_cards, None)
                for index, alias in enumerate(player.action_cards)]
        self._scrolling_rows(window, rows, x + 24, top - 147,
                             bottom + 97, width - 48)
        count = len(self.selected_cards)
        window.text('hand_limit_count', f'SELECTED {count} / {required}',
                    x + 24, bottom + 55, 11, GOLD)
        ready = count == required and len(player.action_cards) - required == 7
        button(window, 'hand_limit_confirm', 'DISCARD SELECTED',
               x + width - 206, bottom + 35, 182, 36,
               primary=True, enabled=ready, size=10)
        if ready:
            self._hit(('confirm',), x + width - 206, bottom + 35, 182, 36)

    def _draw_planet_action(self, window, exploration, x, bottom, width, height):
        top = bottom + height
        if self.alias == 'nanoforge':
            guidance = ('Attach Nano-Forge to a controlled non-home, non-legendary '
                        'planet. This raises both values by 2.')
            confirm = 'ATTACH NANO-FORGE'
        else:
            guidance = ('Destroy another player’s eligible planet adjacent to '
                        'one of your ships with Bombardment.')
            confirm = 'DESTROY PLANET'
        window.text('relic_planet_hint', guidance, x + 24, top - 110,
                    10, MUTED, width - 48)
        rows = [(('planet', planet.planet_id), planet.name,
                 f'{tile.name} · {exploration.planet_values(planet)[0]} resources / '
                 f'{exploration.planet_values(planet)[1]} influence',
                 planet.planet_id == self.planet_id, tile.position)
                for tile, planet in self._targets(exploration)]
        if rows:
            self._scrolling_rows(window, rows, x + 24, top - 147,
                                 bottom + 98, width - 48)
        else:
            window.text('relic_no_target', 'No eligible planets are currently available.',
                        x + 24, top - 178, 11, MUTED)
        button(window, 'relic_confirm', confirm, x + width - 231,
               bottom + 34, 207, 37, primary=True,
               enabled=self._selected_target(exploration) is not None, size=9)
        if self._selected_target(exploration) is not None:
            self._hit(('confirm',), x + width - 231, bottom + 34, 207, 37)

    def _draw_titan(self, window, exploration, x, bottom, width, height):
        top = bottom + height
        recipient = self._recipient(exploration)
        window.text('titan_hint',
                    'Choose a player; they may place a structure for 3 resources or take 1 trade good.',
                    x + 24, top - 104, 10, MUTED,
                    width - 48)
        tab_width = min(125, (width - 48 - 8 * (len(exploration.players) - 1)) /
                        max(1, len(exploration.players)))
        for index, faction in enumerate(exploration.players):
            bx = x + 24 + index * (tab_width + 8)
            button(window, ('titan_player', faction), faction.upper(),
                   bx, top - 157, tab_width, 29,
                   selected=faction == self.recipient_faction, size=8)
            self._hit(('recipient', faction), bx, top - 157, tab_width, 29)
        left_width = (width - 60) * .49
        right_x = x + 36 + left_width
        right_width = x + width - right_x - 24
        window.text('titan_target_title', 'PLACE A STRUCTURE', x + 24,
                    top - 193, 11, ACCENT)
        rows = [(('planet', planet.planet_id), planet.name,
                 f'{tile.name} · {exploration.planet_values(planet)[0]} resources',
                 planet.planet_id == self.planet_id, tile.position)
                for tile, planet in self._targets(exploration)]
        if rows:
            self._scrolling_rows(window, rows, x + 24, top - 209,
                                 bottom + 162, left_width, key='targets')
        else:
            window.text('titan_no_target', 'No planet can receive a structure.',
                        x + 24, top - 233, 9, MUTED, left_width)
        target = self._selected_target(exploration)
        for index, kind in enumerate(('pds', 'spacedock')):
            enabled = target is not None and self._structure_allowed(
                target[0], self.planet_id, recipient, kind, exploration)
            bx = right_x + index * ((right_width + 8) / 2)
            bw = (right_width - 8) / 2
            button(window, ('titan_kind', kind), 'PDS' if kind == 'pds' else 'SPACE DOCK',
                   bx, top - 218, bw, 29, enabled=enabled,
                   selected=self.kind == kind, size=8)
            if enabled:
                self._hit(('structure', kind), bx, top - 218, bw, 29)
        window.text('titan_pay_title', 'PAY 3 RESOURCES', right_x,
                    top - 253, 11, GOLD)
        payment_rows = [(('pay_planet', card.planet.planet_id),
                         card.planet.name,
                         f'{exploration.planet_values(card.planet)[0]} resources',
                         card.planet.planet_id in self.payment_planets, None)
                        for card in self._payment_cards(exploration)]
        if payment_rows:
            self._scrolling_rows(window, payment_rows, right_x, top - 269,
                                 bottom + 173, right_width, key='payment')
        else:
            window.text('titan_no_payment', 'No ready planets.', right_x,
                        top - 290, 9, MUTED)
        value = self._payment_value(exploration)
        window.text('titan_trade_goods',
                    f'TRADE GOODS {self.trade_goods} / {recipient.trade_goods}',
                    right_x, bottom + 133, 10, INK)
        for index, (delta, label) in enumerate(((-1, '−'), (1, '+'))):
            bx = right_x + right_width - 78 + index * 40
            button(window, ('titan_tg', delta), label, bx, bottom + 120,
                   35, 32, enabled=(self.trade_goods > 0 if delta < 0 else
                                    self.trade_goods < recipient.trade_goods), size=12)
            self._hit(('trade_good', delta), bx, bottom + 120, 35, 32)
        window.text('titan_total', f'PAYMENT {value} / 3', x + 24,
                    bottom + 126, 11, GOLD)
        can_place = target is not None and self.kind is not None and value >= 3
        button(window, 'titan_place', 'PAY & PLACE', x + width - 328,
               bottom + 37, 148, 36, primary=True, enabled=can_place, size=9)
        if can_place:
            self._hit(('confirm',), x + width - 328, bottom + 37, 148, 36)
        button(window, 'titan_decline', 'GAIN 1 TRADE GOOD', x + width - 171,
               bottom + 37, 147, 36, size=8)
        self._hit(('decline',), x + width - 171, bottom + 37, 147, 36)
