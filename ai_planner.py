"""Legal, reproducible tactical plans for the automated players.

The forecast and the eventual movement use the same ship and passenger IDs.
This matters for Gravity Drive, fleet supply, and attacks assembled from more
than one system.
"""
from __future__ import annotations

from collections import defaultdict
from copy import copy
from types import SimpleNamespace
from typing import NamedTuple

from ai_combat import win_probability
from action_cards import canonical_action_card
from movement import capital_ship, cargo_cost
from units import Region, UNIT_TYPES, UnitLocation, unit_profile


SHIP_VALUE = {'fighter': .8, 'destroyer': 1.4, 'cruiser': 2.4,
              'carrier': 1.7, 'dreadnought': 4.4, 'flagship': 6.5, 'warsun': 9.0}
MIN_WIN_CHANCE = .80


class ActivationPlan(NamedTuple):
    score: float
    target: tuple
    source: tuple
    ships: tuple[str, ...] = ()
    passengers: tuple[str, ...] = ()
    flank_speed: bool = False
    odds: tuple[float | None, float | None] | None = None


def planet_value(planet, owner, human, round_number):
    if owner is None:
        return 2 + planet.resources + .5 * planet.influence
    pressure = 6.5 if owner == human else 3.5
    value = pressure * (1 + .3 * planet.resources)
    return value * (.65 if round_number == 1 else 1)


def _capture_value(ai, target, player, ground, human):
    """Value only planets for which the planned landing has credible forces."""
    candidates = [planet for planet in target.planets
                  if target.planet_owners.get(planet.planet_id) != player.faction]
    free, defended = [], []
    for planet in candidates:
        enemies = [unit for unit in target.units if unit.owner != player.faction and
                   unit.kind == 'infantry' and unit.location.planet_id == planet.planet_id]
        (defended if enemies else free).append((planet, enemies))
    value = 0.0
    remaining = list(ground)
    for planet, _ in sorted(free, key=lambda entry: -planet_value(
            entry[0], target.planet_owners.get(entry[0].planet_id), human,
            ai.window.turn_order.round_number)):
        if not remaining:
            break
        cannons = [unit for unit in target.units if unit.kind == 'pds' and
                   unit.owner != player.faction and
                   unit.location.planet_id == planet.planet_id]
        required = next((count for count in range(1, len(remaining) + 1)
                         if win_probability(remaining[:count], [],
                                            ai.window.turn_order.players,
                                            space=False, cannons=cannons) >= MIN_WIN_CHANCE), None)
        if required is None:
            continue
        value += planet_value(planet, target.planet_owners.get(planet.planet_id), human,
                               ai.window.turn_order.round_number)
        del remaining[:required]
    ground_odds = None
    if remaining and defended:
        # The invasion controller commits the remaining troops to one defended
        # planet.  Evaluate that actual battle, including its local PDS.
        choices = []
        for planet, enemies in defended:
            cannons = [unit for unit in target.units if unit.kind == 'pds' and
                       unit.owner != player.faction and
                       unit.location.planet_id == planet.planet_id]
            chance = win_probability(remaining, enemies,
                                     ai.window.turn_order.players, space=False,
                                     cannons=cannons)
            if chance >= MIN_WIN_CHANCE:
                worth = planet_value(planet, target.planet_owners.get(planet.planet_id),
                                     human, ai.window.turn_order.round_number)
                choices.append((worth * chance, worth, chance))
            elif ground_odds is None or chance > ground_odds:
                ground_odds = chance
        if choices:
            _, worth, ground_odds = max(choices)
            value += worth
    return value, ground_odds


