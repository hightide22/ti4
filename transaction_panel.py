"""Compact center modal for negotiating one TI4 transaction."""
import json
from functools import lru_cache

import arcade

from board import RESOURCES
from player_panel import INK, MUTED, ACCENT, GOLD


@lru_cache(maxsize=1)
def action_card_names():
    cards = json.loads((RESOURCES / 'data/action_cards/action_cards.json').read_text(encoding='utf-8'))
    return {card['alias']: card['name'] for card in cards if card.get('source') == 'base'}


class TransactionPanel:
    def __init__(self):
        self.hits = []

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def _button(self, window, action, label, x, y, width, height=29, enabled=True):
        fill = (31, 78, 83) if enabled else (24, 35, 48)
        border = (77, 151, 151) if enabled else (48, 65, 82)
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height, fill)
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + height, border, 1)
        window.text(('trade_button', action), label, x + 8, y + max(7, (height - 10) / 2), 9,
                    INK if enabled else MUTED, width - 16)
        if enabled:
            self.hits.append((action, x, x + width, y, y + height))

    def draw(self, window, controller):
        self.hits.clear()
        s = controller.session
        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (3, 7, 14, 218))
        width = min(680, window.width - 48)
        height = min(640, window.height - 48)
        left, bottom = (window.width - width) / 2, (window.height - height) / 2
        top = bottom + height
        arcade.draw_lrbt_rectangle_filled(left, left + width, bottom, top, (13, 24, 39))
        arcade.draw_lrbt_rectangle_outline(left, left + width, bottom, top, (75, 138, 166), 2)
        window.text('trade_title', 'NEGOTIATE A TRANSACTION', left + 24, top - 34, 16, ACCENT)
        window.text('trade_subtitle', f'{s.initiator.faction.upper()} · Action phase trade',
                    left + 24, top - 58, 10, MUTED)
        self._button(window, ('cancel',), 'CANCEL', left + width - 108, top - 54, 82, 27)
        if s.partner is None:
            window.text('trade_partner_prompt', 'Choose a player to trade with:', left + 24,
                        top - 103, 11, INK)
            players = controller.eligible_partners(s.initiator)
            row_y = top - 145
            for index, player in enumerate(players):
                y = row_y - index * 43
                adjacent = controller.are_neighbors(s.initiator.faction, player.faction)
                tag = 'NEIGHBOR' if adjacent else 'HACAN · ANY DISTANCE'
                arcade.draw_lrbt_rectangle_filled(left + 24, left + width - 24, y, y + 34,
                                                   (21, 35, 52))
                window.player_panel.image(f'factions/{player.faction}.png', left + 45, y + 17, 25)
                window.text(('trade_partner', player.faction), player.name, left + 67, y + 21,
                            10, INK)
                window.text(('trade_distance', player.faction), tag, left + width - 175,
                            y + 21, 8, ACCENT if not adjacent else MUTED)
                self.hits.append((('partner', player.faction), left + 24, left + width - 24, y, y + 34))
            if not players:
                window.text('trade_no_partners', 'No eligible players. Neighbors may trade; Hacan can trade across the galaxy.',
                            left + 24, top - 112, 10, MUTED, width - 48)
            return

        partner = s.partner
        window.text('trade_parties', f'{s.initiator.faction.upper()}  ↔  {partner.faction.upper()}',
                    left + 24, top - 95, 13, GOLD)
        window.text('trade_help', 'Set what each player gives, then confirm the exchange.',
                    left + 24, top - 117, 9, MUTED)
        self._trade_row(window, 'Trade goods', 'give_trade_goods', s.give_trade_goods,
                        left + 24, top - 164)
        self._trade_row(window, 'Commodities', 'give_commodities', s.give_commodities,
                        left + 24, top - 202)
        self._trade_row(window, 'Trade goods', 'take_trade_goods', s.take_trade_goods,
                        left + 24, top - 252, sender=partner.faction)
        self._trade_row(window, 'Commodities', 'take_commodities', s.take_commodities,
                        left + 24, top - 290, sender=partner.faction)
        if 'hacan' in (s.initiator.faction, partner.faction):
            section_y = top - 333
            window.text('trade_arbiter_header', 'ARBITERS · ACTION CARDS', left + 24,
                        section_y, 9, ACCENT)
            window.text('trade_arbiter_help', 'Hacan may include action cards in this transaction.',
                        left + 24, section_y - 19, 8, MUTED)
            initiator_bottom = self._card_rows(
                window, s.initiator, s.give_action_cards, left + 24, section_y - 44,
                left + width - 24)
            self._card_rows(window, partner, s.take_action_cards, left + 24,
                            initiator_bottom - 38, left + width - 24)
        self._button(window, ('back',), 'CHANGE PLAYER', left + 24, bottom + 24, 120)
        self._button(window, ('confirm',), 'CONFIRM TRADE', left + width - 174,
                     bottom + 24, 150)

    def _trade_row(self, window, label, field, value, x, y, sender=None):
        s = window.transaction.session
        partner = s.partner
        who = sender or s.initiator.faction
        who_label = 'You give' if sender is None else f'{who.upper()} gives'
        window.text(('trade_row_label', field), f'{who_label} · {label}', x, y + 9, 10, INK)
        amount = next(player for player in (s.initiator, partner) if player.faction == who)
        currency = field[5:]
        minus_enabled = value > 0
        plus_enabled = getattr(amount, currency) > value
        self._button(window, ('change', field, -1), '−', x + 390, y, 35, 27, minus_enabled)
        window.text(('trade_amount', field), str(value), x + 439, y + 8, 11, GOLD)
        self._button(window, ('change', field, 1), '+', x + 468, y, 35, 27, plus_enabled)
        window.text(('trade_available', field), f'Available: {getattr(amount, currency)}',
                    x + 517, y + 9, 8, MUTED, 112)

    def _card_rows(self, window, player, selected, x, y, right_edge):
        label = 'You offer' if player is window.transaction.session.initiator else f'{player.faction.upper()} offers'
        window.text(('trade_card_side', player.faction), label, x, y + 6, 8, MUTED)
        cards = player.action_cards
        if not cards:
            window.text(('trade_no_cards', player.faction), 'No action cards in hand', x + 100,
                        y + 6, 8, MUTED)
            return y
        card_x = x + 102
        row_y = y
        for card_id in cards:
            name = action_card_names().get(card_id, card_id.replace('_', ' ').title())
            width = max(112, min(185, len(name) * 6 + 20))
            if card_x + width > right_edge:
                card_x = x + 102
                row_y -= 25
            is_selected = card_id in selected
            background = (61, 59, 39) if is_selected else (21, 35, 52)
            arcade.draw_lrbt_rectangle_filled(card_x, card_x + width,
                                               row_y - 1, row_y + 20, background)
            arcade.draw_lrbt_rectangle_outline(card_x, card_x + width,
                                                row_y - 1, row_y + 20,
                                                GOLD if is_selected else (65, 95, 119), 1)
            window.text(('trade_card', player.faction, card_id), name, card_x + 7,
                        row_y + 5, 8, INK, width - 14)
            self.hits.append((('toggle_card', player.faction, card_id), card_x, card_x + width,
                              row_y - 1, row_y + 20))
            card_x += width + 6
        return row_y
