from __future__ import annotations

import arcade

INK = (223, 232, 244)
MUTED = (132, 154, 180)
ACCENT = (100, 207, 224)
CARD = (23, 39, 57)
GOLD = (245, 194, 103)

STRATEGY_CARDS = {
    1: ('Leadership', 'Gain 3 command tokens; buy more for influence.'),
    2: ('Diplomacy', 'Place opponents’ tokens in a controlled system; ready 2 planets.'),
    3: ('Politics', 'Choose the next Speaker. Action-card draws are skipped.'),
    4: ('Construction', 'Place a PDS or Space Dock, then another PDS.'),
    5: ('Trade', 'Gain 3 trade goods; replenish commodities.'),
    6: ('Warfare', 'Remove one of your command tokens and redistribute it.'),
    7: ('Technology', 'Primary and secondary abilities are not implemented yet.'),
    8: ('Imperial', 'Primary and secondary abilities are not implemented yet.'),
}


class StrategyPanel:
    def __init__(self):
        self.hits = []

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def draw(self, window, turn, left):
        self.hits.clear()
        x, width = left + 18, window.sidebar - 36
        y = window.height - 26
        if turn.strategy_selection:
            player = turn.active_player
            speaker = turn.speaker
            picks_per_player = 2 if len(turn.players) in (3, 4) else 1
            chosen = len(turn.strategy_assignments[player.faction])
            window.text('strategy_phase_title', 'STRATEGY PHASE', x, y, 12, ACCENT)
            y -= 28
            window.text('strategy_speaker', f'Speaker: {speaker.name}', x, y, 10, GOLD, width)
            y -= 22
            window.text('strategy_picker', f'{player.name} · choose card {chosen + 1} of {picks_per_player}',
                        x, y, 10, INK, width)
            y -= 29
            for card in turn.available_strategy_cards:
                name, detail = STRATEGY_CARDS[card]
                top = y
                arcade.draw_lrbt_rectangle_filled(x, x + width, top - 62, top, CARD)
                arcade.draw_lrbt_rectangle_outline(x, x + width, top - 62, top, (67, 111, 139), 1)
                arcade.draw_lrbt_rectangle_filled(x + 8, x + 39, top - 53, top - 8, (76, 45, 42))
                window.text(('strategy_number', card), str(card), x + 19, top - 37, 17, GOLD)
                window.text(('strategy_name', card), name.upper(), x + 48, top - 21, 10, ACCENT)
                window.text(('strategy_detail', card), detail, x + 48, top - 43, 8, MUTED, width - 57)
                self.hits.append((('choose_strategy', card), x, x + width, top - 62, top))
                y -= 68
            window.text('strategy_order_hint', 'Pick order follows the Speaker clockwise.', x, 118, 9, MUTED, width)
            return

        pending = getattr(window, 'strategy_pending', None)
        if pending and pending[0] == 'leadership':
            _, player, purchases = pending
            window.text('leadership_title', 'LEADERSHIP', x, y, 12, ACCENT)
            y -= 30
            influence = sum(c.planet.influence for c in player.planets if not c.exhausted)
            reinforcements = max(0, 16 - sum(player.command_pools.values()) - player.pending_commands -
                                 sum(player.faction in t.command_tokens for t in window.board.values()))
            max_buy = min((influence + player.trade_goods) // 3, max(0, reinforcements - 3))
            window.text('leadership_base', 'Base gain: 3 command tokens', x, y, 10, INK)
            y -= 25
            window.text('leadership_buy_count', f'Buy extra: {purchases} · cost {purchases * 3} influence',
                        x, y, 10, GOLD)
            y -= 34
            for label, action, enabled in (('-', 'leadership_minus', purchases > 0),
                                           ('+', 'leadership_plus', purchases < max_buy)):
                bx = x + (0 if label == '-' else 48)
                arcade.draw_lrbt_rectangle_filled(bx, bx + 38, y - 2, y + 27,
                                                   (38, 62, 78) if enabled else (29, 36, 47))
                window.text(('leadership_control', label), label, bx + 14, y + 6, 12,
                            INK if enabled else MUTED)
                if enabled:
                    self.hits.append(((action,), bx, bx + 38, y - 2, y + 27))
            y -= 44
            bx, by, bh = x, y - 5, 30
            arcade.draw_lrbt_rectangle_filled(bx, bx + width, by, by + bh, (28, 70, 75))
            window.text('leadership_confirm', 'CONFIRM LEADERSHIP', bx + 8, by + 9, 9, INK)
            self.hits.append((('leadership_confirm',), bx, bx + width, by, by + bh))
            return
        if pending and pending[0] == 'speaker':
            _, card, owner = pending
            window.text('speaker_pick_title', 'CHOOSE THE NEXT SPEAKER', x, y, 12, ACCENT)
            y -= 35
            for candidate in turn.players:
                if candidate is turn.speaker:
                    continue
                top = y
                arcade.draw_lrbt_rectangle_filled(x, x + width, top - 38, top, CARD)
                arcade.draw_lrbt_rectangle_outline(x, x + width, top - 38, top, (67, 111, 139), 1)
                window.text(('speaker_candidate', candidate.faction), candidate.name, x + 10, top - 25, 10, INK)
                self.hits.append((('speaker_pick', candidate.faction), x, x + width, top - 38, top))
                y -= 45
            return

        player = turn.active_player
        window.text('strategy_action_title', 'STRATEGY CARDS', x, y, 12, ACCENT)
        y -= 27
        speaker_name = turn.speaker.name if turn.speaker else 'None'
        window.text('strategy_current_speaker', f'Speaker: {speaker_name}', x, y, 9, GOLD, width)
        y -= 24
        cards = turn.strategy_assignments.get(player.faction, [])
        if not cards:
            window.text('strategy_no_cards', f'{player.name} has no strategy cards.', x, y, 10, MUTED, width)
            return
        window.text('strategy_owned_title', f'{player.name} · primary actions', x, y, 10, INK, width)
        y -= 27
        used = turn.strategy_used.get(player.faction, set())
        for card in cards:
            name, detail = STRATEGY_CARDS[card]
            top = y
            ready = card not in used
            arcade.draw_lrbt_rectangle_filled(x, x + width, top - 69, top,
                                               (28, 62, 75) if ready else (30, 38, 49))
            arcade.draw_lrbt_rectangle_outline(x, x + width, top - 69, top,
                                               (68, 133, 158) if ready else (48, 61, 77), 1)
            window.text(('strategy_owned_number', card), f'{card}', x + 14, top - 28, 18,
                        GOLD if ready else MUTED)
            window.text(('strategy_owned_name', card), f'{name.upper()} · {"READY" if ready else "USED"}',
                        x + 42, top - 19, 9, ACCENT if ready else MUTED)
            window.text(('strategy_owned_detail', card), detail, x + 42, top - 39, 8, INK, width - 51)
            if ready:
                self.hits.append((('strategy_primary', card), x, x + width, top - 69, top))
            y -= 75
        secondary_cards = [card for card in range(1, 9) if turn.can_use_secondary(player, card)]
        if secondary_cards:
            y -= 4
            window.text('strategy_secondaries_title', 'AVAILABLE SECONDARIES', x, y, 9, GOLD)
            y -= 24
            for card in secondary_cards:
                name = STRATEGY_CARDS[card][0]
                arcade.draw_lrbt_rectangle_filled(x, x + width, y - 25, y + 3, (42, 54, 67))
                window.text(('strategy_secondary_label', card), f'{card} · {name}  SECONDARY',
                            x + 8, y - 16, 8, INK)
                self.hits.append((('strategy_secondary', card), x, x + width, y - 25, y + 3))
                y -= 32
        if turn.has_unused_strategy(player):
            window.text('strategy_pass_rule', 'Use every selected strategy card before passing.',
                        x, 118, 9, GOLD, width)
        bx, by, bh = x, 66, 30
        arcade.draw_lrbt_rectangle_filled(bx, bx + width, by, by + bh, (28, 47, 65))
        window.text('strategy_return_system', 'SYSTEM INFO', bx + 9, by + 9, 9, ACCENT)
        self.hits.append((('strategy_show_system',), bx, bx + width, by, by + bh))
