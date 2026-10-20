"""Leader cards, availability, and commander unlock conditions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from board import RESOURCES
from units import Region, unit_profile


@dataclass
class LeaderState:
    unlocked: bool = False
    exhausted: bool = False
    purged: bool = False
    stored_trade_goods: int = 0


@lru_cache(maxsize=1)
def leader_catalog() -> dict[str, dict]:
    path = RESOURCES / 'data/leaders/pok.json'
    return {entry['id']: entry for entry in json.loads(path.read_text(encoding='utf-8'))
            if entry.get('source') in ('base', 'pok', 'codex3', 'codex4')}


def starting_leaders(faction: str) -> dict[str, LeaderState]:
    from factions import faction_catalog
    return {leader_id: LeaderState(unlocked=leader_catalog()[leader_id]['type'] == 'agent')
            for leader_id in faction_catalog()[faction].get('leaders', ())
            if leader_id in leader_catalog()}


def leader_available(player, leader_id: str) -> bool:
    state = player.leaders.get(leader_id)
    return bool(state and state.unlocked and not state.exhausted and not state.purged)


def unlock_leader(player, leader_id: str) -> bool:
    state = player.leaders.get(leader_id)
    if state is None or state.unlocked or state.purged:
        return False
    state.unlocked = True
    return True


def exhaust_leader(player, leader_id: str) -> bool:
    if (not leader_available(player, leader_id) or
            leader_catalog()[leader_id]['type'] != 'agent'):
        return False
    player.leaders[leader_id].exhausted = True
    return True


def purge_leader(player, leader_id: str) -> bool:
    if (not leader_available(player, leader_id) or
            leader_catalog()[leader_id]['type'] != 'hero'):
        return False
    player.leaders[leader_id].purged = True
    return True


def ready_leaders(player) -> None:
    """Status-phase ready: purged heroes remain unavailable."""
    for state in player.leaders.values():
        if not state.purged:
            state.exhausted = False
            if state.stored_trade_goods:
                player.trade_goods += state.stored_trade_goods
                state.stored_trade_goods = 0


def record_scored_objective(player, *, secret: bool = False) -> None:
    player.scored_objectives += 1
    if secret:
        player.scored_secret_objectives += 1
    if player.scored_objectives >= 3:
        for leader_id in player.leaders:
            if leader_catalog()[leader_id]['type'] == 'hero':
                unlock_leader(player, leader_id)
    if secret:
        unlock_leader(player, 'nomadcommander')


def notify_leader_event(player, event: str) -> None:
    """Unlock conditions that cannot be read from the current board position."""
    leader = {
        'produced_war_sun': 'muaatcommander',
        'mecatol_combat': 'winnucommander',
        'used_faction_ability': 'yincommander',
        'keleres_action_card_trade_good': 'kelerescommander',
    }.get(event)
    if leader:
        unlock_leader(player, leader)


def refresh_leader_unlocks(player, board, players=()) -> list[str]:
    """Unlock commanders whose printed board-state requirements are now met."""
    if not player.leaders:
        return []
    faction = player.faction
    units = [(tile, unit) for tile in board.values() for unit in tile.units
             if unit.owner == faction]
    owned_planets = [(tile, planet) for tile in board.values() for planet in tile.planets
                     if tile.planet_owners.get(planet.planet_id) == faction]
    unit_tiles = {tile.position for tile, _ in units}
    owned_tiles = set(unit_tiles)
    owned_tiles.update(tile.position for tile, _ in owned_planets)
    unit_count = lambda kind: sum(unit.kind == kind for _, unit in units)
    checks = {
        'arboreccommander': lambda: sum(unit.kind in ('infantry', 'mech') and
                                      unit.location.region == Region.PLANET and
                                      tile.planet_owners.get(unit.location.planet_id) == faction
                                      for tile, unit in units) >= 12,
        'argentcommander': lambda: sum(any(unit_profile(unit).get(key)
                                              for key in ('afbHitsOn', 'spaceCannonHitsOn',
                                                          'bombardHitsOn')) for _, unit in units) >= 6,
        'cabalcommander': lambda: sum(tile.is_gravity_rift and tile.position in unit_tiles
                                     for tile in board.values()) >= 3,
        'ghostcommander': lambda: sum(bool({'ALPHA', 'BETA'} & set(tile.wormholes)) and
                                      tile.position in owned_tiles for tile in board.values()) >= 3,
        'empyreancommander': lambda: bool(players) and all(
            other is player or any(
                neighbor.position in {occupied.position for occupied in board.values()
                                      if any(unit.owner == other.faction for unit in occupied.units) or
                                      other.faction in occupied.planet_owners.values()}
                for own in board.values() if own.position in owned_tiles
                for neighbor in board.neighbors(own.position))
            for other in players),
        'hacancommander': lambda: player.trade_goods >= 10,
        'jolnarcommander': lambda: len(player.technologies) >= 8,
        'l1z1xcommander': lambda: unit_count('dreadnought') >= 4,
        'letnevcommander': lambda: any(sum(unit.kind != 'fighter' and
                                         unit.location.region == Region.SPACE for unit in tile.units
                                         if unit.owner == faction) >= 5 for tile in board.values()),
        'mahactcommander': lambda: len(player.foreign_fleet_tokens) >= 2,
        'mentakcommander': lambda: unit_count('cruiser') >= 4,
        'naalucommander': lambda: any(tile.number == 18 or
                                    any(neighbor.number == 18 for neighbor in board.neighbors(tile.position))
                                    for tile, unit in units if unit.kind in ('infantry', 'mech')),
        'naazcommander': lambda: len({tile.position for tile, unit in units
                                     if unit.kind == 'mech'}) >= 3,
        'nekrocommander': lambda: len(player.technologies) >= 3,
        'nomadcommander': lambda: player.scored_secret_objectives >= 1,
        'saarcommander': lambda: unit_count('spacedock') >= 3,
        'sardakkcommander': lambda: sum(tile.definition.is_home_system is False
                                      for tile, _ in owned_planets) >= 5,
        'solcommander': lambda: sum(planet.resources for _, planet in owned_planets) >= 12,
        'titanscommander': lambda: sum(unit.kind in ('pds', 'spacedock')
                                     for _, unit in units) >= 5,
        'winnucommander': lambda: any(tile.number == 18 for tile, _ in owned_planets),
        'xxchacommander': lambda: sum(planet.influence for _, planet in owned_planets) >= 12,
        'yssarilcommander': lambda: len(player.action_cards) >= 7,
    }
    unlocked = []
    for leader_id in player.leaders:
        if leader_id in checks and checks[leader_id]() and unlock_leader(player, leader_id):
            unlocked.append(leader_id)
        if leader_catalog()[leader_id]['type'] == 'hero' and player.scored_objectives >= 3:
            if unlock_leader(player, leader_id):
                unlocked.append(leader_id)
    return unlocked
