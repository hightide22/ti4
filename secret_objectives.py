"""Hidden base-game and Prophecy of Kings secret-objective draw pile.

Scoring is deliberately handled elsewhere. This controller owns only the
unscored hands, draws, and the mandatory return after exceeding the limit.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
import random

from board import RESOURCES


@dataclass(frozen=True)
class SecretObjectiveCard:
    alias: str
    name: str
    phase: str
    text: str
    points: int
    source: str


@lru_cache(maxsize=1)
def secret_objective_catalog() -> dict[str, SecretObjectiveCard]:
    path = RESOURCES / 'data/secret_objectives/secret_objectives.json'
    result = {}
    for data in json.loads(path.read_text(encoding='utf-8')):
        if data.get('source') not in {'base', 'pok'}:
            continue
        card = SecretObjectiveCard(data['alias'], data['name'], data['phase'],
                                   data['text'], data['points'], data['source'])
        if card.alias in result:
            raise ValueError(f'Duplicate secret objective: {card.alias}')
        result[card.alias] = card
    return result


class SecretObjectiveController:
    LIMIT = 3

    def __init__(self, players, *, rng=None, deck=None):
        self.players = {player.faction: player for player in players}
        self.catalog = secret_objective_catalog()
        self.rng = rng or random.Random()
        self.deck = list(self.catalog) if deck is None else list(deck)
        if len(self.deck) != len(set(self.deck)) or any(
                alias not in self.catalog for alias in self.deck):
            raise ValueError('Secret-objective deck contains invalid cards.')
        if deck is None:
            self.rng.shuffle(self.deck)
        self.hands: dict[str, list[str]] = {faction: [] for faction in self.players}
        self.pending_returns: dict[str, int] = {}

    def snapshot(self):
        return deepcopy((self.deck, self.hands, self.pending_returns))

    def restore(self, state):
        self.deck, self.hands, self.pending_returns = deepcopy(state)

    def hand_for(self, faction: str) -> tuple[SecretObjectiveCard, ...]:
        return tuple(self.catalog[alias] for alias in self.hands[faction])

    def draw(self, player, count=1) -> tuple[SecretObjectiveCard, ...]:
        faction = player.faction
        if faction not in self.hands:
            raise ValueError('Unknown player for secret-objective draw.')
        if faction in self.pending_returns:
            raise ValueError('Return an unscored secret objective before drawing again.')
        drawn = []
        for _ in range(max(0, count)):
            if not self.deck:
                break
            alias = self.deck.pop()
            self.hands[faction].append(alias)
            drawn.append(self.catalog[alias])
        excess = len(self.hands[faction]) + player.scored_secret_objectives - self.LIMIT
        if excess > 0:
            self.pending_returns[faction] = excess
        return tuple(drawn)

    def return_unscored(self, faction: str, alias: str) -> None:
        if self.pending_returns.get(faction, 0) < 1 or alias not in self.hands[faction]:
            raise ValueError('Choose an unscored objective that must return to the deck.')
        self.hands[faction].remove(alias)
        self.deck.append(alias)
        self.rng.shuffle(self.deck)
        remaining = self.pending_returns[faction] - 1
        if remaining:
            self.pending_returns[faction] = remaining
        else:
            del self.pending_returns[faction]

    def deal_starting_hands(self) -> None:
        """Deal the one starting secret objective once for each player."""
        for player in self.players.values():
            if not self.hands[player.faction] and not player.scored_secret_objectives:
                self.draw(player)
