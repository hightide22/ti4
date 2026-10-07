"""Central strategy-card draft and resolution dialogs."""
import arcade
from ui_theme import (CARD, INK, MUTED, ACCENT, GOLD, BORDER, SELECTED, DISABLED, DANGER,
                      surface, button, modal)

STRATEGY_CARDS = {
    1: ('Leadership', 'Buy command tokens for 3 influence each.'),
    2: ('Diplomacy', 'Ready up to 2 exhausted planets.'),
    3: ('Politics', 'Primary draws two cards and chooses the Speaker; secondary spends a Strategy token to draw two.'),
    4: ('Construction', 'Place a PDS or Space Dock on a controlled planet.'),
    5: ('Trade', 'Replenish your commodities.'),
    6: ('Warfare', 'Produce at one Space Dock in your home system.'),
    7: ('Technology', 'Not implemented yet.'),
    8: ('Imperial', 'Not implemented yet.'),
}


def strategy_image(card, small=False, used=False):
    if small or used:
        return f'emojis/cards/SC{card}{"Back" if used else ""}.png'
    return f'strat_cards/{"pok_2" if card == 2 else f"base_game_{card}"}.png'


class StrategyPanel:
    def __init__(self):
        self.hits = []
        self.page = 0
        self.page_key = None
        self.bounds = None
        self.offset = [0.0, 0.0]
        self.mouse = None
        self.hover_action = None
        self.hover_system = None
        self.hover_system_hits = []

    def drag_header(self, x, y):
        if not self.bounds:
            return False
        left, right, bottom, top = self.bounds
        return left <= x <= min(right, left + 390) and top - 72 <= y <= top - 8

    def move(self, dx, dy):
        self.offset[0] += dx
        self.offset[1] += dy

    def update_hover(self, x, y):
        self.mouse = (x, y)
        self.hover_action = next((a for a, l, r, b, t in reversed(self.hits)
                                  if l <= x <= r and b <= y <= t), None)
        self.hover_system = next((position for l, r, b, t, position in reversed(self.hover_system_hits)
                                  if l <= x <= r and b <= y <= t), None)

    def hit_test(self, x, y):
        return next((a for a, l, r, b, t in reversed(self.hits) if l <= x <= r and b <= y <= t), None)

    def button(self, w, action, label, x, y, width, enabled=True, selected=False):
        primary = action[0] in ('choose_strategy', 'start', 'pay', 'ready_confirm', 'trade_confirm', 'accept', 'continue')
        button(w, ('strategy_button', action), label, x, y, width, 34,
               primary=primary, selected=selected, enabled=enabled, size=11)
        if enabled:
            self.hits.append((action, x, x + width, y, y + 34))
            if action and action[0] == 'build':
                planet_id = action[1]
                position = next((tile.position for tile in w.board.values()
                                 if any(p.planet_id == planet_id for p in tile.planets)), None)
                if position is not None:
                    self.hover_system_hits.append((x, x + width, y, y + 32, position))

    def options(self, w, options, x, y, width, size=5):
        self.page = min(self.page, max(0, (len(options) - 1) // size))
        for i, (action, label, selected) in enumerate(options[self.page * size:(self.page + 1) * size]):
            self.button(w, action, label, x, y - i * 39, width, selected=selected)
        if len(options) > size:
            self.button(w, ('page', -1), '<', x, y - size * 39, 40, self.page > 0)
            self.button(w, ('page', 1), '>', x + width - 40, y - size * 39, 40,
                        (self.page + 1) * size < len(options))

    def draw(self, w, turn, left=None):
        self.hits.clear()
        self.hover_system_hits.clear()
        s = w.strategy.session
        draft = turn.strategy_selection
        map_choice = bool(s and s.stage in ('construction', 'diplomacy_system',
                                            'diplomacy_secondary_system', 'warfare_system'))
        width = min(1060 if draft else 440 if map_choice else 830, w.width - 48)
        height = min(730, w.height - 48) if draft else min(555, w.height - w.player_panel.HEIGHT - 48)
        base_x = w.width - width - 16 if map_choice else (w.width - width) / 2
        base_bottom = (w.height - height) / 2 if draft else w.player_panel.HEIGHT + (w.height - w.player_panel.HEIGHT - height) / 2
        x = max(0, min(w.width - width, base_x + self.offset[0]))
        floor = 0 if draft else w.player_panel.HEIGHT
        bottom = max(floor, min(w.height - height, base_bottom + self.offset[1]))
        self.offset[:] = [x - base_x, bottom - base_bottom]
        top = bottom + height
        self.bounds = (x, x + width, bottom, top)
        if not map_choice:
            arcade.draw_lrbt_rectangle_filled(0, w.width, w.player_panel.HEIGHT if not draft else 0,
                                              w.height, (4, 9, 18, 205))
        modal(x, x + width, bottom, top)
        w.text('strategy_drag_hint', 'DRAG HEADER TO MOVE', x + width - 181, top - 22, 8, MUTED)
        if draft:
            self.draw_draft(w, turn, x, bottom, width, height)
            if self.mouse:
                self.update_hover(*self.mouse)
            return
        if not s:
            self.draw_owned(w, turn, x, bottom, width, height)
            if self.mouse:
                self.update_hover(*self.mouse)
            return
        key = (s.card, s.player.faction, s.stage, s.builds_left)
        if key != self.page_key:
            self.page, self.page_key = 0, key
        w.text('strategy_modal_title', f'{s.card} · {STRATEGY_CARDS[s.card][0].upper()}',
               x + 22, top - 31, 17, ACCENT)
        kind = 'PRIMARY' if s.primary else 'SECONDARY'
        w.text('strategy_actor', f'{s.player.name} / {kind} ABILITY', x + 22, top - 57, 11, GOLD)
        w.player_panel.image(strategy_image(s.card), x + 111, top - 204, 234)
        w.text('strategy_queue', f'Action: {s.owner.faction.upper()}\nNext responses: {len(s.remaining)}',
               x + 24, bottom + 95, 10, MUTED, 177)
        rx, rw, y = x + 229, width - 251, top - 101
        self.draw_stage(w, s, rx, y, rw, bottom)
        if w.movement_error:
            w.text('strategy_error', w.movement_error, x + 22, bottom + 18, 10, DANGER, width - 44)
        if self.mouse:
            self.update_hover(*self.mouse)

    def draw_draft(self, w, turn, x, bottom, width, height):
        top = bottom + height
        player = turn.active_player
        picks = 2 if len(turn.players) in (3, 4) else 1
        w.text('strategy_draft_title', 'CHOOSE A STRATEGY CARD', x + 24, top - 34, 19, ACCENT)
        w.text('strategy_draft_actor', f'{player.name} · pick {len(turn.strategy_assignments[player.faction]) + 1}/{picks}',
               x + 24, top - 61, 12, INK)
        cell_w = (width - 60) / 4
        cell_h = (height - 115) / 2
        for card in range(1, 9):
            col, row = (card - 1) % 4, (card - 1) // 4
            cell_left = x + 24 + col * cell_w
            cell_top = top - 88 - row * cell_h
            cell_bottom = cell_top - cell_h + 10
            ready = card in turn.available_strategy_cards
            hovered = self.hover_action == ('choose_strategy', card)
            surface(cell_left, cell_left + cell_w - 10, cell_bottom, cell_top,
                    SELECTED if hovered else CARD if ready else DISABLED,
                    ACCENT if hovered else BORDER)
            image_size = min(cell_h - 85, cell_w * .72)
            w.player_panel.image(strategy_image(card), cell_left + (cell_w - 10) / 2,
                                 cell_top - 14 - image_size / 2, image_size,
                                 None if ready else (130, 140, 151, 140))
            w.text(('strategy_card_title', card), f'{card:02}  {STRATEGY_CARDS[card][0]}',
                   cell_left + 12, cell_bottom + 40, 12, INK, max_width=cell_w - 32)
            if ready:
                status = 'Choose card'
                self.hits.append((('choose_strategy', card), cell_left, cell_left + cell_w - 10,
                                  cell_bottom, cell_top))
            else:
                owner = next(p for p in turn.players if card in turn.strategy_assignments[p.faction])
                status = f'Taken / {owner.faction.upper()}'
            w.text(('strategy_taken', card), status, cell_left + 12, cell_bottom + 15,
                   10, ACCENT if ready else MUTED, max_width=cell_w - 32)
        w.text('strategy_draft_speaker', f'Speaker: {turn.speaker.name} · clockwise selection',
               x + 24, bottom + 15, 10, MUTED)

    def draw_owned(self, w, turn, x, bottom, width, height):
        player = turn.active_player
        top = bottom + height
        w.text('strategy_owned_title', f'{player.faction.upper()} · STRATEGY CARDS', x + 22, top - 35, 16, ACCENT)
        cards = turn.strategy_assignments[player.faction]
        for i, card in enumerate(cards):
            cx = x + width * (i + 1) / (len(cards) + 1)
            size = min(310, height - 130)
            used = card in turn.strategy_used[player.faction]
            w.player_panel.image(strategy_image(card, used=used), cx, bottom + height / 2, size)
            self.button(w, ('start', card), f'{card} · {"USED" if used else "PLAY CARD"}',
                        cx - 85, bottom + 54, 170,
                        not used and not turn.action_used and not player.pending_commands and not turn.command_allocation)
        self.button(w, ('close',), 'CLOSE', x + width - 118, top - 65, 96)

    def draw_stage(self, w, s, x, y, width, bottom):
        ctl = w.strategy
        foot = bottom + 53
        rows = max(2, min(5, int((y - foot - 80) // 39)))
        if s.stage == 'offer':
            w.text('strategy_offer', STRATEGY_CARDS[s.card][1], x, y, 13, INK, width)
            cost = ctl.secondary_cost()
            reason = ctl.secondary_unavailable()
            w.text('secondary_cost', f'Strategy token cost: {cost}', x, y - 67, 12, GOLD)
            if reason:
                w.text('secondary_reason', reason, x, y - 105, 11, MUTED, width)
            self.button(w, ('accept',), 'USE SECONDARY', x, foot, width / 2 - 5, not reason)
            self.button(w, ('decline',), 'SKIP', x + width / 2 + 5, foot, width / 2 - 5)
        elif s.stage == 'leadership':
            w.text('leadership_base', f'Free tokens: {s.base_gain} · Extra tokens: {s.purchases}', x, y, 13, INK)
            self.button(w, ('buy', -1), '−', x, y - 46, 38, s.purchases > 0)
            self.button(w, ('buy', 1), '+', x + 48, y - 46, 38)
            w.text('leadership_payment', f'Influence paid: {ctl.payment()}/{s.purchases * 3}', x, y - 81, 16, GOLD)
            w.text('leadership_help', 'Click ready planets below to pay their influence.\nClick again to undo payment.',
                   x, y - 117, 11, INK, width)
            w.text('leadership_goods', f'Trade goods: {s.trade_goods}/{s.player.trade_goods}', x, y - 176, 12, INK)
            self.button(w, ('goods', -1), '−', x, y - 224, 38, s.trade_goods > 0)
            self.button(w, ('goods', 1), '+', x + 48, y - 224, 38, s.trade_goods < s.player.trade_goods)
            self.button(w, ('pay',), 'CONFIRM PAYMENT', x, foot, width, ctl.payment() >= s.purchases * 3)
        elif s.stage in ('allocate', 'warfare_allocate'):
            w.text('strategy_allocate', f'Tokens to allocate: {s.player.pending_commands}', x, y, 16, GOLD)
            for i, pool in enumerate(('tactical', 'fleet', 'strategic')):
                self.button(w, ('allocate', pool), f'{pool.title()}: {s.player.command_pools[pool]}',
                            x, y - 54 - i * 42, width, selected=s.pool_source == pool)
            w.text('strategy_allocate_help', 'Choose a pool for each new token.' if s.stage == 'allocate' else
                   'Allocate the returned token, then redistribute\nby selecting a source pool and a destination.',
                   x, y - 213, 11, INK, width)
            if s.stage == 'warfare_allocate':
                self.button(w, ('continue',), 'FINISH REDISTRIBUTION', x, foot, width, not s.player.pending_commands)
        elif s.stage in ('diplomacy_system', 'diplomacy_secondary_system', 'warfare_system'):
            eligible = ctl.selectable_systems()
            if s.stage == 'warfare_system':
                if s.pending_system is not None:
                    tile = w.board[s.pending_system]
                    w.text('warfare_token_confirm', f'Remove your command token from {tile.name}?',
                           x, y, 13, GOLD, width)
                    self.button(w, ('confirm_warfare_removal',), 'CONFIRM TOKEN REMOVAL',
                                x, foot, width)
                elif s.selected_system is not None:
                    tile = w.board[s.selected_system]
                    w.text('warfare_token_selected', f'{tile.name} selected · click your token on the map',
                           x, y, 13, GOLD, width)
                else:
                    w.text('warfare_map_help', 'Click a highlighted system, then click your command token.',
                           x, y, 13, INK, width)
            else:
                title = 'Choose a system on the map' if eligible else 'No eligible systems.'
                if s.selected_system is not None:
                    title = f'{w.board[s.selected_system].name} selected · choose a planet next'
                w.text('strategy_map_help', title, x, y, 13, INK if eligible else MUTED, width)
            if not eligible and s.stage != 'warfare_system':
                self.button(w, ('continue',), 'CONTINUE', x, foot, width)
            elif not eligible:
                w.text('strategy_no_system', 'No eligible systems.', x, y, 12, MUTED)
                self.button(w, ('continue',), 'CONTINUE', x, foot, width)
        elif s.stage == 'ready_planets':
            options = [(('ready_planet', c.planet.planet_id), c.planet.name, c.planet.planet_id in s.ready_planets)
                       for c in s.player.planets if c.exhausted and
                       w.strategy.planet_system(c.planet.planet_id).position == s.selected_system]
            w.text('strategy_ready_title', f'Ready up to 2 planets · selected {len(s.ready_planets)}/2', x, y, 12, GOLD)
            self.options(w, options, x, y - 47, width, rows)
            self.button(w, ('ready_confirm',), 'CONFIRM PLANETS', x, foot, width)
        elif s.stage == 'speaker':
            if s.drawn_cards:
                names = [w.action_cards.name_for(alias) for alias in s.drawn_cards]
                w.text('politics_draw_result', f'Drew: {", ".join(names)}', x, y, 11, GOLD, width)
            self.options(w, [(('speaker', p.faction), p.name, False) for p in w.turn_order.players
                             if p is not w.turn_order.speaker], x, y - (43 if s.drawn_cards else 20), width, rows)
        elif s.stage == 'trade':
            w.text('strategy_trade_title', 'Select players for free secondary abilities:', x, y, 12, INK, width)
            self.options(w, [(('trade_toggle', p.faction), p.name, p.faction in s.free_trade)
                             for p in w.turn_order.players if p is not s.owner], x, y - 47, width, rows)
            self.button(w, ('trade_confirm',), 'CONFIRM TRADE', x, foot, width)
        elif s.stage == 'construction':
            w.text('strategy_build_count', f'Placements remaining: {s.builds_left}', x, y, 12, GOLD)
            for i, kind in enumerate(('spacedock', 'pds')):
                self.button(w, ('structure', kind), 'SPACE DOCK' if i == 0 else 'PDS',
                            x + i * (width / 2 + 4), y - 44, width / 2 - 4,
                            not (s.primary and s.builds_left == 1 and kind == 'spacedock'), s.structure == kind)
            planets = ctl.buildable_planets()
            if s.selected_system is None:
                w.text('strategy_build_map_help', 'Click a highlighted system on the map to choose a planet.',
                       x, y - 77, 11, INK, width)
            else:
                w.text('strategy_build_system', f'{w.board[s.selected_system].name} · choose a planet',
                       x, y - 77, 11, GOLD, width)
                self.options(w, [(('build', c.planet.planet_id), c.planet.name, False) for c in planets],
                             x, y - 109, width, max(2, rows - 1))
            self.button(w, ('continue',), 'SKIP REMAINING PLACEMENTS', x, foot, width)
        elif s.stage == 'production_site':
            options = [(('produce_at', u.unit_id), next(p.name for p in t.planets if p.planet_id == u.location.planet_id), False)
                       for t, u in ctl.home_docks()]
            w.text('strategy_dock_title', 'Choose one home-system Space Dock:', x, y, 12, INK)
            self.options(w, options, x, y - 45, width, rows)
        elif s.stage == 'placeholder':
            w.text('strategy_placeholder', 'This card has no implemented effect yet.', x, y, 12, MUTED, width)
            self.button(w, ('continue',), 'CONTINUE', x, foot, width)
