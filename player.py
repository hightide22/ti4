from __future__ import annotations

from dataclasses import dataclass, field
import json

from board import Planet, RESOURCES


COMMAND_TOKEN_SUPPLY = 16


def command_tokens_in_play(player, board):
    on_sheet = sum(player.command_pools.values()) + player.pending_commands
    on_board = sum(player.faction in tile.command_tokens for tile in board.values())
    return on_sheet + on_board + len(player.foreign_command_tokens_out)


def command_tokens_in_reinforcements(player, board):
    return max(0, COMMAND_TOKEN_SUPPLY - command_tokens_in_play(player, board))


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
    leaders: dict = field(default_factory=dict)
    scored_objectives: int = 0
    scored_secret_objectives: int = 0
    foreign_fleet_tokens: set[str] = field(default_factory=set)
    foreign_command_tokens_out: set[str] = field(default_factory=set)
    captured_units: dict[str, int] = field(default_factory=dict)
    hero_attachments: set[str] = field(default_factory=set)
    hero_round_flags: set[str] = field(default_factory=set)
    command_pools: dict[str, int] = field(default_factory=lambda: {
        'tactical': 3, 'fleet': 3, 'strategic': 2})
    technologies: frozenset[str] = frozenset()
    exhausted_technologies: set[str] = field(default_factory=set)
    infantry_on_cards: dict[str, int] = field(default_factory=dict)
    action_cards: list[str] = field(default_factory=list)
    exhausted_legendary_abilities: set[str] = field(default_factory=set)

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
        return (3 if 'hm' in self.technologies else 2) + (1 if self.faction == 'sol' else 0)

    def receive_round_commands(self, supply=16):
        on_sheet = (sum(self.command_pools.values()) + self.pending_commands +
                    len(self.foreign_command_tokens_out))
        self.pending_commands += min(self.round_command_gain(), max(0, supply - on_sheet))

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
        return (sum(p.resources + (3 if p.planet_id in self.hero_attachments else 0)
                    for p in ready),
                sum(p.influence + (3 if p.planet_id in self.hero_attachments else 0)
                    for p in ready))


def create_players(board, config):
    from factions import faction_catalog
    from leaders import starting_leaders, refresh_leader_unlocks
    factions = faction_catalog()
    players = []
    if config.get('player_slots'):
        entries_by_position = {(entry['q'], entry['r']): entry for entry in config['tiles']}
        player_entries = [entries_by_position[tuple(position)] for position in config['player_slots']]
    else:
        player_entries = [entry for entry in config['tiles'] if entry.get('faction')]
    for entry in player_entries:
        alias = entry.get('faction')
        if alias:
            faction = factions[alias]
            starting_techs = list(faction.get('startingTech') or ())
            if not starting_techs and faction.get('startingTechOptions'):
                from technology import technology_catalog
                catalog = technology_catalog()
                available = {tech for tech in faction['startingTechOptions']
                             if tech in catalog and catalog[tech].get('source') in ('base', 'pok') and
                             catalog[tech].get('faction') is None}
                preference = ('st', 'det', 'amd', 'ps', 'pa', 'nm', 'sdn')
                amount = int(faction.get('startingTechAmount') or 0)
                chosen = (config.get('starting_technology_choices') or {}).get(alias)
                if chosen is not None:
                    if len(chosen) != amount or len(set(chosen)) != amount or \
                            not set(chosen) <= available:
                        raise ValueError(f'Invalid starting technologies for {alias}')
                    starting_techs = list(chosen)
                else:
                    starting_techs = [tech for tech in preference if tech in available][:amount]
            tile = next((candidate for candidate in board.values()
                         if any(planet.faction_homeworld == alias for planet in candidate.planets)), None)
            if tile is None:
                tile = board[(entry['q'], entry['r'])]
            tile.planet_owners.update({planet.planet_id: alias for planet in tile.planets})
            players.append(PlayerState(alias, faction['factionName'], entry['unit_color'],
                                       faction['commodities'], [PlanetCard(p) for p in tile.planets],
                                       technologies=frozenset(starting_techs),
                                       leaders=starting_leaders(alias)))
    # Codex III Keleres copy two distinct non-faction starting technologies
    # owned by other players after the entire table has been set up.
    if any(player.faction.startswith('keleres') for player in players):
        from technology import technology_catalog
        catalog = technology_catalog()
        for player in players:
            if not player.faction.startswith('keleres'):
                continue
            choices = sorted({tech_id for other in players if other is not player
                              for tech_id in other.technologies
                              if tech_id in catalog and not catalog[tech_id].get('faction')})
            chosen = (config.get('starting_technology_choices') or {}).get(player.faction)
            if chosen is not None:
                if len(chosen) != 2 or len(set(chosen)) != 2 or not set(chosen) <= set(choices):
                    raise ValueError('Keleres must choose two other players’ non-faction technologies')
                player.technologies = frozenset(chosen)
            else:
                player.technologies = frozenset(choices[:2])
    for player in players:
        refresh_leader_unlocks(player, board, players)
    return players
