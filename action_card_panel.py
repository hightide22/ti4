"""Overlapping, scan-based action-card hand with playable-card highlights."""
from pathlib import Path

import arcade

from action_cards import ACTION_CARD_DEFS
from ui_theme import (ACCENT, BORDER, CARD, GOLD, INK, MUTED, SELECTED,
                      button, modal)


ROOT = Path(__file__).resolve().parent
CARD_ART = ROOT / 'assets' / 'action_cards'
CARD_IMAGES = {
    'bunker': 'bunker.jpg',
    'courageous_to_the_end': 'courageous_to_the_end.jpg',
    'direct_hit': 'direct_hit.jpg',
    'disable': 'disable.jpg',
    'experimental_battlestation': 'experimental_battlestation.jpg',
    'fire_team': 'fire_team.jpg',
    'flank_speed': 'flank_speed.jpg',
    'in_the_silence_of_space': 'in_the_silence_of_space.jpg',
    'intercept': 'intercept.jpg',
    'maneuvering_jets': 'maneuvering_jets.jpg',
    'morale_boost': 'morale_boost.jpg',
    'salvage': 'salvage.jpg',
    'shields_holding': 'shields_holding.jpg',
    'skilled_retreat': 'skilled_retreat.jpg',
    'upgrade': 'upgrade.jpg',
    'war_effort': 'war_effort.jpg',
    'fighter_prototype': 'fighter_prototype.jpg',
    'emergency_repairs': 'emergency_repairs.jpg',
}
CARD_ASPECT = 500 / 760


