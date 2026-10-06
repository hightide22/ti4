from __future__ import annotations

from dataclasses import dataclass, field
import json

from board import Planet, RESOURCES


@dataclass
class PlanetCard:
    planet: Planet
    exhausted: bool = False

    def flip(self):
        self.exhausted = not self.exhausted


@dataclass
class PlayerState:
    faction: str
    name: str
    color_code: str
    commodity_limit: int
    planets: list[PlanetCard] = field(default_factory=list)
    trade_goods: int = 0
    commodities: int = 0
    pending_commands: int = 0
    command_pools: dict[str, int] = field(default_factory=lambda: {
        'tactical': 3, 'fleet': 3, 'strategic': 2})
    technologies: frozenset[str] = frozenset()

    def change_currency(self, currency, amount):
        if currency not in ('trade_goods', 'commodities'):
            raise ValueError('Unknown currency')
        value = max(0, getattr(self, currency) + amount)
        if currency == 'commodities':
            value = min(self.commodity_limit, value)
        setattr(self, currency, value)

    def transfer_command(self, source, target):
        if source not in self.command_pools or target not in self.command_pools:
            raise ValueError('Unknown command pool')
        if source == target or not self.command_pools[source]:
            return False
        self.command_pools[source] -= 1
        self.command_pools[target] += 1
        return True

    def round_command_gain(self):
        return 3 if self.faction == 'sol' else 2

    def receive_round_commands(self):
        self.pending_commands += self.round_command_gain()

    def allocate_command(self, target):
        if target not in self.command_pools:
            raise ValueError('Unknown command pool')
        if not self.pending_commands:
            return False
        self.pending_commands -= 1
        self.command_pools[target] += 1
        return True

    @property
    def available_values(self):
        ready = [card.planet for card in self.planets if not card.exhausted]
        return sum(p.resources for p in ready), sum(p.influence for p in ready)


def create_players(board, config):
    factions = {f['alias']: f for f in json.loads(
        (RESOURCES / 'data/factions/base.json').read_text(encoding='utf-8'))}
    players = []
    for entry in config['tiles']:
        alias = entry.get('faction')
        if alias:
            faction = factions[alias]
            tile = board[(entry['q'], entry['r'])]
            tile.planet_owners.update({planet.planet_id: alias for planet in tile.planets})
            players.append(PlayerState(alias, faction['factionName'], entry['unit_color'],
                                       faction['commodities'], [PlanetCard(p) for p in tile.planets],
                                       technologies=frozenset(faction['startingTech'])))
    return players
