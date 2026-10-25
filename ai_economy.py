"""State-dependent economic goals and bounded fleet-composition planning.

The planner scores complete purchases, so a transport and its infantry can be
valuable together even when neither would be a useful purchase on its own.
It only previews choices; payment, production and upgrades use the controllers.
"""
from __future__ import annotations

from collections import Counter
from math import ceil

from movement import capital_ship
from technology import (COLOR_CODES, available_technologies, is_unit_upgrade,
                        production_allowed, technology_catalog, unit_stats)
from units import Region, UNIT_TYPES, unit_profile, unit_profiles


BUILD_KINDS = ('carrier', 'infantry', 'mech', 'fighter', 'destroyer', 'cruiser',
               'dreadnought', 'flagship', 'warsun')
MODEL_LIMITS = {'carrier': 4, 'destroyer': 8, 'cruiser': 8,
                'dreadnought': 5, 'flagship': 1, 'warsun': 2, 'mech': 4}


def owned_units(player, board):
    return [unit for tile in board.values() for unit in tile.units
            if unit.owner == player.faction]


def effective_yields(player, card, exploration=None):
    resources, influence = (exploration.planet_values(card.planet)
                            if exploration else
                            (card.planet.resources, card.planet.influence))
    participants = (exploration.players.values() if exploration else (player,))
    bonus = 3 if any(card.planet.planet_id in participant.hero_attachments
                     for participant in participants) else 0
    return resources + bonus, influence + bonus


def command_targets(player, board, round_number):
    """Fund actual fleets and operations instead of equalizing all three pools."""
    stacks = [sum(capital_ship(unit) and unit.location.region == Region.SPACE
                  and unit.owner == player.faction for unit in tile.units)
              for tile in board.values()]
    expeditions = sum(any(unit.owner == player.faction and unit.capacity > 0 and
                         unit.location.region == Region.SPACE for unit in tile.units)
                      for tile in board.values())
    bonus = 2 if player.faction == 'letnev' else 0
    current = max(stacks, default=0)
    resources = player.available_values[0] + player.trade_goods
    growing = resources >= 4 and current > 0
    fleet = max(2, min(6, current + int(growing) - bonus))
    tactical = min(5, max(3, expeditions + 1))
    strategic = 1 + int(resources >= 4 or round_number >= 3)
    return {'tactical': tactical, 'fleet': fleet, 'strategic': strategic}


