from __future__ import annotations


class TurnOrder:
    """Tracks round-robin turns and players who have ended the current round."""

    def __init__(self, players):
        self.players = list(players)
        self.active_index = 0
        self.round_number = 1
        self.action_used = False
        self.passed_indices: set[int] = set()

    @property
    def active_player(self):
        return self.players[self.active_index] if self.players else None

    def mark_action_completed(self):
        if self.action_used:
            return False
        self.action_used = True
        return True

    def end_turn(self):
        if not self.players:
            return False
        if getattr(self.active_player, 'pending_commands', 0):
            raise ValueError('Allocate all new command tokens before ending the turn')
        self.passed_indices.add(self.active_index)
        self.action_used = False
        if len(self.passed_indices) == len(self.players):
            self.passed_indices.clear()
            self.active_index = 0
            self.round_number += 1
            return True
        for offset in range(1, len(self.players) + 1):
            next_index = (self.active_index + offset) % len(self.players)
            if next_index not in self.passed_indices:
                self.active_index = next_index
                return False
        raise RuntimeError('No eligible player found during turn rotation')
