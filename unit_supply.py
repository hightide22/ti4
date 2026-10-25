"""Shared checks for infantry plastic-piece anchors and token groups."""

from collections import Counter

from units import Region

INFANTRY_SUPPLY = 12


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