def _eligible_ships(ai, player, target, speed_bonus):
    movement = ai.window.movement
    result = []
    for source in ai.window.board.values():
        if source is not target and player.faction in source.command_tokens:
            continue
        for unit in source.units:
            independent_fighter = (unit.kind == 'fighter' and
                                   'ff2' in player.technologies and
                                   unit.location.region == Region.SPACE)
            if unit.owner != player.faction or not (capital_ship(unit) or independent_fighter):
                continue
            if source is target or movement.route(source, target, unit, player,
                                                 bonus=speed_bonus):
                result.append((source, unit, False))
            elif 'gd' in player.technologies and movement.route(
                    source, target, unit, player, bonus=speed_bonus + 1):
                result.append((source, unit, True))
    return result


def _source_ground(source, player, ships):
    ids = {unit.unit_id for unit in ships}
    aboard = [unit for unit in source.units if unit.owner == player.faction and
              unit.kind == 'infantry' and unit.location.region == Region.TRANSPORT and
              unit.location.carrier_id in ids]
    available = [unit for unit in source.units if unit.owner == player.faction and
                 unit.kind == 'infantry' and unit.location.region == Region.PLANET]
    # Leave one defender on an owned planet when another can go instead.
    for planet in source.planets:
        defenders = [unit for unit in available
                     if unit.location.planet_id == planet.planet_id]
        if len(defenders) >= 2 and source.planet_owners.get(planet.planet_id) == player.faction:
            available.remove(min(defenders, key=lambda unit: unit.unit_id))
    return aboard, sorted(available, key=lambda unit: unit.unit_id)


def _manifest(ai, player, entries, ground_goal):
    grouped = defaultdict(list)
    for source, unit, _ in entries:
        grouped[source.position].append(unit)
    passenger_ids = []
    ground, fighters = [], []
    for position, ships in grouped.items():
        source = ai.window.board[position]
        ids = {unit.unit_id for unit in ships}
        aboard = [unit for unit in source.units if unit.owner == player.faction and
                  unit.location.region == Region.TRANSPORT and
                  unit.location.carrier_id in ids]
        ground.extend(unit for unit in aboard if unit.kind == 'infantry')
        fighters.extend(unit for unit in aboard if unit.kind == 'fighter')
        capacity = max(0, sum(unit.capacity for unit in ships) -
                       sum(cargo_cost(unit) for unit in aboard))
        _, infantry = _source_ground(source, player, ships)
        for unit in infantry:
            if capacity < cargo_cost(unit) or len(ground) >= ground_goal:
                break
            passenger_ids.append(unit.unit_id)
            ground.append(unit)
            capacity -= cargo_cost(unit)
        candidates = sorted((unit for unit in source.units
                             if unit.owner == player.faction and unit.kind == 'fighter' and
                             unit.location.region == Region.SPACE and unit.unit_id not in ids),
                            key=lambda unit: unit.unit_id)
        for unit in candidates:
            if capacity < cargo_cost(unit):
                break
            passenger_ids.append(unit.unit_id)
            fighters.append(unit)
            capacity -= cargo_cost(unit)
        for unit in infantry:
            if unit.unit_id in passenger_ids or capacity < cargo_cost(unit):
                continue
            passenger_ids.append(unit.unit_id)
            ground.append(unit)
            capacity -= cargo_cost(unit)
    return tuple(passenger_ids), ground, fighters


def _home_commitment(entries, player):
    by_source = defaultdict(int)
    for source, unit, _ in entries:
        if capital_ship(unit):
            by_source[source.position] += 1
    for source, _, _ in entries:
        if any(planet.faction_homeworld == player.faction for planet in source.planets):
            capitals = sum(unit.owner == player.faction and capital_ship(unit)
                           for unit in source.units)
            if capitals > 1 and by_source[source.position] >= capitals:
                return False
    return True