def technology_values(player, board, round_number, *, exploration=None):
    """Immediate fleet/economy value, with discounted paths to useful upgrades."""
    units = owned_units(player, board)
    fleet = Counter(unit.kind for unit in units)
    sustaining = sum(bool(unit_profile(unit).get('sustainDamage')) for unit in units)
    slow_transports = sum(unit.capacity > 0 and unit.move_value < 2 for unit in units
                          if unit.location.region == Region.SPACE)
    producers = sum(bool(unit_profile(unit).get('productionValue')) for unit in units)
    enemies = [unit for tile in board.values() for unit in tile.units
               if unit.owner != player.faction]
    enemy_fighters = sum(unit.kind == 'fighter' for unit in enemies)
    enemy_ground = sum(unit.kind in ('infantry', 'mech') for unit in enemies)
    stacks = [sum(unit.owner == player.faction and capital_ship(unit)
                  for unit in tile.units) for tile in board.values()]
    ready_income = sum(max(effective_yields(player, c, exploration))
                       for c in player.planets if not c.exhausted)
    specialty_count = sum(bool(exploration.planet_specialties(card.planet)
                               if exploration else card.planet.tech_specialties)
                          for card in player.planets)
    docks = fleet['spacedock']
    frontier_count = (sum(bool(tokens) for tokens in exploration.space_tokens.values())
                      if exploration else 0)
    long_game = max(.35, 1.15 - .12 * round_number)
    values = {
        'amd': 5 + min(8, len(units) / 2),
        'gd': 9 + min(20, 7 * slow_transports) + min(5, fleet['dreadnought']),
        'st': 5 + min(19, 5 * producers) * long_game,
        'nm': 11 * long_game + max(0, 4 - len(player.action_cards)),
        'hm': 19 * long_game + 2 * (player.command_pools['tactical'] < 2),
        'ps': 3 + 3 * (fleet['dreadnought'] + fleet['pds'] + fleet['warsun']),
        'fl': 3 + min(12, player.command_pools['tactical'] * 2),
        'lwd': 5 + min(13, len(enemies) / 2),
        'dxa': 2 + min(8, fleet['infantry'] / 2),
        'md': 4 + 3 * (fleet['pds'] + docks),
        'da': 2 + 5 * sustaining,
        'asc': 3 + 14 * sum(size >= 3 for size in stacks),
        'gls': 1 + 4 * fleet['pds'],
        'td': 2 + min(12, fleet['infantry']),
        'ie': 3 + min(14, fleet['carrier'] * 3 + fleet['infantry'] / 2),
        'x89c4': 4 + min(14, enemy_ground) *
                  bool(fleet['dreadnought'] + fleet['warsun']),
        'nes': 4 + 8 * sustaining,
        'ers': 5 + 3 * sum(size > 0 for size in stacks),
        'pm': 20 * long_game,
        'cm': 8 + 5 * fleet['spacedock'],
        'mr': 3 + 5 * fleet['warsun'],
        'ds': 4 + min(14, sum(stacks)),
        'wg': 8 + min(10, len(player.planets)),
        'scc': 3 + 3 * sum(size > 0 for size in stacks),
        'l4': 2 + 2 * sum(unit.kind == 'pds' for unit in enemies),
        'bio': 3 + min(14, fleet['infantry']),
        'qdn': 3 + 8 * (player.trade_goods >= 3),
        'is': 5 + 8 * (ready_income >= 8),
        # Favor PoK technologies with direct effects in this ruleset. Cards
        # without a resolver remain low priority until their ability is live.
        'det': 2 + min(10, frontier_count * 2),
        'sr': 5 + min(13, docks * 4 + ready_income / 2),
        'pa': 3 + min(18, specialty_count * 5),
        'bs': 3 + min(14, specialty_count * 3 + fleet['spacedock']),
        'aida': 9 + min(12, 3 * (fleet['carrier'] + fleet['destroyer'] +
                                 fleet['dreadnought'])),
        'sar': 0,
        'sdn': 0,
        'pi': 5 + min(10, sum(player.command_pools.values())),
    }
    definitions, _ = unit_profiles()
    cards = available_technologies(player)
    for tech in cards:
        if not is_unit_upgrade(tech):
            continue
        alias = tech['alias']
        profile = next((profile for profile in definitions.values()
                        if profile.get('requiredTechId') == alias and
                        profile.get('faction') in (None, player.faction)), None)
        if not profile:
            continue
        kind = profile['baseType']
        count = fleet[kind]
        base = unit_stats(player, kind)
        move_gain = max(0, profile.get('moveValue', 0) - base.get('moveValue', 0))
        capacity_gain = max(0, profile.get('capacityValue', 0) - base.get('capacityValue', 0))
        combat_gain = max(0, (base.get('combatHitsOn') or 11) -
                          (profile.get('combatHitsOn') or 11))
        value = 2 + count * (4 * move_gain + 1.5 * capacity_gain + 1.7 * combat_gain)
        if kind == 'carrier':
            value += min(8, fleet['infantry']) + 3 * count
        elif kind == 'fighter':
            value += min(9, count)  # independent movement and capacity relief
        elif kind == 'destroyer':
            value += min(24, enemy_fighters * min(count, 3) * 1.5)
        elif kind == 'dreadnought':
            value += 3 * count  # sustain is no longer vulnerable to Direct Hit
        elif kind == 'pds':
            value += 7 * count
        elif kind == 'spacedock':
            value += count * (7 if player.faction == 'saar' else 2) * (ready_income >= 4)
        elif kind == 'infantry':
            value += count * (1.4 if player.faction == 'arborec' else .6)
        elif kind == 'warsun':
            value += 18 if count else (7 if ready_income >= 10 else 0)
        values[alias] = value
    # A prerequisite is useful when it moves us toward a concrete fleet goal.
    # Use one best path, not the sum of unrelated technologies of the same color.
    immediate = dict(values)
    catalog = technology_catalog()
    owned_colors = Counter(color for alias in player.technologies
                           for kind in catalog.get(alias, {}).get('types', ())
                           if (color := COLOR_CODES.get(kind)) is not None)
    goals = []
    for goal in cards:
        if goal['alias'] in player.technologies:
            continue
        unmet = Counter(goal.get('requirements') or '') - owned_colors
        before = sum(unmet.values()) - int(player.faction == 'jolnar' and not is_unit_upgrade(goal))
        if before > 0:
            goals.append((goal['alias'], unmet, before))
    for tech in cards:
        alias = tech['alias']
        if alias in player.technologies or is_unit_upgrade(tech):
            continue
        colors = {COLOR_CODES[kind] for kind in tech.get('types', ()) if kind in COLOR_CODES}
        path = 0.0
        for goal_alias, unmet, before in goals:
            if goal_alias != alias and colors.intersection(unmet):
                path = max(path, immediate.get(goal_alias, 3) * .55 ** before)
        values[alias] = immediate.get(alias, 3) + path
    return values


