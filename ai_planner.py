"""Legal, reproducible tactical plans for the automated players.

The forecast and the eventual movement use the same ship and passenger IDs.
This matters for Gravity Drive, fleet supply, and attacks assembled from more
than one system.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import copy
from types import SimpleNamespace
from typing import NamedTuple

from ai_combat import win_probability
from ai_economy import production_frontier
from action_cards import canonical_action_card
from movement import capital_ship, cargo_cost
from units import Region, UNIT_TYPES, UnitLocation, mobile_ship, unit_profile


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


def _landing_options(ai, target, player, ground):
    """Smallest credible force for each planet, including its own PDS."""
    options = []
    human = ai.human_faction()
    session = getattr(getattr(ai.window, 'movement', None), 'session', None)
    disabled = session.disabled_pds if session and session.target is target else set()
    for planet in sorted(target.planets, key=lambda item: item.planet_id):
        owner = target.planet_owners.get(planet.planet_id)
        if owner == player.faction:
            continue
        enemies = [unit for unit in target.units if unit.owner != player.faction and
                   unit.kind == 'infantry' and unit.location.planet_id == planet.planet_id]
        cannons = [unit for unit in target.units if unit.owner != player.faction and
                   unit.unit_id not in disabled and
                   unit.kind == 'pds' and unit.location.planet_id == planet.planet_id]
        for count in range(1, len(ground) + 1):
            chance = win_probability(ground[:count], enemies,
                                     ai.window.turn_order.players, space=False,
                                     cannons=cannons)
            if chance >= MIN_WIN_CHANCE:
                worth = planet_value(planet, owner, human, ai.window.turn_order.round_number)
                options.append((planet.planet_id, count, worth, chance,
                                bool(enemies or cannons)))
                break
    return options


def _landing_selection(ai, target, player, ground, cache=None):
    # A system has at most three planets: considering their subsets is cheap
    # and avoids committing all troops to one fight or a low-value free planet.
    key = (target.position, tuple(sorted((unit.owner, unit.profile_id, unit.damaged)
                                         for unit in ground)))
    if cache is not None and key in cache:
        return cache[key]
    choices = [(0.0, 0, ())]
    for option in _landing_options(ai, target, player, ground):
        _, count, worth, chance, _ = option
        choices += [(value + worth * chance, used + count, selected + (option,))
                    for value, used, selected in list(choices)
                    if used + count <= len(ground)]
    selected = max(choices, key=lambda item: (item[0], -item[1]))[2]
    if cache is not None:
        cache[key] = selected
    return selected


def plan_landings(ai, target, player, ground):
    """Share the forecast's exact, minimum-force planetary assignments with AI UI."""
    ground = sorted(ground, key=lambda unit: unit.unit_id)
    selected = _landing_selection(ai, target, player, ground)
    assignments = {}
    offset = 0
    for planet_id, count, _, _, _ in selected:
        assignments.update((unit.unit_id, planet_id) for unit in ground[offset:offset + count])
        offset += count
    return assignments


def _capture_value(ai, target, player, ground, human, cache=None):
    selected = _landing_selection(ai, target, player,
                                  sorted(ground, key=lambda unit: unit.unit_id), cache)
    odds = [chance for _, _, _, chance, contested in selected if contested]
    return sum(worth * chance for _, _, worth, chance, _ in selected), min(odds, default=None)


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
            if unit.owner != player.faction or unit.location.region != Region.SPACE or not (
                    (mobile_ship(unit) and unit.kind != 'fighter') or independent_fighter):
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
    return tuple(passenger_ids), ground, fighters


