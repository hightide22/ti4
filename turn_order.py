from __future__ import annotations


class TurnOrder:
    """Tracks the active player and the single completed action in their turn."""

    def __init__(self, players):
        self.players = list(players)
        self.active_index = 0
        self.turn_number = 1
        self.action_used = False

    @property
    def active_player(self):
        return self.players[self.active_index] if self.players else None

    def mark_action_completed(self):
        if self.action_used:
            return False
        self.action_used = True
        return True

    def pass_turn(self):
        if not self.players:
            return None
        self.active_index = (self.active_index + 1) % len(self.players)
        self.turn_number += 1
        self.action_used = False
        return self.active_player
