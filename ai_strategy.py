"""Choose strategic-action targets from public logistics and frontier threats."""
from ai_actions import UNIT_VALUE
from units import Region, UNIT_TYPES, mobile_ship, unit_profile
from unit_supply import planet_structure_count


def ships(tile, faction, *, enemy=False):
    return [unit for unit in tile.units if (unit.owner != faction if enemy else unit.owner == faction)
            and unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]


def mobile_units(tile, faction):
    return [unit for unit in tile.units if unit.owner == faction and
            unit.location.region == Region.SPACE and mobile_ship(unit)]


def frontier(ai, tile, player):
    neighbors = ai.window.movement.neighbors(tile, player.faction)
    danger, opportunities = 0.0, 0.0
    for other in neighbors:
        for unit in ships(other, player.faction, enemy=True):
            if unit.owner not in other.command_tokens:
                danger += UNIT_VALUE.get(unit.kind, 1)
        opportunities += sum(1 + planet.resources + .4 * planet.influence
                             for planet in other.planets
                             if other.planet_owners.get(planet.planet_id) != player.faction)
    return danger, opportunities


def diplomacy_value(ai, tile, player):
    danger, _ = frontier(ai, tile, player)
    assets = sum(2 + planet.resources + .5 * planet.influence for planet in tile.planets
                 if tile.planet_owners.get(planet.planet_id) == player.faction)
    assets += 4 * sum(unit.owner == player.faction and unit.kind == 'spacedock'
                      for unit in tile.units)
    defense = sum(UNIT_VALUE.get(unit.kind, 1) for unit in ships(tile, player.faction))
    return (assets * (1 + min(3, max(0, danger - .5 * defense))), tile.position)


def warfare_value(ai, tile, player):
    _, opportunities = frontier(ai, tile, player)
    fleet = ships(tile, player.faction)
    mobile = mobile_units(tile, player.faction)
    firepower = sum(UNIT_VALUE.get(unit.kind, 1) for unit in fleet)
    infantry = sum(unit.owner == player.faction and unit.kind == 'infantry' for unit in tile.units)
    capacity = sum(unit.capacity for unit in mobile)
    expedition = min(capacity, infantry, 4)
    # A forward fleet gets another operation; an empty rich planet does not.
    score = firepower + expedition * 3 + min(12, opportunities) * bool(mobile)
    if any(unit.owner == player.faction and unit_profile(unit).get('productionValue')
           for unit in tile.units):
        score += min(5, player.available_values[0] + player.trade_goods)
    return score, tile.position


def construction_choice(ai, controller, session):
    player = session.player
    board = ai.window.board
    docks = [tile for tile in board.values() if any(
        unit.owner == player.faction and unit.kind == 'spacedock' for unit in tile.units)]
    options = []
    # Only the second PRIMARY build is restricted to a PDS.
    kinds = ('pds',) if session.primary and session.builds_left == 1 else ('spacedock', 'pds')
    for kind in kinds:
        session.structure = kind
        for card in controller.buildable_planets(selected_only=False):
            tile = controller.planet_system(card.planet.planet_id)
            danger, opportunity = frontier(ai, tile, player)
            fleet = ships(tile, player.faction)
            blocked = bool(ships(tile, player.faction, enemy=True))
            if kind == 'spacedock':
                neighbors = {t.position for t in ai.window.movement.neighbors(tile, player.faction)}
                nearby = sum(dock.position in neighbors or dock is tile for dock in docks)
                score = (12 if not docks else 6) + 2 * card.planet.resources
                score += min(10, opportunity) - 7 * nearby - 3 * len(docks)
                score -= 20 * blocked + max(0, danger - sum(UNIT_VALUE.get(u.kind, 1) for u in fleet))
                if player.faction == 'saar':
                    score += 10  # Mobile transport and production, independent of planet resources.
            else:
                count = planet_structure_count(tile, card.planet.planet_id, 'pds', player.faction)
                score = (2 + card.planet.resources + min(12, danger) +
                         3 * any(dock is tile for dock in docks) - 4 * count)
                if 'pds2' in player.technologies:
                    score += min(6, opportunity)
            if not session.primary and player.faction not in tile.command_tokens:
                score -= min(16, sum(UNIT_VALUE.get(unit.kind, 1)
                                    for unit in mobile_units(tile, player.faction)) * 2)
            options.append((score, kind, card.planet.planet_id, tile.position))
    return max(options) if options else None
