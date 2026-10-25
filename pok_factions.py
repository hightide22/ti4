"""Faction-specific PoK and Codex III abilities with controller-callable hooks."""

from __future__ import annotations

from uuid import uuid4

from leaders import refresh_leader_unlocks
from player import command_tokens_in_reinforcements
from units import Region, Unit, UnitLocation, unit_profile
from unit_supply import planet_structure_count


SLEEPER_TOKEN_SUPPLY = 5
PDS_SUPPLY = 6


def cabal_capture_unit(cabal, kind: str, original_owner: str) -> None:
    """Record a captured unit and retain its owner while it is on Cabal's sheet."""
    cabal.captured_units[kind] = cabal.captured_units.get(kind, 0) + 1
    # Fighter and infantry captures use unlimited supply tokens. Ships and
    # mechs leave a faction-specific plastic unit out of its owner's reserve.
    if kind not in ('fighter', 'infantry'):
        key = (kind, original_owner)
        sources = getattr(cabal, 'captured_unit_sources', None)
        if sources is None:
            cabal.captured_unit_sources = sources = {}
        sources[key] = sources.get(key, 0) + 1


def cabal_capture_destroyed(cabal, destroyed, *, during_combat: bool) -> bool:
    """Devour captures an enemy non-structure destroyed during combat."""
    if (cabal.faction != 'cabal' or not during_combat or
            destroyed.owner == cabal.faction or
            destroyed.kind in ('pds', 'spacedock')):
        return False
    cabal_capture_unit(cabal, destroyed.kind, destroyed.owner)
    return True


def cabal_return_captured(cabal, kind: str, original_owner: str | None = None) -> bool:
    """Spend one captured unit for Amalgamation or Riftmeld."""
    if cabal.faction != 'cabal' or cabal.captured_units.get(kind, 0) <= 0:
        return False
    cabal.captured_units[kind] -= 1
    if not cabal.captured_units[kind]:
        del cabal.captured_units[kind]
    sources = getattr(cabal, 'captured_unit_sources', {})
    candidates = sorted(owner for (unit_kind, owner), count in sources.items()
                        if unit_kind == kind and count > 0 and
                        (original_owner is None or owner == original_owner))
    if candidates:
        key = (kind, candidates[0])
        sources[key] -= 1
        if sources[key] <= 0:
            del sources[key]
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


def titans_awaken(player, board, tile, *, planet_ids: tuple[str, ...],
                  exploration=None):
    """Replace selected sleeper tokens that were present at activation with PDS units."""
    if player.faction != 'titans' or tile.position not in board:
        raise ValueError('Only Titans can awaken sleeper tokens')
    selected = set(planet_ids)
    if not selected <= tile.sleeper_tokens:
        raise ValueError('Sleeper token was not present at activation')
    planet_ids_on_tile = {planet.planet_id for planet in tile.planets}
    if not selected <= planet_ids_on_tile:
        raise ValueError('A sleeper token is not on a planet in this system')

    pds_count = sum(unit.owner == player.faction and unit.kind == 'pds'
                    for system in board.values() for unit in system.units)
    reserve = max(0, PDS_SUPPLY - pds_count)
    movable = sorted(
        ((system.position, unit.unit_id, system, unit)
         for system in board.values()
         if player.faction not in system.command_tokens
         for unit in system.units
         if unit.owner == player.faction and unit.kind == 'pds'),
        key=lambda item: (item[0], item[1]))
    planned = []
    reserved_for_move = set()
    pds_on_target = {
        planet_id: planet_structure_count(tile, planet_id, 'pds', player.faction)
        for planet_id in selected
    }
    for planet_id in sorted(selected):
        if exploration is not None and (
                exploration.planet_is_blocked(planet_id) or
                planet_id in exploration.destroyed_planets):
            continue
        # Each faction can have at most two PDS on a planet.
        if pds_on_target[planet_id] >= 2:
            continue
        if reserve:
            reserve -= 1
            planned.append((planet_id, None))
            pds_on_target[planet_id] += 1
            continue
        source = next((entry for entry in movable if entry[1] not in reserved_for_move and
                       entry[2] is not tile), None)
        if source is None:
            continue
        reserved_for_move.add(source[1])
        planned.append((planet_id, source))
        pds_on_target[planet_id] += 1

    if not planned:
        return ()

    from technology import upgrade_profile
    placed = []
    for planet_id, source in planned:
        tile.sleeper_tokens.remove(planet_id)
        if source is None:
            unit = Unit(f'{player.faction}-pds-{uuid4().hex}', 'pds', player.faction,
                        player.color_code, UnitLocation(Region.PLANET, planet_id),
                        profile_id=(upgrade_profile(player, 'pds') or {}).get('id'))
        else:
            _, _, source_tile, unit = source
            source_tile.units.remove(unit)
            unit.location = UnitLocation(Region.PLANET, planet_id)
            unit.damaged = False
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