class ActionCardPanel:
    def __init__(self):
        self.open = False
        self.hits = []
        self.card_bounds = []
        self.bounds = None
        self.player_faction = None
        self._textures = {}
        self.hovered_card_index = None

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def _texture(self, alias):
        filename = CARD_IMAGES.get(alias)
        if not filename:
            return None
        path = CARD_ART / filename
        if not path.is_file():
            return None
        if alias not in self._textures:
            self._textures[alias] = arcade.load_texture(path)
        return self._textures[alias]

    @staticmethod
    def _draw_card_frame(x, y, width, height, playable):
        if playable:
            arcade.draw_lrbt_rectangle_filled(x - 5, x + width + 5,
                                              y - 5, y + height + 5,
                                              (100, 207, 224, 62))
        arcade.draw_lrbt_rectangle_outline(x - 2, x + width + 2, y - 2,
                                           y + height + 2,
                                           ACCENT if playable else BORDER,
                                           3 if playable else 1)

    def _draw_card(self, window, alias, index, x, y, width, height, playable):
        texture = self._texture(alias)
        if texture:
            arcade.draw_texture_rect(texture, arcade.XYWH(x + width / 2, y + height / 2,
                                                           width, height))
        else:
            card = ACTION_CARD_DEFS.get(alias, {
                'name': alias, 'window': 'Unknown timing',
                'effect': 'This card is not implemented yet.'})
            arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height,
                                              SELECTED if playable else CARD)
            window.text(('action_card_name', index), card.get('name', alias),
                        x + 10, y + height - 24, 12, INK, width - 20)
            window.text(('action_card_window', index), card['window'],
                        x + 10, y + height - 52, 9, ACCENT if playable else MUTED,
                        width - 20)
            window.text(('action_card_effect', index), card['effect'],
                        x + 10, y + height - 82, 9, INK if playable else MUTED,
                        width - 20)

        self._draw_card_frame(x, y, width, height, playable)
        if playable:
            badge_width = min(72, width - 16)
            badge_left = x + (width - badge_width) / 2
            arcade.draw_lrbt_rectangle_filled(badge_left, badge_left + badge_width,
                                              y + 10, y + 32, (7, 12, 23, 230))
            window.text(('action_card_status', index), 'PLAY NOW', badge_left + 7,
                        y + 16, 8, GOLD, badge_width - 14)

    def draw(self, window):
        self.hits.clear()
        self.card_bounds.clear()
        self.hovered_card_index = None
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
        hand = player.action_cards[:7]
        has_tabs = len(participants) > 1

        width = min(940, window.width - 40)
        inner_width = width - 40
        card_width = min(147, inner_width / (1 + .84 * max(0, len(hand) - 1)))
        card_height = card_width / CARD_ASPECT
        height = min(window.height - 36, card_height + 216 + (30 if has_tabs else 0))
        left, bottom = (window.width - width) / 2, (window.height - height) / 2
        top = bottom + height
        self.bounds = (left, left + width, bottom, top)

        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (3, 7, 14, 125))
        modal(left, left + width, bottom, top)
        window.text('action_cards_title', f'{player.faction.upper()} · ACTION CARDS',
                    left + 22, top - 31, 16, ACCENT)
        window.text('action_cards_help',
                    'Hover a card to lift it forward. Playable cards glow; click one to play it.',
                    left + 22, top - 57, 10, MUTED, width - 44)
        close_x, close_y = left + width - 100, top - 49
        button(window, 'action_cards_close', 'CLOSE', close_x, close_y, 78, 28, size=9)
        self.hits.append((('close',), close_x, close_x + 78, close_y, close_y + 28))

        if has_tabs:
            tab_width = min(142, (width - 54) / len(participants))
            for index, participant in enumerate(participants):
                tx, ty = left + 22 + index * (tab_width + 8), top - 108
                selected = participant is player
                arcade.draw_lrbt_rectangle_filled(tx, tx + tab_width, ty - 27, ty,
                                                  SELECTED if selected else CARD)
                arcade.draw_lrbt_rectangle_outline(tx, tx + tab_width, ty - 27, ty,
                                                   ACCENT if selected else BORDER, 1)
                window.text(('action_card_player', participant.faction), participant.faction.upper(),
                            tx + 8, ty - 18, 8, ACCENT if selected else MUTED, tab_width - 16)
                self.hits.append((('player', participant.faction), tx, tx + tab_width,
                                  ty - 27, ty))

        if not hand:
            window.text('action_cards_empty',
                        'No cards in hand. Politics draws two; each status phase draws one.',
                        left + 22, top - (137 if has_tabs else 96), 11, MUTED, width - 44)
            return

        fan_width = width - 40
        spacing = min(card_width * .84,
                      (fan_width - card_width) / max(1, len(hand) - 1))
        total_width = card_width + spacing * (len(hand) - 1)
        fan_left = left + (width - total_width) / 2
        card_y = bottom + 28
        rectangles = []
        playable_by_index = {}
        for index, alias in enumerate(hand):
            x = fan_left + spacing * index
            rect = (index, x, card_y, card_width, card_height)
            rectangles.append(rect)
            self.card_bounds.append((index, x, x + card_width, card_y, card_y + card_height))
            playable_by_index[index] = bool(window.action_cards.can_play(player.faction, alias, session))

        mouse_x, mouse_y = window.mouse_position
        self.hovered_card_index = next((index for index, x0, x1, y0, y1 in reversed(self.card_bounds)
                                        if x0 <= mouse_x <= x1 and y0 <= mouse_y <= y1), None)

        # Draw the fan back-to-front, then redraw the hovered card above the rest.
        for index, x, y, draw_width, draw_height in rectangles:
            if index == self.hovered_card_index:
                continue
            self._draw_card(window, hand[index], index, x, y,
                            draw_width, draw_height, playable_by_index[index])
            if playable_by_index[index]:
                self.hits.append((('play', index), x, x + draw_width, y, y + draw_height))

        hovered = self.hovered_card_index
        if hovered is not None:
            index, x, y, draw_width, draw_height = rectangles[hovered]
            scale = 1.18
            lifted_width, lifted_height = draw_width * scale, draw_height * scale
            lifted_x = min(max(x - (lifted_width - draw_width) / 2, left + 12),
                           left + width - lifted_width - 12)
            lifted_y = min(y + 16, top - lifted_height - 18)
            self._draw_card(window, hand[index], index, lifted_x, lifted_y,
                            lifted_width, lifted_height, playable_by_index[index])
            if playable_by_index[index]:
                self.hits.append((('play', index), lifted_x, lifted_x + lifted_width,
                                  lifted_y, lifted_y + lifted_height))
