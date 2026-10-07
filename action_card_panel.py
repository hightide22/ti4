"""Compact hand view with timing-aware playable card highlights."""
import arcade

from action_cards import ACTION_CARD_DEFS
from ui_theme import (ACCENT, BORDER, CARD, GOLD, INK, MUTED, SELECTED,
                      button, modal)


class ActionCardPanel:
    def __init__(self):
        self.open = False
        self.hits = []
        self.bounds = None
        self.player_faction = None

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def draw(self, window):
        self.hits.clear()
        if not self.open:
            return
        session = window.movement.session
        participants = ([p for p in window.player_panel.players if p.faction in session.combat_factions]
                        if session and session.stage in ('space_combat', 'ground_combat', 'combat_end', 'retreat_selection')
                        else [session.player] if session else [])
        if not participants:
            player = window.strategy.player if window.strategy.session else window.turn_order.active_player
            participants = [player] if player else []
        if not participants:
            return
        if not any(p.faction == self.player_faction for p in participants):
            self.player_faction = participants[0].faction
        player = next(p for p in participants if p.faction == self.player_faction)
        hand = player.action_cards
        width = min(650, window.width - 48)
        row_height = 90
        visible_rows = max(1, (len(hand) + 1) // 2)
        has_tabs = len(participants) > 1
        height = min(window.height - 48, 96 + visible_rows * row_height + (36 if has_tabs else 0))
        left, bottom = (window.width - width) / 2, (window.height - height) / 2
        top = bottom + height
        self.bounds = (left, left + width, bottom, top)
        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (3, 7, 14, 105))
        modal(left, left + width, bottom, top)
        window.text('action_cards_title', f'{player.faction.upper()} · ACTION CARDS',
                    left + 22, top - 31, 16, ACCENT)
        window.text('action_cards_help', 'Playable cards glow when their timing window is open.',
                    left + 22, top - 57, 10, MUTED, width - 44)
        close_x, close_y = left + width - 100, top - 49
        button(window, 'action_cards_close', 'CLOSE', close_x, close_y, 78, 28, size=9)
        self.hits.append((('close',), close_x, close_x + 78, close_y, close_y + 28))
        cards_top = top - 86
        if has_tabs:
            tab_width = min(142, (width - 54) / len(participants))
            for index, participant in enumerate(participants):
                tx, ty = left + 22 + index * (tab_width + 8), top - 99
                selected = participant is player
                arcade.draw_lrbt_rectangle_filled(tx, tx + tab_width, ty - 27, ty,
                                                  SELECTED if selected else CARD)
                arcade.draw_lrbt_rectangle_outline(tx, tx + tab_width, ty - 27, ty,
                                                   ACCENT if selected else BORDER, 1)
                window.text(('action_card_player', participant.faction), participant.faction.upper(),
                            tx + 8, ty - 18, 8, ACCENT if selected else MUTED, tab_width - 16)
                self.hits.append((('player', participant.faction), tx, tx + tab_width, ty - 27, ty))
            cards_top = top - 123
        if not hand:
            window.text('action_cards_empty', 'No cards in hand. Politics draws two; each status phase draws one.',
                        left + 22, cards_top - 28, 11, MUTED, width - 44)
            return
        gap = 10
        cell_width = (width - 44 - gap) / 2
        y = cards_top
        for index, alias in enumerate(hand):
            row, col = divmod(index, 2)
            x = left + 22 + col * (cell_width + gap)
            card_y = y - row * row_height
            card = ACTION_CARD_DEFS.get(alias, {'name': alias, 'window': 'Unknown timing',
                                                'effect': 'This card is not implemented yet.'})
            playable = bool(window.action_cards.can_play(player.faction, alias, session))
            fill = SELECTED if playable else CARD
            arcade.draw_lrbt_rectangle_filled(x, x + cell_width, card_y - 78, card_y, fill)
            arcade.draw_lrbt_rectangle_outline(x, x + cell_width, card_y - 78, card_y,
                                               ACCENT if playable else BORDER, 2 if playable else 1)
            status = 'PLAY NOW' if playable else 'WAIT'
            window.text(('action_card_status', index), status, x + cell_width - 73,
                        card_y - 16, 8, GOLD if playable else MUTED)
            window.text(('action_card_name', index), card.get('name', alias), x + 10,
                        card_y - 18, 11, INK, cell_width - 100)
            window.text(('action_card_window', index), card['window'], x + 10,
                        card_y - 39, 8, ACCENT if playable else MUTED, cell_width - 20)
            window.text(('action_card_effect', index), card['effect'], x + 10,
                        card_y - 59, 8, INK if playable else MUTED, cell_width - 20)
            if playable:
                self.hits.append((('play', index), x, x + cell_width, card_y - 78, card_y))
