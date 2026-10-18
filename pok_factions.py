"""Faction-specific PoK and Codex III abilities with controller-callable hooks."""

from __future__ import annotations

from uuid import uuid4

from leaders import refresh_leader_unlocks
from player import command_tokens_in_reinforcements
from units import Region, Unit, UnitLocation, unit_profile


SLEEPER_TOKEN_SUPPLY = 5
PDS_SUPPLY = 6


def cabal_capture_destroyed(cabal, destroyed, *, during_combat: bool) -> bool:
    """Devour captures an enemy non-structure destroyed during combat."""
    if (cabal.faction != 'cabal' or not during_combat or
            destroyed.owner == cabal.faction or
            destroyed.kind in ('pds', 'spacedock')):
        return False
    cabal.captured_units[destroyed.kind] = cabal.captured_units.get(destroyed.kind, 0) + 1
    return True


def cabal_return_captured(cabal, kind: str) -> bool:
    """Spend one captured unit for Amalgamation or Riftmeld."""
    if cabal.faction != 'cabal' or cabal.captured_units.get(kind, 0) <= 0:
        return False
    cabal.captured_units[kind] -= 1
    if not cabal.captured_units[kind]:
        del cabal.captured_units[kind]
    return True


def argent_raid_targets(afb_hits: int, enemy_fighters: int, enemy_ships) -> tuple:
    """Each excess AFB hit may damage one intact sustain ship."""
    extra = max(0, afb_hits - enemy_fighters)
    return tuple(ship for ship in enemy_ships if unit_profile(ship).get('sustainDamage')
                 and not ship.damaged)[:extra]


def mahact_gain_fleet_token(mahact, defeated, board) -> bool:
    """Edict takes one defeated player's token from reinforcements."""
    if (mahact.faction != 'mahact' or defeated.faction == mahact.faction or
            defeated.faction in mahact.foreign_fleet_tokens or
            command_tokens_in_reinforcements(defeated, board) < 1):
        return False
    mahact.foreign_fleet_tokens.add(defeated.faction)
    defeated.foreign_command_tokens_out.add(mahact.faction)
    refresh_leader_unlocks(mahact, board)
    return True


def mahact_fleet_limit(player) -> int:
    return player.command_pools['fleet'] + len(player.foreign_fleet_tokens)


def empyrean_ignores_nebula(player) -> bool:
    return player.faction == 'empyrean'


def naaz_explore_planet(player, exploration, tile, planet_id: str,
                       *, auto_resolve=False):
    """Distant Suns offers two exploration cards when a mech is on the planet."""
    if player.faction != 'naaz':
        raise ValueError('Distant Suns belongs to the Naaz-Rokha Alliance')
    has_mech = any(unit.owner == player.faction and unit.kind == 'mech' and
                   unit.location.region == Region.PLANET and
                   unit.location.planet_id == planet_id for unit in tile.units)
    return exploration.explore_planet(player, tile, planet_id,
                                      draw_count=2 if has_mech else 1,
                                      auto_resolve=auto_resolve)


def naaz_fabrication(player, exploration, fragment_ids: tuple[str, ...],
                     *, board=None, command_pool=None):
    """Fabrication trades one fragment for a command or two for a relic."""
    if player.faction != 'naaz':
        raise ValueError('Only the Naaz-Rokha Alliance can use Fabrication')
    if command_pool is None:
        if len(fragment_ids) != 2:
            raise ValueError('Select two fragments for a relic')
        return exploration.purge_fragments_for_relic(player.faction, fragment_ids)
    if (len(fragment_ids) != 1 or command_pool not in player.command_pools or
            board is None or command_tokens_in_reinforcements(player, board) < 1):
        raise ValueError('Select one fragment and a command pool')
    inventory = exploration.fragments[player.faction]
    if fragment_ids[0] not in inventory:
        raise ValueError('Fragment is not owned')
    inventory.remove(fragment_ids[0])
    player.command_pools[command_pool] += 1
    return None


def titans_place_sleeper(player, board, tile, planet_id: str,
                        *, move_from_planet_id: str | None = None) -> None:
    """Terragenesis places or relocates one of five sleeper tokens."""
    if player.faction != 'titans' or tile.position not in board:
        raise ValueError('Only Titans can place a sleeper token on this board')
    if (planet_id not in {planet.planet_id for planet in tile.planets} or
            planet_id in tile.sleeper_tokens):
        raise ValueError('Choose an explored planet without a sleeper')
    placed = [(candidate, sleeper) for candidate in board.values()
              for sleeper in candidate.sleeper_tokens]
    if move_from_planet_id is not None:
        source = next((candidate for candidate, sleeper in placed
                       if sleeper == move_from_planet_id), None)
        if source is None:
            raise ValueError('That sleeper token does not exist')
        source.sleeper_tokens.remove(move_from_planet_id)
    elif len(placed) >= SLEEPER_TOKEN_SUPPLY:
        raise ValueError('All sleeper tokens are on the board; choose one to move')
    tile.sleeper_tokens.add(planet_id)


def titans_awaken(player, board, tile, *, planet_ids: tuple[str, ...] | None = None):
    """Replace sleeper tokens present at activation with PDS units."""
    if player.faction != 'titans' or tile.position not in board:
        raise ValueError('Only Titans can awaken sleeper tokens')
    selected = set(tile.sleeper_tokens if planet_ids is None else planet_ids)
    if not selected <= tile.sleeper_tokens:
        raise ValueError('Sleeper token was not present at activation')
    available = PDS_SUPPLY - sum(unit.owner == player.faction and unit.kind == 'pds'
                                 for system in board.values() for unit in system.units)
    if len(selected) > available:
        raise ValueError('No PDS remains in reinforcements')
    placed = []
    for planet_id in selected:
        tile.sleeper_tokens.remove(planet_id)
        unit = Unit(f'{player.faction}-pds-{uuid4().hex}', 'pds', player.faction,
                    player.color_code, UnitLocation(Region.PLANET, planet_id))
        tile.units.append(unit)
        placed.append(unit)
    refresh_leader_unlocks(player, board)
    return tuple(placed)


def keleres_council_patronage(player, exploration=None) -> None:
    """At strategy phase start, replenish commodities and gain one trade good."""
    if not player.faction.startswith('keleres'):
        raise ValueError('Only Council Keleres receives Council Patronage')
    player.commodities = (exploration.relics.commodity_limit(player)
                          if exploration is not None else player.commodity_limit)
    player.trade_goods += 1


def titans_hero_space_cannon(player, tile) -> tuple[int, int] | None:
    """Ul's Elysium attachment fires three dice hitting on five or higher."""
    if (player.faction == 'titans' and 'elysium' in player.hero_attachments and
            tile.planet_owners.get('elysium') == player.faction):
        return 5, 3
    return None