def production_frontier(controller, tile, player):
    """Return nearest unsettled/enemy planet systems and supply distance (1–3)."""
    neighbors = list(controller.neighbors(tile, player.faction))
    objective_systems = [other for other in [tile, *neighbors] if any(
        other.planet_owners.get(p.planet_id) != player.faction for p in other.planets)]
    supply_distance = 1
    # A home dock remains useful after its neighbors are settled: it supplies
    # the nearest frontier rather than treating a ring of friendly planets as
    # proof that no more transports or ground forces are needed.
    if not objective_systems:
        visited = {tile.position, *(other.position for other in neighbors)}
        ring = neighbors
        for distance in (2, 3):
            next_ring = []
            for origin in ring:
                for other in controller.neighbors(origin, player.faction):
                    if other.position not in visited:
                        visited.add(other.position)
                        next_ring.append(other)
            objective_systems = [other for other in next_ring if any(
                other.planet_owners.get(p.planet_id) != player.faction for p in other.planets)]
            if objective_systems:
                supply_distance = distance
                break
            ring = next_ring
    return objective_systems, supply_distance


def build_plan(controller, session, round_number):
    """Find a useful, affordable mix with no fleet/capacity waste after building."""
    player, tile = session.player, session.target
    exploration = getattr(controller, 'exploration', None)
    budget = (sum(effective_yields(player, card, exploration)[0]
                  for card in player.planets if not card.exhausted) +
              player.trade_goods)
    discount = int('st' in player.technologies and not session.integrated_current
                   and not session.agent_production)
    if session.integrated_current:
        planet = next(p for p in tile.planets
                      if p.planet_id == session.integrated_current)
        card = next((card for card in player.planets
                     if card.planet.planet_id == planet.planet_id), None)
        ceiling = (effective_yields(player, card, exploration)[0] if card else
                   exploration.planet_values(planet)[0] if exploration else planet.resources)
        budget = min(budget, ceiling)
    if budget + discount <= 0 or session.production_limit <= 0:
        return Counter()
    local_units = [unit for unit in tile.units if unit.owner == player.faction]
    local = Counter(unit.kind for unit in local_units)
    empire = Counter(unit.kind for unit in owned_units(player, controller.board))
    neighbors = list(controller.neighbors(tile, player.faction))
    objective_systems, supply_distance = production_frontier(controller, tile, player)
    objectives = [p for other in objective_systems for p in other.planets
                  if other.planet_owners.get(p.planet_id) != player.faction]
    enemies = [u for other in neighbors for u in other.units if u.owner != player.faction]
    if supply_distance > 1:
        enemies.extend(u for other in objective_systems for u in other.units
                       if u.owner != player.faction)
    hostile_ships = [u for u in enemies if UNIT_TYPES[u.kind]['ship']]
    hostile_ground = sum(u.kind in ('infantry', 'mech') for u in enemies)
    hostile_fighters = sum(u.kind == 'fighter' for u in hostile_ships)
    enemy_power = sum((11 - min(11, unit_profile(u).get('combatHitsOn') or 11)) /
                      10 * (unit_profile(u).get('combatDieCount') or 1) +
                      .45 * (1 + bool(unit_profile(u).get('sustainDamage')))
                      for u in hostile_ships) * .8 ** (supply_distance - 1)
    owned_planets = [p for p in tile.planets if tile.planet_owners.get(p.planet_id) == player.faction]
    ground_in_space = not owned_planets
    allow_ground = bool(owned_planets or session.ground_forces_in_space or
                        (player.faction == 'saar' and local['spacedock']))
    ships_blocked = controller.ships_blocked_by_opponents(session)
    profiles = {kind: unit_stats(player, kind) for kind in BUILD_KINDS}
    costs = {kind: controller.unit_cost(kind, player) for kind in BUILD_KINDS}
    reclaimable = {kind: len(controller.production_reclaim_candidates(session, kind))
                   for kind in MODEL_LIMITS}
    capital_count = sum(capital_ship(u) for u in local_units if u.location.region == Region.SPACE)
    base_capacity = controller._capacity_value(tile, player.faction)
    cargo = controller._capacity_units(tile, player.faction)
    transported_ground = sum(u.kind in ('infantry', 'mech') for u in cargo)
    fighters = sum(u.kind == 'fighter' for u in cargo)
    transported_fighters = sum(u.kind == 'fighter' and u.location.region == Region.TRANSPORT
                              for u in cargo)
    dock_slots = 3 * local['spacedock']
    fleet_limit = controller.fleet_supply(player)
    letani = sum(unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                 bool(unit_profile(unit).get('productionValue')) for unit in local_units)
    available = [kind for kind in BUILD_KINDS if production_allowed(player, kind)
                 and (session.production_unit_cost_cap is None or
                      costs[kind] <= session.production_unit_cost_cap)
                 and not (ships_blocked and UNIT_TYPES[kind]['ship'])
                 and (kind not in ('infantry', 'mech') or allow_ground)]
    # Enough to cover nearby planets and one defended invasion, with diminishing
    # value beyond that. Grounded garrisons never consume transport capacity.
    planets_per_landing = max((sum(other.planet_owners.get(p.planet_id) != player.faction
                                  for p in other.planets) for other in objective_systems), default=0)
    invasion_goal = min(6, planets_per_landing + min(3, hostile_ground)) if objectives else 0
    garrison_goal = min(4, len(owned_planets) + int(bool(hostile_ships)))
    threat = min(2.0, .7 + enemy_power / 5)
    invasion_weight = 7.0 * .85 ** (supply_distance - 1) if objectives else 0.0
    existing_power = sum((11 - min(11, unit_profile(u).get('combatHitsOn') or 11)) /
                         10 * (unit_profile(u).get('combatDieCount') or 1) +
                         .45 * (1 + bool(unit_profile(u).get('sustainDamage')))
                         for u in local_units if UNIT_TYPES[u.kind]['ship'])

    def valid(counts):
        total = dict(zip(BUILD_KINDS, counts))
        raw_cost = ceil(sum(costs[k] * n for k, n in total.items()))
        if max(0, raw_cost - discount) > budget:
            return False
        if any(total[k] > max(0, limit - empire[k]) + reclaimable[k]
               for k, limit in MODEL_LIMITS.items()):
            return False
        if player.faction == 'arborec' and total['infantry'] > letani:
            return False
        capitals = capital_count + sum(n for k, n in total.items()
                                       if k not in ('infantry', 'mech', 'fighter'))
        capacity = base_capacity + sum(profiles[k].get('capacityValue', 0) * n
                                       for k, n in total.items())
        ground = transported_ground + (
            total['infantry'] + total['mech'] if ground_in_space else 0)
        if ground > capacity:
            return False
        excess = max(0, ground + max(0, fighters + total['fighter'] - dock_slots) - capacity)
        if 'ff2' in player.technologies and ground + max(0, transported_fighters - dock_slots) > capacity:
            return False
        return (('ff2' in player.technologies or excess == 0) and
                capitals + (excess if 'ff2' in player.technologies else 0) <= fleet_limit)

    def score(counts):
        added = dict(zip(BUILD_KINDS, counts))
        count = local + Counter(added)
        capacity = base_capacity + sum(profiles[k].get('capacityValue', 0) * n
                                       for k, n in added.items())
        troops = count['infantry'] + count['mech']
        reserve = max(0, troops - garrison_goal)
        lift = min(invasion_goal, capacity, reserve)
        ground_value = 3.1 * min(troops, garrison_goal) + invasion_weight * lift
        ground_value += min(count['mech'], max(0, hostile_ground)) * (
            2.5 + 2.0 * .85 ** (supply_distance - 1))
        if player.faction == 'arborec':
            ground_value += 2 * min(troops, 6)
        # Several transports create separate expeditions, but only while there
        # are objectives and troops to carry. Empty carriers are not a goal.
        transports = sum(u.capacity >= 3 and u.location.region == Region.SPACE for u in local_units)
        transports += sum(n for k, n in added.items() if profiles[k].get('capacityValue', 0) >= 3)
        expedition = 2 * min(transports, max(0, (troops - garrison_goal) / 2), len(objectives))
        attack = existing_power + sum(n * ((11 - min(11, profiles[k].get('combatHitsOn') or 11)) /
                       10 * (profiles[k].get('combatDieCount') or 1) +
                       .45 * (1 + bool(profiles[k].get('sustainDamage'))))
                       for k, n in added.items() if UNIT_TYPES[k]['ship'])
        power_goal = max(2.8, enemy_power * 1.35)
        military = threat * (3.5 * min(attack, power_goal) + .6 * max(0, attack - power_goal))
        escort = count['destroyer'] + count['cruiser'] + count['dreadnought'] + count['flagship'] + count['warsun']
        protection = 3.5 * min(1, escort) if transports else 0
        screen = min(count['fighter'], max(0, capacity - min(reserve, invasion_goal)))
        protection += 1.7 * min(screen, max(2, 2 * transports))
        afb = sum((local[k] + added[k]) * max(0, 11 - (profiles[k].get('afbHitsOn') or 11)) /
                  10 * (profiles[k].get('afbDieCount') or 1) for k in BUILD_KINDS)
        protection += 3 * min(hostile_fighters, afb)
        raw_cost = ceil(sum(costs[k] * n for k, n in added.items()))
        cost = max(0, raw_cost - discount) if any(counts) else 0
        # Keeping resources matters when a threat is already covered; this
        # prevents filling every fleet with marginal ships merely because we can.
        large_hull_reserve = (added['flagship'] + added['warsun']) * max(0, 6 - enemy_power)
        return ground_value + expedition + military + protection - cost - large_hull_reserve

    zero = (0,) * len(BUILD_KINDS)
    best, best_score = zero, score(zero)
    beam = [zero]
    seen = {zero}
    max_depth = min(session.production_limit, 16)
    for _ in range(max_depth):
        candidates = []
        for state in beam:
            for kind in available:
                index = BUILD_KINDS.index(kind)
                next_state = (*state[:index], state[index] + 1, *state[index + 1:])
                if next_state in seen:
                    continue
                seen.add(next_state)
                if not valid(next_state):
                    continue
                value = score(next_state)
                candidates.append((value, next_state))
                if value > best_score + 1e-9:
                    best, best_score = next_state, value
        if not candidates:
            break
        beam = [state for _, state in sorted(candidates, reverse=True)[:64]]
    return Counter({kind: count for kind, count in zip(BUILD_KINDS, best) if count})
