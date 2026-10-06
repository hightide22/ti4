"""Read-only faction roster with strategy cards and player details."""
import json
import textwrap
from functools import lru_cache

import arcade
from ui_theme import (PANEL, SHELL, CARD, INK, MUTED, ACCENT, GOLD, BORDER, SELECTED,
                      ROSTER_WIDTH)
from board import RESOURCES
from player import command_tokens_in_reinforcements
from player_panel import Control
from strategy_panel import strategy_image


@lru_cache(maxsize=1)
def player_reference():
    def read(path):
        return json.loads((RESOURCES / path).read_text(encoding='utf-8'))
    return ({f['alias']: f for f in read('data/factions/base.json')},
            {a['id']: a for a in read('data/abilities/base.json')},
            {t['alias']: t for t in read('data/technologies/pok.json')})


class PlayerRoster:
    WIDTH = ROSTER_WIDTH

    def __init__(self):
        self.hits = []
        self.hovered = None
        self.hovered_card = None
        self.offset = 0
        self.detail_offset = 0

    def hit_test(self, x, y):
        return next((action for action, l, r, b, t in reversed(self.hits)
                     if l <= x <= r and b <= y <= t), None)

    def hover(self, x, y):
        action = self.hit_test(x, y)
        previous = self.hovered
        self.hovered = action[1] if action and action[0] in ('player', 'card') else None
        self.hovered_card = action[2] if action and action[0] == 'card' else None
        if previous != self.hovered:
            self.detail_offset = 0

    def draw(self, w):
        self.hits.clear()
        top, bottom = w.height - 80, w.player_panel.HEIGHT
        arcade.draw_lrbt_rectangle_filled(0, self.WIDTH, bottom, top, SHELL)
        arcade.draw_line(self.WIDTH, bottom, self.WIDTH, top, BORDER, 1)
        w.text('roster_title', 'PLAYERS / TURN ORDER', 14, top - 24, 10, MUTED)
        count = max(1, int((top - bottom - 45) // 113))
        self.offset = min(self.offset, max(0, len(w.turn_order.players) - count))
        for slot, index in enumerate(range(self.offset, min(len(w.turn_order.players), self.offset + count))):
            player = w.turn_order.players[index]
            row_top = top - 41 - slot * 113
            row_bottom = row_top - 104
            resolving = w.strategy.session and w.strategy.player is player
            active = w.turn_order.active_player is player
            color = SELECTED if resolving else SELECTED if active else CARD
            arcade.draw_lrbt_rectangle_filled(8, self.WIDTH - 8, row_bottom, row_top, color)
            if active or resolving:
                arcade.draw_lrbt_rectangle_filled(8, 11, row_bottom, row_top, GOLD if resolving else ACCENT)
                arcade.draw_lrbt_rectangle_outline(8, self.WIDTH - 8, row_bottom, row_top,
                                                  GOLD if resolving else ACCENT, 1)
            tag = ' · PASSED' if index in w.turn_order.passed_indices else ' · ACTIVE' if active else ''
            w.text(('roster_name', index), player.faction.upper() + tag, 17, row_top - 17, 10, INK)
            w.player_panel.image(f'factions/{player.faction}.png', 34, row_top - 55, 35)
            self.hits.append((('player', index), 8, self.WIDTH - 8, row_bottom, row_top))
            w.player_panel.controls.append(Control(('player', index), 10, row_bottom + 4, 49, 80))
            for i, card in enumerate(w.turn_order.strategy_assignments.get(player.faction, [])):
                used = card in w.turn_order.strategy_used[player.faction]
                cx, cy = 86 + i * 49, row_top - 61
                w.player_panel.image(strategy_image(card, small=True, used=used), cx, cy, 64)
                self.hits.append((('card', index, card), cx - 23, cx + 23, cy - 32, cy + 32))
            if w.turn_order.speaker is player:
                w.player_panel.image('tokens/token_speaker.png', 183, row_top - 53, 34)
                w.text(('speaker_badge', index), 'SPEAKER', 164, row_top - 81, 7, GOLD)
        if len(w.turn_order.players) > count:
            w.text('roster_scroll', 'Scroll to see more players', 12, bottom + 8, 8, MUTED)

    def detail_lines(self, w, player):
        factions, abilities, techs = player_reference()
        faction = factions.get(player.faction, {})
        on_board = sum(player.faction in t.command_tokens for t in w.board.values())
        lines = [(player.name, ACCENT),
                 (f'Trade goods: {player.trade_goods}   Commodities: {player.commodities}/{player.commodity_limit}', GOLD),
                 ('COMMAND TOKENS', MUTED),
                 ('   '.join(f'{key.title()}: {value}' for key, value in player.command_pools.items()), INK),
                 (f'On board: {on_board}   Reinforcements: {command_tokens_in_reinforcements(player, w.board)}   Unallocated: {player.pending_commands}', INK),
                 (f'Planets: {len(player.planets)}   Ready resources / influence: {player.available_values[0]} / {player.available_values[1]}', INK),
                 ('FACTION ABILITIES', MUTED)]
        for ability_id in faction.get('abilities', []):
            ability = abilities.get(ability_id, {})
            description = ' '.join(ability.get(k, '') for k in ('permanentEffect', 'window', 'windowEffect')).strip()
            lines.append((f'{ability.get("name", ability_id)}: {description}', INK))
        lines.append(('TECHNOLOGIES', MUTED))
        for tech_id in sorted(player.technologies):
            tech = techs.get(tech_id, {})
            lines.append((f'{tech.get("name", tech_id)}: {tech.get("text", "")}', INK))
        if not player.technologies:
            lines.append(('None', INK))
        lines.append(('Action cards: not implemented yet', MUTED))
        return lines

    def draw_details(self, w):
        if self.hovered is None:
            return
        player = w.turn_order.players[self.hovered]
        x, width = self.WIDTH + 9, min(510, w.width - w.sidebar - self.WIDTH - 20)
        wrap_width = max(30, int((width - 30) / 6.5))
        lines = [(line, color) for text, color in self.detail_lines(w, player)
                 for line in textwrap.wrap(text.replace('\n', ' '), width=wrap_width) or ['']]
        top = w.height - 92
        max_lines = max(4, int((top - w.player_panel.HEIGHT - 36) // 17))
        self.detail_offset = min(self.detail_offset, max(0, len(lines) - max_lines))
        visible = lines[self.detail_offset:self.detail_offset + max_lines]
        bottom = top - len(visible) * 17 - 32
        arcade.draw_lrbt_rectangle_filled(x, x + width, bottom, top, PANEL)
        arcade.draw_lrbt_rectangle_outline(x, x + width, bottom, top, ACCENT, 1)
        for i, (line, color) in enumerate(visible):
            w.text(('roster_detail', i), line, x + 14, top - 23 - i * 17, 10, color)
        if len(lines) > max_lines:
            w.text('roster_detail_scroll', 'Scroll over player to read more', x + 14, bottom + 8, 9, GOLD)