def _counterattack_forces(ai, player, target):
    """Plausible next-action invasions, using the opponents' actual legal routes."""
    movement = ai.window.movement
    forces = []
    for index, opponent in enumerate(ai.window.turn_order.players):
        if opponent is player or not opponent.command_pools.get('tactical', 0):
            continue
        if index in getattr(ai.window.turn_order, 'passed_indices', set()):
            continue
        if opponent.faction in target.command_tokens:
            continue
        grouped = defaultdict(list)
        for origin, unit, boost in _eligible_ships(ai, opponent, target, 0):
            if origin is not target and unit.kind != 'spacedock':
                grouped[origin.position].append((origin, unit, boost))
        # A single hostile fleet is enough to make a threat credible. This
        # deliberately avoids inventing an all-opponent coalition invasion.
        for entries in grouped.values():
            entries.sort(key=lambda entry: (-SHIP_VALUE.get(entry[1].kind, 0),
                                            entry[1].unit_id))
            chosen, gravity_used, capitals = [], False, 0
            for entry in entries:
                if entry[2] and gravity_used:
                    continue
                if capital_ship(entry[1]) and capitals >= movement.fleet_supply(opponent):
                    continue
                chosen.append(entry)
                gravity_used |= entry[2]
                capitals += int(capital_ship(entry[1]))
            _, ground, fighters = _manifest(ai, opponent, chosen, 4)
            fleet = [unit for _, unit, _ in chosen] + fighters
            if fleet:
                forces.append((opponent, fleet, ground))
    return forces


