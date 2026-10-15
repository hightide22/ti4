"""Curated combat and economy cards from base, PoK, and Codex I."""
import json
import random

from board import RESOURCES
from action_cards import CARD_EFFECT_ALIASES


class ActionCardDeck:
    def __init__(self, rng=None):
        cards = json.loads((RESOURCES / 'data/action_cards/action_cards.json').read_text(encoding='utf-8'))
        self.cards = [card['alias'] for card in cards
                      if card.get('source') in ('base', 'pok', 'codex1') and
                      card['alias'] in CARD_EFFECT_ALIASES]
        (rng or random).shuffle(self.cards)

    def draw(self):
        return self.cards.pop() if self.cards else None
