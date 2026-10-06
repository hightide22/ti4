"""Central strategy-card draft and resolution dialogs."""
import arcade

INK = (223, 232, 244)
MUTED = (132, 154, 180)
ACCENT = (100, 207, 224)
CARD = (23, 39, 57)
GOLD = (245, 194, 103)
STRATEGY_CARDS = {
    1: ('Leadership', 'Buy command tokens for 3 influence each.'),
    2: ('Diplomacy', 'Ready up to 2 exhausted planets.'),
    3: ('Politics', 'Action-card draws are not implemented yet.'),
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
        hovered = action == self.hover_action
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + 32,
                                         (48, 113, 117) if hovered else (37, 85, 89) if selected else CARD if enabled else (28, 33, 43))
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + 32,
                                          ACCENT if selected or hovered else (58, 86, 104), 2 if hovered else 1)
        w.text(('strategy_button', action), label, x + 9, y + 10, 10, INK if enabled else MUTED)
        if enabled:
            self.hits.append((action, x, x + width, y, y + 32))
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
        width = min(1060 if draft else 830, w.width - 48)
        height = min(730, w.height - 48) if draft else min(555, w.height - w.player_panel.HEIGHT - 48)
        base_x = (w.width - width) / 2
        base_bottom = (w.height - height) / 2 if draft else w.player_panel.HEIGHT + (w.height - w.player_panel.HEIGHT - height) / 2
        x = max(0, min(w.width - width, base_x + self.offset[0]))
        floor = 0 if draft else w.player_panel.HEIGHT
        bottom = max(floor, min(w.height - height, base_bottom + self.offset[1]))
        self.offset[:] = [x - base_x, bottom - base_bottom]
        top = bottom + height
        self.bounds = (x, x + width, bottom, top)
        arcade.draw_lrbt_rectangle_filled(0, w.width, w.player_panel.HEIGHT if not draft else 0, w.height, (4, 9, 18, 205))
        arcade.draw_lrbt_rectangle_filled(x, x + width, bottom, top, (14, 25, 41))
        arcade.draw_lrbt_rectangle_outline(x, x + width, bottom, top, (78, 138, 156), 2)
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
        w.text('strategy_actor', f'{s.player.name} · {kind}', x + 22, top - 57, 11, GOLD)
        w.player_panel.image(strategy_image(s.card), x + 111, top - 204, 234)
        w.text('strategy_queue', f'Turn owner: {s.owner.faction.upper()}\nResponders left: {len(s.remaining)}',
               x + 24, bottom + 95, 10, MUTED, 177)
        rx, rw, y = x + 229, width - 251, top - 101
        self.draw_stage(w, s, rx, y, rw, bottom)
        if w.movement_error:
            w.text('strategy_error', w.movement_error, x + 22, bottom + 18, 10, (248, 151, 130), width - 44)
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
            cx, cy = x + 30 + (col + .5) * cell_w, top - 87 - (row + .5) * cell_h
            ready = card in turn.available_strategy_cards
            size = min(cell_h - 14, (cell_w - 12) * 1.25)
            w.player_panel.image(strategy_image(card), cx, cy, size,
                                 None if ready else (93, 100, 115, 160))
            bounds = (cx - size * .4, cx + size * .4, cy - size / 2, cy + size / 2)
            if ready:
                self.hits.append((('choose_strategy', card), *bounds))
            else:
                owner = next(p for p in turn.players if card in turn.strategy_assignments[p.faction])
                w.text(('strategy_taken', card), owner.faction.upper(), bounds[0] + 8, cy, 12, GOLD)
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
        self.button(w, ('close',), 'CLOSE', x + width - 118, top - 43, 96)

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
        elif s.stage in ('diplomacy_system', 'warfare_system'):
            if s.stage == 'diplomacy_system':
                tiles = [t for t in w.board.values() if s.player.faction in t.planet_owners.values() and t.number != 18]
            else:
                tiles = [t for t in w.board.values() if s.player.faction in t.command_tokens]
            self.options(w, [(('system', t.position), t.name, False) for t in tiles], x, y - 19, width, rows)
            if not tiles:
                w.text('strategy_no_system', 'No eligible systems.', x, y, 12, MUTED)
                self.button(w, ('continue',), 'CONTINUE', x, foot, width)
        elif s.stage == 'ready_planets':
            options = [(('ready_planet', c.planet.planet_id), c.planet.name, c.planet.planet_id in s.ready_planets)
                       for c in s.player.planets if c.exhausted]
            w.text('strategy_ready_title', f'Ready up to 2 planets · selected {len(s.ready_planets)}/2', x, y, 12, GOLD)
            self.options(w, options, x, y - 47, width, rows)
            self.button(w, ('ready_confirm',), 'CONFIRM PLANETS', x, foot, width)
        elif s.stage == 'speaker':
            self.options(w, [(('speaker', p.faction), p.name, False) for p in w.turn_order.players
                             if p is not w.turn_order.speaker], x, y - 20, width, rows)
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
            self.options(w, [(('build', c.planet.planet_id), c.planet.name, False) for c in s.player.planets],
                         x, y - 87, width, max(2, rows - 1))
            self.button(w, ('continue',), 'SKIP REMAINING PLACEMENTS', x, foot, width)
        elif s.stage == 'production_site':
            options = [(('produce_at', u.unit_id), next(p.name for p in t.planets if p.planet_id == u.location.planet_id), False)
                       for t, u in ctl.home_docks()]
            w.text('strategy_dock_title', 'Choose one home-system Space Dock:', x, y, 12, INK)
            self.options(w, options, x, y - 45, width, rows)
        elif s.stage == 'placeholder':
            w.text('strategy_placeholder', 'This card has no implemented effect yet.', x, y, 12, MUTED, width)
            self.button(w, ('continue',), 'CONTINUE', x, foot, width)
