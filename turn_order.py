from __future__ import annotations


class TurnOrder:
    """Tracks round-robin turns and players who have ended the current round."""

    def __init__(self, players, strategy_enabled=False):
        self.players = list(players)
        self.active_index = 0
        self.round_number = 1
        self.turn_serial = 0
        self.action_used = False
        self.actions_used = 0
        self.passed_indices: set[int] = set()
        self.command_allocation = False
        self.strategy_enabled = strategy_enabled
        self.strategy_resolution = None
        self.speaker_index = next((i for i, p in enumerate(self.players)
                                   if getattr(p, 'faction', None) == 'sol'), 0)
        self.strategy_selection = False
        self.strategy_pick_order = []
        self.strategy_pick_cursor = 0
        self.strategy_assignments = {self._key(p): [] for p in self.players}
        self.strategy_used = {self._key(p): set() for p in self.players}
        self.strategy_secondary_used = {self._key(p): set() for p in self.players}
        self.strategy_free_secondary = {5: set()}
        self.strategy_initiative = list(range(len(self.players)))
        self.qdn_pending = False
        if strategy_enabled:
            self.begin_strategy_phase()

    @property
    def active_player(self):
        if self.strategy_selection and self.strategy_pick_order:
            return self.players[self.strategy_pick_order[self.strategy_pick_cursor]]
        return self.players[self.active_index] if self.players else None

    @property
    def speaker(self):
        return self.players[self.speaker_index] if self.players else None

    @property
    def available_strategy_cards(self):
        chosen = {card for cards in self.strategy_assignments.values() for card in cards}
        return tuple(card for card in range(1, 9) if card not in chosen)

    def _key(self, player):
        return getattr(player, 'faction', f'player-{self.players.index(player)}')

    def begin_strategy_phase(self):
        if not self.players:
            return
        self.strategy_selection = True
        self.qdn_pending = False
        self.action_used = False
        self.actions_used = 0
        self.passed_indices.clear()
        self.reset_strategy_cards()
        speaker_order = [(self.speaker_index + offset) % len(self.players)
                         for offset in range(len(self.players))]
        picks_per_player = 2 if len(self.players) in (3, 4) else 1
        self.strategy_pick_order = [index for _ in range(picks_per_player) for index in speaker_order]
        self.strategy_pick_cursor = 0

    def reset_strategy_cards(self):
        """Return every strategy card to the draft after a completed round."""
        self.strategy_assignments = {self._key(p): [] for p in self.players}
        self.strategy_used = {self._key(p): set() for p in self.players}
        self.strategy_secondary_used = {self._key(p): set() for p in self.players}
        self.strategy_free_secondary = {5: set()}

    def choose_strategy_card(self, card):
        if not self.strategy_selection or card not in self.available_strategy_cards:
            raise ValueError('That strategy card is not available')
        player = self.active_player
        self.strategy_assignments[self._key(player)].append(card)
        self.strategy_pick_cursor += 1
        if self.strategy_pick_cursor >= len(self.strategy_pick_order):
            self.strategy_selection = False
            self.strategy_initiative = sorted(range(len(self.players)), key=lambda i: (
                min(self.strategy_assignments[self._key(self.players[i])], default=99),
                (i - self.speaker_index) % len(self.players)))
            self.active_index = self.strategy_initiative[0]
            hacan = next((p for p in self.players if getattr(p, 'faction', None) == 'hacan'), None)
            self.qdn_pending = bool(hacan and 'qdn' in getattr(hacan, 'technologies', ()) and
                                    getattr(hacan, 'command_pools', {}).get('strategic', 0) > 0 and
                                    getattr(hacan, 'trade_goods', 0) >= 3)
        return player

    def qdn_exchange(self, target_faction, own_card, target_card):
        hacan = next((p for p in self.players if getattr(p, 'faction', None) == 'hacan'), None)
        other = next((p for p in self.players if self._key(p) == target_faction), None)
        if (not self.qdn_pending or hacan is None or other is None or other is hacan or
                own_card not in self.strategy_assignments['hacan'] or
                target_card not in self.strategy_assignments[target_faction] or
                hacan.command_pools['strategic'] < 1 or hacan.trade_goods < 3):
            raise ValueError('This strategy-card exchange is unavailable.')
        hacan.command_pools['strategic'] -= 1
        hacan.trade_goods -= 3
        other.trade_goods += 3
        self.strategy_assignments['hacan'].remove(own_card)
        self.strategy_assignments['hacan'].append(target_card)
        self.strategy_assignments[target_faction].remove(target_card)
        self.strategy_assignments[target_faction].append(own_card)
        self.strategy_initiative = sorted(range(len(self.players)), key=lambda i: (
            min(self.strategy_assignments[self._key(self.players[i])], default=99),
            (i - self.speaker_index) % len(self.players)))
        self.active_index = self.strategy_initiative[0]
        self.qdn_pending = False

    def skip_qdn(self):
        self.qdn_pending = False

    def set_speaker(self, player):
        if player not in self.players:
            raise ValueError('Unknown Speaker')
        if player is self.speaker:
            raise ValueError('The current Speaker cannot be chosen again')
        self.speaker_index = self.players.index(player)

    def mark_strategy_used(self, player, card):
        if card not in self.strategy_assignments.get(self._key(player), []):
            raise ValueError('This player does not own that strategy card')
        self.strategy_used[self._key(player)].add(card)
        if player is self.active_player:
            self.mark_action_completed()

    def has_unused_strategy(self, player):
        key = self._key(player)
        cards = self.strategy_assignments.get(key, [])
        return any(card not in self.strategy_used.get(key, set()) for card in cards)

    def can_use_secondary(self, player, card):
        s = self.strategy_resolution
        return bool(s and s.stage == 'offer' and not s.primary and
                    s.player is player and s.card == card and card not in (3, 8) and
                    card not in self.strategy_secondary_used[self._key(player)])

    def mark_secondary_used(self, player, card):
        if not self.can_use_secondary(player, card):
            raise ValueError('This secondary ability is not available')
        self.strategy_secondary_used[self._key(player)].add(card)

    def clockwise_players_after(self, player):
        index = self.players.index(player)
        return [self.players[(index + offset) % len(self.players)]
                for offset in range(1, len(self.players))]

    def mark_action_completed(self):
        if not self.can_take_action:
            return False
        self.actions_used += 1
        self.action_used = True
        return True

    @property
    def can_take_action(self):
        player = self.active_player
        return bool(player and self.actions_used <
                    (2 if 'fl' in getattr(player, 'technologies', ()) else 1))

    def end_turn(self):
        if not self.players:
            return False
        if self.strategy_resolution or self.strategy_selection or self.qdn_pending:
            raise ValueError('Finish resolving the strategy card first.')
        if self.command_allocation:
            raise ValueError('Finish command allocation before taking a turn')
        if getattr(self.active_player, 'pending_commands', 0):
            raise ValueError('Allocate all new command tokens before ending the turn')
        if self.action_used:
            self.action_used = False
            self.actions_used = 0
        else:
            if self.strategy_enabled and self.has_unused_strategy(self.active_player):
                raise ValueError('Use all of your strategy cards before passing')
            self.passed_indices.add(self.active_index)
            if len(self.passed_indices) == len(self.players):
                self.passed_indices.clear()
                self.active_index = self.strategy_initiative[0] if self.strategy_initiative else 0
                self.round_number += 1
                self.turn_serial += 1
                if self.strategy_enabled:
                    self.reset_strategy_cards()
                return True
        order = self.strategy_initiative if self.strategy_enabled and self.strategy_initiative else list(range(len(self.players)))
        current = order.index(self.active_index) if self.active_index in order else 0
        for offset in range(1, len(self.players) + 1):
            next_index = order[(current + offset) % len(order)]
            if next_index not in self.passed_indices:
                self.active_index = next_index
                self.turn_serial += 1
                return False
        raise RuntimeError('No eligible player found during turn rotation')

    def begin_command_allocation(self):
        self.command_allocation = any(getattr(player, 'pending_commands', 0) for player in self.players)
        self.active_index = next((index for index, player in enumerate(self.players)
                                  if getattr(player, 'pending_commands', 0)), 0)

    def finish_player_command_allocation(self):
        if not self.command_allocation:
            return True
        if getattr(self.active_player, 'pending_commands', 0):
            return False
        for offset in range(1, len(self.players) + 1):
            next_index = (self.active_index + offset) % len(self.players)
            if getattr(self.players[next_index], 'pending_commands', 0):
                self.active_index = next_index
                return False
        self.command_allocation = False
        self.active_index = 0
        self.action_used = False
        self.actions_used = 0
        return True