def _source_exposure(ai, player, source, units, threats):
    """Expected economic damage from the strongest available counterattack."""
    defenders = [unit for unit in units if unit.owner == player.faction and
                 unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
    owned = [planet for planet in source.planets
             if source.planet_owners.get(planet.planet_id) == player.faction]
    docks = [unit for unit in units if unit.owner == player.faction and unit.kind == 'spacedock']
    is_home = any(planet.faction_homeworld == player.faction for planet in owned)
    cannons = [unit for area in (source, *ai.window.movement.neighbors(source))
               for unit in area.units if unit.owner == player.faction and unit.kind == 'pds' and
               (area is source or unit_profile(unit).get('deepSpaceCannon'))]
    highest = 0.0
    for _, fleet, ground in threats:
        chance = win_probability(fleet, defenders, ai.window.turn_order.players, cannons=cannons)
        if chance < .45:
            continue
        # Losing access to a dock or a home system is costly even before a
        # landing; an empty low-value transit system is cheap to relinquish.
        harm = 3 * len(docks) + (4 if is_home and docks else 0)
        if ground:
            invasions = []
            for planet in owned:
                guards = [unit for unit in units if unit.owner == player.faction and
                          unit.kind == 'infantry' and
                          unit.location.planet_id == planet.planet_id]
                pds = [unit for unit in cannons if unit.location.planet_id == planet.planet_id]
                invasion = win_probability(ground, guards, ai.window.turn_order.players,
                                           space=False, cannons=pds)
                if invasion >= .5:
                    value = 3 + planet.resources + .5 * planet.influence
                    value += 12 if is_home else 0
                    invasions.append(value * invasion)
            harm += max(invasions, default=0)
        highest = max(highest, chance * harm)
    return highest


def _departure_cost(ai, player, target, entries, passengers, threat_cache):
    grouped = defaultdict(set)
    for source, ship, _ in entries:
        if source is not target:
            grouped[source.position].add(ship.unit_id)
    cost = 0.0
    exposure_cache = threat_cache.setdefault('exposures', {})
    for position, ship_ids in grouped.items():
        source = ai.window.board[position]
        if position not in threat_cache:
            threats = _counterattack_forces(ai, player, source)
            baseline = _source_exposure(ai, player, source, source.units, threats)
            threat_cache[position] = threats, baseline
        threats, baseline = threat_cache[position]
        if not threats:
            continue
        remaining = [unit for unit in source.units if unit.unit_id not in ship_ids and
                     unit.unit_id not in passengers and not (
                         unit.location.region == Region.TRANSPORT and
                         unit.location.carrier_id in ship_ids)]
        key = (position, frozenset(unit.unit_id for unit in remaining))
        if key not in exposure_cache:
            exposure_cache[key] = _source_exposure(ai, player, source, remaining, threats)
        cost += 1.4 * max(0, exposure_cache[key] - baseline)
    return cost


def _entry_signature(entry):
    """Interchangeable models at one origin need only one fleet evaluation."""
    source, unit, boosted = entry
    aboard = tuple(sorted((cargo.kind, cargo.profile_id or '', cargo.damaged)
                           for cargo in source.units if cargo.location.region == Region.TRANSPORT and
                           cargo.location.carrier_id == unit.unit_id))
    return source.position, unit.kind, unit.profile_id or '', unit.damaged, boosted, aboard


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
    board, movement = ai.window.board, ai.window.movement
    flank_available = any(canonical_action_card(card) == 'flank_speed'
                          for card in player.action_cards)
    best = None
    threat_cache = {}
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
                   (target.planet_owners.get(unit.location.planet_id) == player.faction or
                    (player.faction == 'saar' and unit.location.region == Region.SPACE))
                   for unit in target.units)
        if dock:
            budget = player.available_values[0] + player.trade_goods
            local = [unit for unit in target.units if unit.owner == player.faction]
            transport = sum(unit.capacity for unit in local
                            if unit.location.region == Region.SPACE)
            infantry = sum(unit.kind == 'infantry' for unit in local)
            fighters = sum(unit.kind == 'fighter' for unit in local)
            fleet_slots = movement.fleet_supply(player) - sum(capital_ship(unit) for unit in local)
            frontier = bool(production_frontier(movement, target, player)[0])
            need = ((3 if not transport and frontier and fleet_slots > 0 and budget >= 3 else 0) +
                    (2 if transport and infantry < 2 and frontier else 0) +
                    (1 if fighters < min(transport + 3, 3) else 0) +
                    (1 if fleet_slots > 0 and budget >= 2 else 0))
            if budget >= 1:
                score = 4 + min(3, budget) + need if need else 0
                plan = ActivationPlan(score, target.position, target.position)
                if best is None or plan.score > best.score:
                    best = plan
        if not uncaptured and not enemies:
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
                aboard, available = _source_ground(source, player, [ship])
                if (ship.capacity > 0 and (aboard or available)) or (enemies and capital_ship(ship)):
                    seeds.append((source, ship, boosted))
            # A few distinct transports suffice; extra ships can join from any
            # eligible source after the transport is chosen.
            seeds.sort(key=lambda entry: (-len(_source_ground(entry[0], player,
                                                              [entry[1]])[1]),
                                          -entry[1].capacity, entry[0].position,
                                          entry[1].unit_id))
            evaluated = {}
            unique_seeds, seed_signatures = [], set()
            for entry in seeds:
                signature = _entry_signature(entry)
                if signature not in seed_signatures:
                    seed_signatures.add(signature)
                    unique_seeds.append(entry)
            for seed in unique_seeds[:6]:
                if len(evaluated) >= 24:
                    break
                # Compare cheap screens and heavy escorts. Keeping both paths
                # avoids committing three cheap ships where one strong ship is
                # sufficient, or a dreadnought to an undefended neutral planet.
                for heavy_first in (False, True):
                    if len(evaluated) >= 24:
                        break
                    chosen = [seed]
                    remaining = [entry for entry in options if entry[1] is not seed[1]]
                    remaining.sort(key=lambda entry: (
                        SHIP_VALUE.get(entry[1].kind, 0) * (-1 if heavy_first else 1),
                        entry[0].position, entry[1].unit_id))
                    for _ in range(min(len(options), 10)):
                        key = tuple(sorted(_entry_signature(entry) for entry in chosen))
                        complete = evaluated.get(key, False)
                        if key not in evaluated:
                            plan, complete = _evaluate_activation(
                                ai, player, target, chosen, ground_goal, friendly, enemies,
                                cannons, gravity_by_id, speed_bonus, threat_cache)
                            evaluated[key] = complete
                            if plan and (best is None or plan.score > best.score):
                                best = plan
                        if complete or len(evaluated) >= 24:
                            break
                        if not remaining:
                            break
                        gravity_used = any(boosted for _, _, boosted in chosen)
                        capitals = sum(capital_ship(unit) and source is not target
                                       for source, unit, _ in chosen)
                        choices = [entry for entry in remaining
                                   if (not entry[2] or not gravity_used) and
                                   (not capital_ship(entry[1]) or
                                    entry[0] is target or capitals < slots)]
                        if not choices:
                            break
                        chosen.append(choices[0])
                        remaining.remove(choices[0])
    if best is None or best.score < 14:
        staging = _best_staging(ai, player, threat_cache)
        if staging and (best is None or staging.score > best.score):
            best = staging
    return best if best and best.score > 0 else None


