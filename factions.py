"""Official base-game, Prophecy of Kings, and Codex III faction data."""

from __future__ import annotations

import json
from functools import lru_cache

from board import RESOURCES


@lru_cache(maxsize=1)
def faction_catalog() -> dict[str, dict]:
    factions = {}
    for filename in ('base.json', 'pok.json', 'keleres.json'):
        path = RESOURCES / 'data' / 'factions' / filename
        for faction in json.loads(path.read_text(encoding='utf-8')):
            if faction.get('source') in ('base', 'pok', 'codex3'):
                factions[faction['alias']] = faction
    return factions


KELERES_VARIANTS = frozenset({'keleresm', 'keleresx', 'keleresa'})
KELERES_PARENT = {'keleresm': 'mentak', 'keleresx': 'xxcha', 'keleresa': 'argent'}


def validate_faction_selection(aliases):
    """The Council Keleres use one unchosen faction's home system."""
    variants = KELERES_VARIANTS.intersection(aliases)
    if len(variants) > 1:
        raise ValueError('Only one Council Keleres variant may be played')
    if variants and KELERES_PARENT[next(iter(variants))] in aliases:
        raise ValueError('Council Keleres must use an unchosen faction home system')