def _limits_allow_plan(ai, player, target, entries, passenger_ids):
    """Ask the real capacity/fleet rules about the projected board state."""
    movement = ai.window.movement
    projected = {target.position: list(target.units)}
    ships_by_source = defaultdict(list)
    for source, ship, _ in entries:
        projected.setdefault(source.position, list(source.units))
        ships_by_source[source.position].append(ship)
    transfers = []
    for position, ships in ships_by_source.items():
        source = ai.window.board[position]
        ids = {ship.unit_id for ship in ships}
        transfers.extend((source, ship, UnitLocation(Region.SPACE)) for ship in ships)
        transfers.extend((source, unit, unit.location) for unit in source.units
                         if unit.owner == player.faction and
                         unit.location.region == Region.TRANSPORT and
                         unit.location.carrier_id in ids)
        carrier_id = next((ship.unit_id for ship in ships if ship.capacity > 0), None)
        transfers.extend((source, unit, UnitLocation(
            Region.SPACE if unit.kind == 'fighter' else Region.TRANSPORT,
            carrier_id=carrier_id if unit.kind == 'infantry' else None))
            for unit in source.units if unit.unit_id in passenger_ids)
    for source, unit, location in transfers:
        projected[source.position].remove(unit)
        moved = copy(unit)
        moved.location = location
        projected[target.position].append(moved)
    for units in projected.values():
        preview = SimpleNamespace(units=units)
        if (movement.capacity_overflow(preview, player.faction) or
                len(movement._fleet_ships_at(preview, player.faction)) >
                movement.fleet_supply(player)):
            return False
    return True


def _ground_carriers(ai, player, entries, passenger_ids, independent_ids):
    """Mirror the controller's first-available-carrier loading order."""
    grouped = defaultdict(set)
    for source, ship, _ in entries:
        grouped[source.position].add(ship.unit_id)
    carriers = set()
    for position, selected in grouped.items():
        source = ai.window.board[position]
        ships = [unit for unit in source.units if unit.unit_id in selected]
        carried = [unit for unit in source.units if unit.owner == player.faction and
                   unit.location.region == Region.TRANSPORT and
                   unit.location.carrier_id in selected]
        remaining = {ship.unit_id: ship.capacity - sum(
            cargo_cost(unit) for unit in carried
            if unit.location.carrier_id == ship.unit_id) for ship in ships}
        carriers.update(unit.location.carrier_id for unit in carried
                        if unit.kind == 'infantry')
        for unit in source.units:
            if unit.unit_id not in passenger_ids or unit.unit_id in independent_ids:
                continue
            cost = cargo_cost(unit)
            carrier_id = next((ship_id for ship_id, slots in remaining.items()
                               if slots >= cost), None)
            if carrier_id is None:
                return None
            remaining[carrier_id] -= cost
            if unit.kind == 'infantry':
                carriers.add(carrier_id)
    return carriers