def _evaluate_activation(ai, player, target, chosen, ground_goal, friendly, enemies,
                         cannons, gravity_by_id, speed_bonus, threat_cache):
    passenger_ids, ground, fighters = _manifest(
        ai, player, chosen, ground_goal)
    boosts = (sum(boosted for _, _, boosted in chosen) +
              sum(gravity_by_id.get(unit_id, False)
                  for unit_id in passenger_ids))
    ground_carriers = _ground_carriers(
        ai, player, chosen, passenger_ids, gravity_by_id.keys())
    if boosts > 1 or ground_carriers is None or not _limits_allow_plan(
            ai, player, target, chosen, passenger_ids):
        return None, False
    incoming = [unit for source, unit, _ in chosen if source is not target]
    # Factories can carry the landing force but never roll
    # combat dice or absorb hits as fleet ships.
    fleet = list({unit.unit_id: unit for unit in friendly + incoming + fighters
                  if UNIT_TYPES[unit.kind]['ship']}.values())
    factories = {unit.unit_id for _, unit, _ in chosen if unit.kind == 'spacedock'}
    require_transport = bool(ground) and not bool(ground_carriers & factories)
    space_odds = (win_probability(
        fleet, enemies, ai.window.turn_order.players, cannons=cannons,
        require_transport=require_transport,
        cargo_carriers=ground_carriers) if enemies or cannons else 1.0)
    if space_odds < MIN_WIN_CHANCE:
        return None, False
    selected = _landing_selection(ai, target, player, ground,
                                  threat_cache.setdefault('landings', {}))
    capture_value = sum(worth * chance for _, _, worth, chance, _ in selected)
    ground_odds = min((chance for _, _, _, chance, contested in selected if contested), default=None)
    complete = len(selected) == sum(target.planet_owners.get(planet.planet_id) != player.faction
                                    for planet in target.planets)
    enemy_value = sum(SHIP_VALUE.get(unit.kind, 0) for unit in enemies)
    owned = [planet for planet in target.planets
             if target.planet_owners.get(planet.planet_id) == player.faction]
    # A raid needs material value or a blockade to break;
    # chasing a lone fighter is not worth an activation.
    raid_value = 0
    if enemies and (owned or enemy_value >= 3):
        pressure = 1.3 if any(unit.owner == ai.human_faction()
                             for unit in enemies) else 1
        raid_value = min(7, enemy_value * pressure) + 3 * len(owned)
    if not capture_value and not raid_value:
        return None, False
    departure = _departure_cost(ai, player, target, chosen,
                                passenger_ids, threat_cache)
    material = sum(SHIP_VALUE.get(unit.kind, 2) for unit in incoming)
    score = (9 + capture_value + raid_value - .45 * material -
             .5 * len(incoming) - .12 * len(passenger_ids) - departure -
             2 * speed_bonus - 6 * (1 - space_odds))
    return ActivationPlan(
        score, target.position, chosen[0][0].position,
        tuple(unit.unit_id for _, unit, _ in chosen), passenger_ids,
        bool(speed_bonus),
        (space_odds if enemies or cannons else None, ground_odds)), complete


def _distances(movement, destination, faction):
    distances = {destination.position: 0}
    queue = deque([destination])
    while queue:
        tile = queue.popleft()
        for other in movement.neighbors(tile, faction):
            if other.position not in distances:
                distances[other.position] = distances[tile.position] + 1
                queue.append(other)
    return distances


