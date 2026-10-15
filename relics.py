"""Prophecy of Kings and Codex relic deck and ownership.

The controller deliberately keeps relic ownership separate from ``PlayerState``:
it is also the place where purged cards, exhaustion and relic-only victory
points live.  Callers can snapshot/restore it along with their turn undo state.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import random
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from board import RESOURCES


RELIC_DATA_FILES = ('pok.json', 'codexii.json')


@dataclass(frozen=True)
class RelicCard:
    alias: str
    name: str
    text: str
    source: str
    image_path: Path | None = None


def relic_catalog() -> dict[str, RelicCard]:
    cards = {}
    for filename in RELIC_DATA_FILES:
        path = RESOURCES / 'data/relics' / filename
        for data in json.loads(path.read_text(encoding='utf-8')):
            image_url = data.get('imageURL', '')
            image_path = (RESOURCES / unquote(image_url.split('/resources/', 1)[1].split('?', 1)[0])
                          if '/resources/' in image_url else None)
            card = RelicCard(data['alias'], data['name'], data['text'], data['source'],
                             image_path if image_path and image_path.is_file() else None)
            if card.alias in cards:
                raise ValueError(f'Duplicate relic alias: {card.alias}')
            cards[card.alias] = card
    return cards


@dataclass(frozen=True)
class RelicOutcome:
    alias: str | None
    events: tuple[str, ...] = ()


class RelicController:
    def __init__(self, players, *, rng=None, deck=None):
        self.catalog = relic_catalog()
        self.rng = rng or random.Random()
        self.deck = list(deck if deck is not None else self.catalog)
        if deck is None:
            self.rng.shuffle(self.deck)
        if len(set(self.deck)) != len(self.deck) or any(alias not in self.catalog for alias in self.deck):
            raise ValueError('Relic deck contains an unknown or duplicate card.')
        self.owned: dict[str, list[str]] = {player.faction: [] for player in players}
        self.exhausted: set[str] = set()
        self.purged: set[str] = set()
        self.victory_points: dict[str, int] = {player.faction: 0 for player in players}

    def snapshot(self):
        return deepcopy((self.deck, self.owned, self.exhausted,
                         self.purged, self.victory_points))

    def restore(self, snapshot):
        (self.deck, self.owned, self.exhausted,
         self.purged, self.victory_points) = deepcopy(snapshot)

    def cards(self, faction: str) -> tuple[RelicCard, ...]:
        return tuple(self.catalog[alias] for alias in self.owned[faction])

    def draw(self, faction: str) -> RelicOutcome:
        if faction not in self.owned:
            raise ValueError(f'Unknown faction: {faction}')
        if not self.deck:
            return RelicOutcome(None)
        alias = self.deck.pop()
        self.owned[faction].append(alias)
        events = []
        if alias == 'shard':
            self.victory_points[faction] += 1
            events.append('Gain 1 victory point from Shard of the Throne.')
        elif alias == 'obsidian':
            events.append('Draw 1 secret objective; your secret objective limit increases by 1.')
        elif alias == 'dynamiscore':
            events.append('Commodity value increases by 2 while Dynamis Core is owned.')
        return RelicOutcome(alias, tuple(events))

    def owner(self, alias: str) -> str | None:
        return next((faction for faction, aliases in self.owned.items()
                     if alias in aliases), None)

    def is_ready(self, faction: str, alias: str) -> bool:
        return alias in self.owned[faction] and alias not in self.exhausted

    def exhaust(self, faction: str, alias: str) -> None:
        if not self.is_ready(faction, alias):
            raise ValueError('Relic is not ready or not owned by this player.')
        self.exhausted.add(alias)

    def ready_all(self) -> None:
        self.exhausted.clear()

    def purge(self, faction: str, alias: str) -> None:
        if alias not in self.owned[faction]:
            raise ValueError('Relic is not owned by this player.')
        self.owned[faction].remove(alias)
        self.exhausted.discard(alias)
        self.purged.add(alias)
        if alias == 'shard':
            self.victory_points[faction] -= 1

    def transfer_shard(self, old_faction: str, new_faction: str) -> bool:
        """Transfer Shard when its owner loses a home/legendary planet."""
        if old_faction == new_faction or 'shard' not in self.owned[old_faction]:
            return False
        self.owned[old_faction].remove('shard')
        self.owned[new_faction].append('shard')
        self.victory_points[old_faction] -= 1
        self.victory_points[new_faction] += 1
        return True

    def commodity_limit(self, player) -> int:
        return player.commodity_limit + (2 if 'dynamiscore' in self.owned[player.faction] else 0)

    def use_dynamis_core(self, player) -> RelicOutcome:
        if 'dynamiscore' not in self.owned[player.faction]:
            raise ValueError('Dynamis Core is not owned by this player.')
        gained = self.commodity_limit(player)
        self.purge(player.faction, 'dynamiscore')
        player.trade_goods += gained
        return RelicOutcome('dynamiscore', (f'Gain {gained} trade goods.',))

    def use_emelpar(self, faction: str, reinforcements: int) -> bool:
        """Pay one strategic-pool cost from reinforcements without gaining a token."""
        if reinforcements < 1 or not self.is_ready(faction, 'emelpar'):
            return False
        self.exhaust(faction, 'emelpar')
        return True

    def use_codex(self, player, action_cards, aliases: tuple[str, ...]) -> RelicOutcome:
        if 'codex' not in self.owned[player.faction]:
            raise ValueError('The Codex is not owned by this player.')
        if len(aliases) > 3 or Counter(aliases) - Counter(action_cards.discard):
            raise ValueError('Choose up to three cards from the action card discard.')
        # A Codex draw can exceed the seven-card hand limit; the caller must
        # then let the player discard down to that limit.
        for alias in aliases:
            action_cards.discard.remove(alias)
            player.action_cards.append(alias)
        self.purge(player.faction, 'codex')
        return RelicOutcome('codex', (f'Recover {len(aliases)} action cards.',))

    def use_dominus_orb(self, faction: str) -> RelicOutcome:
        """Open its one-tactical-action movement exception for the caller."""
        self.purge(faction, 'dominusorb')
        return RelicOutcome('dominusorb', ('Ships may move from systems containing your command tokens this tactical action.',))

    def use_maw_of_worlds(self, player, board, technology_alias: str) -> RelicOutcome:
        """Agenda-window ability; the turn controller must validate timing."""
        if 'mawofworlds' not in self.owned[player.faction]:
            raise ValueError('Maw of Worlds is not owned by this player.')
        from technology import copy_technology
        # Research first so an invalid selection leaves planets and relic intact.
        tech = copy_technology(player, technology_alias, board)
        for card in player.planets:
            card.exhausted = True
        self.purge(player.faction, 'mawofworlds')
        return RelicOutcome('mawofworlds', (f'Research {tech["name"]}.',))

    def use_prophets_tears(self, player, mode: str, action_cards=None) -> RelicOutcome:
        """Research-window ability; caller applies an ignored prerequisite."""
        if mode not in {'ignore_prerequisite', 'draw_action_card'}:
            raise ValueError('Choose one Prophet’s Tears ability.')
        if not self.is_ready(player.faction, 'prophetstears'):
            raise ValueError('The Prophet’s Tears are not ready.')
        if mode == 'draw_action_card' and action_cards is None:
            raise ValueError('An action-card deck is required.')
        self.exhaust(player.faction, 'prophetstears')
        if mode == 'draw_action_card':
            drawn = action_cards.draw(player, 1)
            return RelicOutcome('prophetstears', (f'Draw {len(drawn)} action cards.',))
        return RelicOutcome('prophetstears', ('Ignore one prerequisite for this research.',))

    def score_crown_of_emphidia(self, faction: str, tomb_controlled: bool) -> bool:
        """Status-phase optional scoring of the Tomb of Emphidia."""
        if not tomb_controlled or 'emphidia' not in self.owned[faction]:
            return False
        self.purge(faction, 'emphidia')
        self.victory_points[faction] += 1
        return True
