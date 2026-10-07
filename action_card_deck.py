"""Minimal base action-card draw pile for status-phase draws and Hacan trades."""
import json
import random

from board import RESOURCES


class ActionCardDeck:
    def __init__(self, rng=None):
        cards = json.loads((RESOURCES / 'data/action_cards/action_cards.json').read_text(encoding='utf-8'))
        self.cards = [card['alias'] for card in cards if card.get('source') == 'base']
        (rng or random).shuffle(self.cards)

    def draw(self):
        return self.cards.pop() if self.cards else None
