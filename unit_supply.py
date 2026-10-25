"""Shared checks for infantry plastic-piece anchors and token groups."""

from collections import Counter

from units import Region

INFANTRY_SUPPLY = 12
FIGHTER_SUPPLY = 10

# Plastic component limits from the TI4 Rules Reference. Fighters and infantry
# use their token rules below instead of a per-unit board limit.
PLASTIC_UNIT_LIMITS = {
    'carrier': 4,
    'cruiser': 8,
    'destroyer': 8,
    'dreadnought': 5,
    'flagship': 1,
    'warsun': 2,
    'mech': 4,
    'pds': 6,
    'spacedock': 3,
}


def unit_count(player, board, kind):
    faction = player if isinstance(player, str) else player.faction
    return sum(unit.owner == faction and unit.kind == kind
               for tile in board.values() for unit in tile.units)


def unit_supply_remaining(player, board, kind):
    limit = PLASTIC_UNIT_LIMITS.get(kind)
    return None if limit is None else max(0, limit - unit_count(player, board, kind))


def planet_structure_count(tile, planet_id, kind, owner=None):
    """Count structures on a planet, optionally restricted to one player.

    Both PDS and space-dock limits are per player and planet. Keeping this
    query explicit helps placement options and resolution enforce the same
    board limits.
    """
    return sum(unit.kind == kind and unit.location.region == Region.PLANET and
               unit.location.planet_id == planet_id and
               (owner is None or unit.owner == owner)
               for unit in tile.units)


def can_place_structure(tile, planet_id, faction, kind):
    if kind == 'pds':
        return planet_structure_count(tile, planet_id, kind, faction) < 2
    if kind == 'spacedock':
        return planet_structure_count(tile, planet_id, kind, faction) < 1
    return False


def fighter_area(tile, location):
    """Return the system whose plastic fighter can anchor its fighter tokens."""
    if location.region in (Region.SPACE, Region.TRANSPORT):
        return tile.position
    return None


def fighter_area_counts(player, board):
    faction = player if isinstance(player, str) else player.faction
    areas = set()
    for tile in board.values():
        if any(unit.owner == faction and unit.kind == 'fighter' and
               fighter_area(tile, unit.location) is not None for unit in tile.units):
            areas.add(tile.position)
    return areas


def can_transfer_fighters(player, board, transfers):
    """Keep fighter tokens anchored while units move between systems.

    Each transfer is ``(source_tile, unit, destination_tile, destination_location)``.
    A fighter carried between systems counts as being in the destination system.
    """
    faction = player if isinstance(player, str) else player.faction
    counts = Counter()
    for tile in board.values():
        for unit in tile.units:
            if unit.owner == faction and unit.kind == 'fighter':
                area = fighter_area(tile, unit.location)
                if area is not None:
                    counts[area] += 1
    transferred_ids = set()
    for source, unit, destination, location in transfers:
        if unit.owner != faction or unit.kind != 'fighter':
            continue
        if (unit.unit_id in transferred_ids or
                sum(candidate is unit for candidate in source.units) != 1):
            return False
        transferred_ids.add(unit.unit_id)
        source_area = fighter_area(source, unit.location)
        destination_area = fighter_area(destination, location)
        if source_area is None or destination_area is None or counts[source_area] <= 0:
            return False
        counts[source_area] -= 1
        if counts[source_area] == 0:
            del counts[source_area]
        counts[destination_area] += 1
    return len(counts) <= FIGHTER_SUPPLY


def fighter_areas_remaining(player, board):
    return max(0, FIGHTER_SUPPLY - len(fighter_area_counts(player, board)))


def can_place_fighter(player, board, tile, location):
    """Tokens may be unlimited, but each fighter system needs one of 10 anchors."""
    area = fighter_area(tile, location)
    if area is None:
        return False
    occupied = fighter_area_counts(player, board)
    return area in occupied or len(occupied) < FIGHTER_SUPPLY


def infantry_area(tile, location):
    """Return the board area that needs a plastic infantry anchor.

    Infantry tokens may stack without limit, but every planet or system space
    area containing them needs at least one plastic infantry. Carried infantry
    and infantry in a system's space area share the same anchor.
    """
    if location.region == Region.PLANET:
        return tile.position, 'planet', location.planet_id
    if location.region in (Region.TRANSPORT, Region.SPACE_AREA, Region.SPACE):
        return tile.position, 'space', None
    return None


def infantry_area_counts(player, board):
    faction = player if isinstance(player, str) else player.faction
    areas = {}
    for tile in board.values():
        for unit in tile.units:
            if unit.owner != faction or unit.kind != 'infantry':
                continue
            key = infantry_area(tile, unit.location)
            if key is not None:
                areas[key] = areas.get(key, 0) + 1
    return areas


def infantry_count(player, board):
    """Count infantry units on the board; extra units are represented by tokens."""
    faction = player if isinstance(player, str) else player.faction
    return sum(unit.owner == faction and unit.kind == 'infantry'
               for tile in board.values() for unit in tile.units)


def infantry_remaining(player, board):
    """Return unused plastic infantry anchors, not the number of infantry tokens."""
    return max(0, INFANTRY_SUPPLY - len(infantry_area_counts(player, board)))


def can_place_infantry(player, board, tile, location):
    """Whether a unit/token can be placed while respecting the 12 plastic anchors."""
    key = infantry_area(tile, location)
    if key is None:
        return False
    occupied = infantry_area_counts(player, board)
    return key in occupied or len(occupied) < INFANTRY_SUPPLY


def can_transfer_infantry(player, board, transfers):
    """Check a batch of moves for newly unanchored or excess infantry areas.

    Each transfer is ``(source_tile, unit, destination_tile, destination_location)``.
    The whole batch is evaluated together so moving the last infantry out of one
    area can free its plastic anchor for a different area in the same effect.
    """
    areas = Counter(infantry_area_counts(player, board))
    faction = player if isinstance(player, str) else player.faction
    transferred_ids = set()
    for source, unit, destination, location in transfers:
        if unit.owner != faction or unit.kind != 'infantry':
            continue
        if unit.unit_id in transferred_ids or not any(candidate is unit for candidate in source.units):
            return False
        transferred_ids.add(unit.unit_id)
        source_key = infantry_area(source, unit.location)
        destination_key = infantry_area(destination, location)
        if source_key is None or destination_key is None or areas[source_key] <= 0:
            return False
        areas[source_key] -= 1
        if areas[source_key] <= 0:
            del areas[source_key]
        areas[destination_key] += 1
    return len(areas) <= INFANTRY_SUPPLY