def best_activation(ai, player):
    board, movement, turn = ai.window.board, ai.window.movement, ai.window.turn_order
    human = ai.human_faction()
    flank_available = any(canonical_action_card(card) == 'flank_speed'
                          for card in player.action_cards)
    best = None
    for target in board.values():
        if player.faction in target.command_tokens:
            continue
        friendly = [unit for unit in target.units if unit.owner == player.faction and
                    unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        enemies = [unit for unit in target.units if unit.owner != player.faction and
                   unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        cannons = [unit for area in (target, *movement.neighbors(target))
                   for unit in area.units if unit.kind == 'pds' and
                   unit.owner != player.faction and
                   unit_profile(unit).get('spaceCannonHitsOn') and
                   (area is target or unit_profile(unit).get('deepSpaceCannon'))]
        uncaptured = [planet for planet in target.planets
                      if target.planet_owners.get(planet.planet_id) != player.faction]
        uncaptured_ids = {planet.planet_id for planet in uncaptured}
        dock = any(unit.owner == player.faction and unit.kind == 'spacedock' and
                   target.planet_owners.get(unit.location.planet_id) == player.faction
                   for unit in target.units)
        if dock:
            budget = player.available_values[0] + player.trade_goods
            local_count = sum(unit.owner == player.faction for unit in target.units)
            if budget >= 1:
                score = 7 + min(4, budget) + (2 if local_count < 6 else 0)
                plan = ActivationPlan(score, target.position, target.position)
                if best is None or plan.score > best.score:
                    best = plan
        if not uncaptured:
            continue
        ground_goal = (len(uncaptured) + 2 * sum(
            unit.kind == 'infantry' and unit.owner != player.faction
            for unit in target.units) + 2 * sum(
            unit.kind == 'pds' and unit.owner != player.faction and
            unit.location.planet_id in uncaptured_ids
            for unit in target.units))
        slots = max(0, movement.fleet_supply(player) -
                    sum(capital_ship(unit) for unit in friendly))
        for speed_bonus in ((0, 1) if flank_available else (0,)):
            options = _eligible_ships(ai, player, target, speed_bonus)
            gravity_by_id = {unit.unit_id: boosted for _, unit, boosted in options}
            seeds = []
            for source, ship, boosted in options:
                if ship.capacity <= 0:
                    continue
                aboard, available = _source_ground(source, player, [ship])
                if aboard or available:
                    seeds.append((source, ship, boosted))
            # A few distinct transports suffice; extra ships can join from any
            # eligible source after the transport is chosen.
            seeds.sort(key=lambda entry: (-len(_source_ground(entry[0], player,
                                                              [entry[1]])[1]),
                                          -entry[1].capacity, entry[0].position,
                                          entry[1].unit_id))
            for seed in seeds[:8]:
                chosen = [seed]
                remaining = [entry for entry in options if entry[1] is not seed[1]]
                remaining.sort(key=lambda entry: (-SHIP_VALUE.get(entry[1].kind, 0),
                                                  entry[0].position, entry[1].unit_id))
                for _ in range(min(len(options), 10)):
                    if _home_commitment(chosen, player):
                        passenger_ids, ground, fighters = _manifest(
                            ai, player, chosen, ground_goal)
                        boosts = (sum(boosted for _, _, boosted in chosen) +
                                  sum(gravity_by_id.get(unit_id, False)
                                      for unit_id in passenger_ids))
                        ground_carriers = _ground_carriers(
                            ai, player, chosen, passenger_ids, gravity_by_id.keys())
                        if boosts <= 1 and ground_carriers is not None and _limits_allow_plan(
                                ai, player, target, chosen, passenger_ids):
                            incoming = [unit for source, unit, _ in chosen if source is not target]
                            fleet = friendly + incoming + fighters
                            space_odds = (win_probability(
                                fleet, enemies, turn.players, cannons=cannons,
                                require_transport=True,
                                cargo_carriers=ground_carriers) if enemies or cannons else 1.0)
                            capture_value, ground_odds = _capture_value(
                                ai, target, player, ground, human)
                        else:
                            space_odds, capture_value, ground_odds = 0, 0, None
                        if capture_value and space_odds >= MIN_WIN_CHANCE:
                            home_cost = sum(.9 for source, unit, _ in chosen
                                            if source is not target and
                                            any(p.faction_homeworld == player.faction
                                                for p in source.planets))
                            score = (9 + capture_value + min(4, len(ground)) -
                                     .6 * len(incoming) - home_cost - 2 * speed_bonus)
                            if enemies or cannons:
                                score += 4 * (space_odds - MIN_WIN_CHANCE)
                            plan = ActivationPlan(
                                score, target.position, seed[0].position,
                                tuple(unit.unit_id for _, unit, _ in chosen),
                                passenger_ids, bool(speed_bonus),
                                (space_odds if enemies or cannons else None, ground_odds))
                            if best is None or plan.score > best.score:
                                best = plan
                    if not remaining:
                        break
                    gravity_used = any(boosted for _, _, boosted in chosen)
                    capitals = sum(capital_ship(unit) and source is not target
                                   for source, unit, _ in chosen)
                    choices = [entry for entry in remaining
                               if (not entry[2] or not gravity_used) and
                               (not capital_ship(entry[1]) or
                                entry[0] is target or capitals < slots) and
                               _home_commitment(chosen + [entry], player)]
                    if not choices:
                        break
                    chosen.append(choices[0])
                    remaining.remove(choices[0])
    return best if best and best.score > 0 else None