def _best_staging(ai, player, threat_cache):
    """Advance a usable invasion fleet toward a valuable goal for next round.

    Staging cannot unlock ships again this round. It is therefore lower-value
    than capturing territory immediately, and requires a concrete reachable
    objective from the proposed waypoint rather than aimless map movement.
    """
    movement, board = ai.window.movement, ai.window.board
    goals = [tile for tile in board.values() if any(
        tile.planet_owners.get(planet.planet_id) != player.faction for planet in tile.planets)]
    goals.sort(key=lambda tile: (-sum(planet_value(
        planet, tile.planet_owners.get(planet.planet_id), ai.human_faction(),
        ai.window.turn_order.round_number) for planet in tile.planets
        if tile.planet_owners.get(planet.planet_id) != player.faction), tile.position))
    best = None
    for goal in goals[:6]:
        distances = _distances(movement, goal, player.faction)
        for source in board.values():
            if player.faction in source.command_tokens or source is goal:
                continue
            transports = [unit for unit in source.units if unit.owner == player.faction and
                          unit.location.region == Region.SPACE and unit.capacity > 0 and
                          mobile_ship(unit) and any(_source_ground(source, player, [unit]))]
            for transport in transports[:2]:
                if movement.route(source, goal, transport, player,
                                  bonus=int('gd' in player.technologies)):
                    continue
                for waypoint in movement.neighbors(goal, player.faction):
                    if (waypoint is source or player.faction in waypoint.command_tokens or
                            distances.get(waypoint.position, 999) >= distances.get(source.position, 999) or
                            any(unit.owner != player.faction and mobile_ship(unit) and
                                unit.location.region == Region.SPACE for unit in waypoint.units) or
                            any(waypoint.planet_owners.get(planet.planet_id) != player.faction
                                for planet in waypoint.planets)):
                        continue
                    if not (movement.route(source, waypoint, transport, player) and
                            movement.route(waypoint, goal, transport, player)):
                        continue
                    entries = [(source, transport, False)]
                    escorts = [unit for unit in source.units if unit is not transport and
                               unit.owner == player.faction and capital_ship(unit) and
                               movement.route(source, waypoint, unit, player) and
                               movement.route(waypoint, goal, unit, player)]
                    escorts.sort(key=lambda unit: (-SHIP_VALUE.get(unit.kind, 0), unit.unit_id))
                    for escort_count in range(min(len(escorts), 3) + 1):
                        chosen = entries + [(source, unit, False) for unit in escorts[:escort_count]]
                        passengers, ground, fighters = _manifest(ai, player, chosen, 6)
                        if not ground or not _limits_allow_plan(ai, player, waypoint, chosen, passengers):
                            continue
                        fleet = [unit for _, unit, _ in chosen if UNIT_TYPES[unit.kind]['ship']] + fighters
                        carriers = _ground_carriers(ai, player, chosen, passengers, ())
                        if carriers is None:
                            continue
                        factories = {unit.unit_id for _, unit, _ in chosen if unit.kind == 'spacedock'}
                        require_transport = not bool(carriers & factories)
                        local_cannons = [unit for area in (waypoint, *movement.neighbors(waypoint))
                                         for unit in area.units if unit.owner != player.faction and
                                         unit.kind == 'pds' and (area is waypoint or
                                                                  unit_profile(unit).get('deepSpaceCannon'))]
                        local_fleet = fleet + [unit for unit in waypoint.units
                                               if unit.owner == player.faction and
                                               unit.location.region == Region.SPACE and
                                               UNIT_TYPES[unit.kind]['ship']]
                        if local_cannons and win_probability(
                                local_fleet, [], ai.window.turn_order.players, cannons=local_cannons,
                                require_transport=require_transport, cargo_carriers=carriers) < MIN_WIN_CHANCE:
                            continue
                        enemies = [unit for unit in goal.units if unit.owner != player.faction and
                                   unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
                        cannons = [unit for area in (goal, *movement.neighbors(goal)) for unit in area.units
                                   if unit.owner != player.faction and unit.kind == 'pds' and
                                   (area is goal or unit_profile(unit).get('deepSpaceCannon'))]
                        odds = win_probability(fleet, enemies, ai.window.turn_order.players,
                                               cannons=cannons, require_transport=require_transport,
                                               cargo_carriers=carriers)
                        worth, _ = _capture_value(ai, goal, player, ground, ai.human_faction())
                        if odds < MIN_WIN_CHANCE or not worth:
                            continue
                        departure = _departure_cost(ai, player, waypoint, chosen, passengers, threat_cache)
                        score = (7 + min(4, .3 * worth) - .4 * len(chosen) - departure)
                        plan = ActivationPlan(score, waypoint.position, source.position,
                                              tuple(unit.unit_id for _, unit, _ in chosen), passengers)
                        if best is None or plan.score > best.score:
                            best = plan
                        break
    return best
