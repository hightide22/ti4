"""PoK leader effects that can be resolved by the current game systems.

The UI/controller calls these at the printed timing window. Each function
validates its targets before exhausting or purging a leader card.
"""

from __future__ import annotations

import random
from uuid import uuid4

from leaders import exhaust_leader, leader_available, purge_leader
from player import command_tokens_in_reinforcements
from units import (Region, Unit, UnitLocation, UNIT_TYPES, unit_profiles,
                   unit_profile)
from technology import upgrade_profile
from unit_supply import can_transfer_infantry


UNIT_SUPPLY_LIMITS = {
    'fighter': 10, 'infantry': 12, 'mech': 4, 'pds': 6, 'spacedock': 3,
    'carrier': 4, 'cruiser': 8, 'destroyer': 8, 'dreadnought': 5,
    'flagship': 1, 'warsun': 2,
}


def _require_agent(owner, leader_id):
    if not leader_available(owner, leader_id):
        raise ValueError(f'{leader_id} is not ready')


def _require_hero(owner, leader_id):
    if not leader_available(owner, leader_id):
        raise ValueError(f'{leader_id} is not unlocked or was purged')


def use_argent_agent(owner, beneficiary, board, source_tile, produced_units,
                     destinations: dict[str, str]):
    """Move just-produced ground forces to controlled planets nearby.

    ``destinations`` maps produced unit IDs to controlled planet IDs. Units
    omitted from the mapping stay where they were produced.
    """
    _require_agent(owner, 'argentagent')
    if not destinations:
        raise ValueError('Select a ground force to relocate')
    produced = {unit.unit_id: unit for unit in produced_units}
    nearby = [source_tile, *board.neighbors(source_tile.position)]
    planets = {planet.planet_id: tile for tile in nearby for planet in tile.planets
               if tile.planet_owners.get(planet.planet_id) == beneficiary.faction}
    for unit_id, planet_id in destinations.items():
        unit = produced.get(unit_id)
        if (unit is None or unit.owner != beneficiary.faction or
                unit.kind not in ('infantry', 'mech') or
                unit not in source_tile.units or planet_id not in planets):
            raise ValueError('Argent agent can relocate only newly produced ground forces')
    if not can_transfer_infantry(
            beneficiary, board,
            [(source_tile, produced[unit_id], planets[planet_id],
              UnitLocation(Region.PLANET, planet_id))
             for unit_id, planet_id in destinations.items()]):
        raise ValueError('Infantry tokens need a plastic infantry anchor on each destination planet.')
    exhaust_leader(owner, 'argentagent')
    for unit_id, planet_id in destinations.items():
        unit = produced[unit_id]
        destination = planets[planet_id]
        source_tile.units.remove(unit)
        unit.location = UnitLocation(Region.PLANET, planet_id)
        destination.units.append(unit)


def use_cabal_agent(owner, target, kind: str):
    """Convert a replenished player's commodities and capture one unit."""
    _require_agent(owner, 'cabalagent')
    if owner is target or target.commodities <= 0:
        raise ValueError('Choose another player after commodity replenishment')
    definitions, factions = unit_profiles()
    candidates = [definitions[alias] for alias in factions[target.faction]['units']
                  if alias in definitions and definitions[alias].get('baseType') == kind]
    if not candidates:
        raise ValueError('This player has no such unit in reinforcements')
    profile = candidates[0]
    if kind in ('pds', 'spacedock') or float(profile.get('cost', 0)) > target.commodity_limit:
        raise ValueError('The captured unit must cost no more than the commodity value')
    exhaust_leader(owner, 'cabalagent')
    target.trade_goods += target.commodities
    target.commodities = 0
    owner.captured_units[kind] = owner.captured_units.get(kind, 0) + 1


def use_empyrean_agent(owner, beneficiary, board, tile, pool: str):
    """Grant a command token after ships enter a planetless system."""
    _require_agent(owner, 'empyreanagent')
    if tile.planets or pool not in beneficiary.command_pools:
        raise ValueError('The destination must be planetless and the pool valid')
    if not any(unit.owner == beneficiary.faction and unit.location.region == Region.SPACE
               and UNIT_TYPES[unit.kind]['ship'] for unit in tile.units):
        raise ValueError('The beneficiary has no ship in that system')
    if command_tokens_in_reinforcements(beneficiary, board) < 1:
        raise ValueError('No command token remains in reinforcements')
    exhaust_leader(owner, 'empyreanagent')
    beneficiary.command_pools[pool] += 1


def use_mahact_agent(owner, active_player, board, token_tile):
    """Use the active player's on-board token for a strategy secondary."""
    _require_agent(owner, 'mahactagent')
    if active_player is owner or token_tile.position not in board or \
            active_player.faction not in token_tile.command_tokens:
        raise ValueError('Choose an active-player command token on the board')
    exhaust_leader(owner, 'mahactagent')
    token_tile.command_tokens.remove(active_player.faction)


def muaat_agent_systems(beneficiary, board):
    """Systems where Umbat can enable production for this player."""
    return tuple(tile for tile in board.values() if any(
        unit.owner == beneficiary.faction and unit.kind in ('warsun', 'flagship') and
        unit.location.region == Region.SPACE for unit in tile.units))


def use_muaat_agent(owner, beneficiary, tile):
    """Validate Umbat's player/system choice and exhaust the agent."""
    _require_agent(owner, 'muaatagent')
    if not any(unit.owner == beneficiary.faction and unit.kind in ('warsun', 'flagship') and
               unit.location.region == Region.SPACE for unit in tile.units):
        raise ValueError('Choose a system with the selected player’s War Sun or flagship.')
    exhaust_leader(owner, 'muaatagent')
    return True


def use_naaz_agent(owner, beneficiary, exploration, tile, planet_id,
                   *, auto_resolve=False):
    """Explore a controlled non-home planet at the end of a turn."""
    _require_agent(owner, 'naazagent')
    result = exploration.explore_planet(beneficiary, tile, planet_id,
                                        auto_resolve=auto_resolve)
    exhaust_leader(owner, 'naazagent')
    return result


def use_l1z1x_agent(owner, beneficiary, tile, infantry_id, board):
    """Replace one infantry in the newly activated system with a mech.

    Transported infantry can be replaced only when its carrier has enough
    remaining capacity for the heavier mech.
    """
    _require_agent(owner, 'l1z1xagent')
    if owner.faction != 'l1z1x' or tile.position not in board:
        raise ValueError('I48S requires an active L1Z1X agent and an activated system')
    infantry = next((unit for unit in tile.units if unit.unit_id == infantry_id), None)
    if (infantry is None or infantry.owner != beneficiary.faction or
            infantry.kind != 'infantry' or infantry.location.region not in
            (Region.PLANET, Region.TRANSPORT)):
        raise ValueError('Choose one of the active player’s infantry in this system')
    mechs = [unit for system in board.values()
             for unit in system.units if unit.owner == beneficiary.faction and
             unit.kind == 'mech']
    mech_to_redeploy = None
    if len(mechs) >= UNIT_SUPPLY_LIMITS['mech']:
        mech_to_redeploy = next((unit for system in board.values()
                                 if system is not tile and
                                 beneficiary.faction not in system.command_tokens
                                 for unit in system.units
                                 if unit.owner == beneficiary.faction and
                                 unit.kind == 'mech'), None)
        if mech_to_redeploy is None:
            raise ValueError('No mechs remain in that player’s reinforcements or unlocked systems')
    if infantry.location.region == Region.TRANSPORT:
        carrier = next((unit for unit in tile.units
                        if unit.unit_id == infantry.location.carrier_id and
                        unit.owner == beneficiary.faction), None)
        if carrier is None:
            raise ValueError('The infantry’s transport is no longer in the system')
        cargo = [unit for unit in tile.units
                 if unit.location.region == Region.TRANSPORT and
                 unit.location.carrier_id == carrier.unit_id]
        used = sum(int(unit_profile(unit).get('capacityUsed') or 1)
                   for unit in cargo)
        mech_capacity = int(unit_profile(Unit(
            'l1z1x-agent-capacity-check', 'mech', beneficiary.faction,
            beneficiary.color_code, infantry.location)).get('capacityUsed') or 1)
        if used - int(unit_profile(infantry).get('capacityUsed') or 1) + mech_capacity > carrier.capacity:
            raise ValueError('That transport has no room for the replacement mech')
    replacement = Unit(f'{beneficiary.faction}-agent-mech-{infantry_id}',
                       'mech', beneficiary.faction, beneficiary.color_code,
                       infantry.location,
                       profile_id=next((profile_id for profile_id in
                           unit_profiles()[1][beneficiary.faction].get('units', ())
                           if unit_profiles()[0].get(profile_id, {}).get('baseType') == 'mech'
                           and unit_profiles()[0][profile_id].get('requiredTechId') in
                               beneficiary.technologies), None))
    exhaust_leader(owner, 'l1z1xagent')
    tile.units.remove(infantry)
    if mech_to_redeploy is not None:
        source = next(system for system in board.values()
                      if mech_to_redeploy in system.units)
        source.units.remove(mech_to_redeploy)
    tile.units.append(replacement)
    return replacement


def l1z1x_agent_mech_available(beneficiary, tile, board):
    """Whether I48S can place a mech from reinforcements or an unlocked system."""
    mechs = [unit for system in board.values() for unit in system.units
             if unit.owner == beneficiary.faction and unit.kind == 'mech']
    if len(mechs) < UNIT_SUPPLY_LIMITS['mech']:
        return True
    return any(system is not tile and beneficiary.faction not in system.command_tokens
               and any(unit.owner == beneficiary.faction and unit.kind == 'mech'
                       for unit in system.units)
               for system in board.values())


def use_saar_agent(owner, beneficiary, board, ship_id):
    """Return the move value Captain Mendosa grants for this activation."""
    _require_agent(owner, 'saaragent')
    ship = next((unit for tile in board.values() for unit in tile.units
                 if unit.unit_id == ship_id), None)
    if (ship is None or ship.owner != beneficiary.faction or
            ship.location.region != Region.SPACE or
            not UNIT_TYPES[ship.kind]['ship']):
        raise ValueError('Choose one of the active player’s ships on the board')
    highest = max((unit.move_value for tile in board.values() for unit in tile.units
                   if unit.location.region == Region.SPACE and
                   UNIT_TYPES[unit.kind]['ship']), default=0)
    exhaust_leader(owner, 'saaragent')
    return highest


def use_ghost_agent(owner, tile, board):
    """Exhaust the Ghost agent to link a wormhole system to all wormholes."""
    _require_agent(owner, 'ghostagent')
    if tile.position not in board or not any(
            str(wormhole).upper() != 'DELTA' for wormhole in tile.wormholes):
        raise ValueError('The activated system must contain a non-delta wormhole')
    exhaust_leader(owner, 'ghostagent')


def arborec_agent_replacements(target, ship, board):
    """Legal non-fighter ship types from a player's reinforcements."""
    definitions, factions = unit_profiles()
    old_cost = float(unit_profile(ship).get('cost') or 0)
    kinds = {definitions[profile_id]['baseType']
             for profile_id in factions.get(target.faction, {}).get('units', ())
             if profile_id in definitions and
             definitions[profile_id].get('baseType') in UNIT_TYPES and
             UNIT_TYPES[definitions[profile_id]['baseType']]['ship'] and
             definitions[profile_id]['baseType'] != 'fighter' and
             float(definitions[profile_id].get('cost') or 0) <= old_cost + 2}
    counts = {kind: sum(unit.owner == target.faction and unit.kind == kind
                        for tile in board.values() for unit in tile.units)
              for kind in kinds}
    if ship.owner == target.faction and ship.kind in counts:
        counts[ship.kind] -= 1
    return tuple(sorted(kind for kind in kinds
                        if counts[kind] < UNIT_SUPPLY_LIMITS[kind]))


def use_arborec_agent(owner, target, board, ship_id, replacement_kind):
    """Replace a player's non-fighter ship with one of its legal reinforcements."""
    _require_agent(owner, 'arborecagent')
    if owner.faction != 'arborec':
        raise ValueError('Letani Ospha belongs to the Arborec')
    system, ship = next(((tile, unit) for tile in board.values() for unit in tile.units
                        if unit.unit_id == ship_id), (None, None))
    if (system is None or ship.owner != target.faction or ship.kind == 'fighter' or
            ship.location.region != Region.SPACE or not UNIT_TYPES[ship.kind]['ship']):
        raise ValueError('Choose a player’s non-fighter ship in space')
    if replacement_kind not in arborec_agent_replacements(target, ship, board):
        raise ValueError('That replacement ship is unavailable or costs too much')
    new_profile = upgrade_profile(target, replacement_kind)
    replacement = Unit(f'{target.faction}-arborec-agent-{uuid4().hex}',
                       replacement_kind, target.faction, target.color_code,
                       UnitLocation(Region.SPACE),
                       profile_id=(new_profile or {}).get('id'))
    cargo = [unit for unit in system.units
             if unit.location.region == Region.TRANSPORT and
             unit.location.carrier_id == ship.unit_id]
    cargo_used = sum(int(unit_profile(unit).get('capacityUsed') or 1)
                     for unit in cargo)
    if cargo_used > replacement.capacity:
        raise ValueError('The replacement ship does not have enough capacity for its cargo')
    exhaust_leader(owner, 'arborecagent')
    system.units.remove(ship)
    system.units.append(replacement)
    for unit in cargo:
        unit.location = UnitLocation(Region.TRANSPORT, carrier_id=replacement.unit_id)
    return replacement


def xxcha_agent_remove_options(owner, board, planet_id):
    """Infantry that can be removed when readying an adjacent planet."""
    tile = next((tile for tile in board.values()
                 if any(planet.planet_id == planet_id for planet in tile.planets)), None)
    if tile is None or not any(
            neighbor.planet_owners.get(planet.planet_id) == owner.faction
            for neighbor in board.neighbors(tile.position) for planet in neighbor.planets):
        return ()
    return tuple(unit for unit in tile.units if unit.kind == 'infantry' and
                 unit.location.region == Region.PLANET and
                 unit.location.planet_id == planet_id)


def use_xxcha_agent(owner, board, planet_id, *, remove_infantry_id=None):
    """Ready a controlled planet; optionally return adjacent-system infantry."""
    _require_agent(owner, 'xxchaagent')
    card = next((card for card in owner.planets
                 if card.planet.planet_id == planet_id), None)
    if card is None or not card.exhausted:
        raise ValueError('Choose one of your exhausted planets')
    infantry = None
    if remove_infantry_id is not None:
        infantry = next((unit for unit in xxcha_agent_remove_options(owner, board, planet_id)
                         if unit.unit_id == remove_infantry_id), None)
        if infantry is None:
            raise ValueError('That infantry cannot be removed by this agent')
    exhaust_leader(owner, 'xxchaagent')
    card.exhausted = False
    if infantry is not None:
        tile = next(tile for tile in board.values() if infantry in tile.units)
        tile.units.remove(infantry)
    return infantry


def use_nomad_artuno(owner, gained_from_supply: int):
    """Hold matching trade goods on Artuno until it next readies."""
    _require_agent(owner, 'nomadagentartuno')
    if gained_from_supply <= 0:
        raise ValueError('Artuno requires trade goods gained from the supply')
    exhaust_leader(owner, 'nomadagentartuno')
    owner.leaders['nomadagentartuno'].stored_trade_goods += gained_from_supply


def use_nomad_mercer(owner, beneficiary, board, active_tile,
                     destinations: dict[str, str]):
    """Redeploy up to two ground forces to controlled active-system planets."""
    _require_agent(owner, 'nomadagentmercer')
    if not 1 <= len(destinations) <= 2:
        raise ValueError('Mercer moves one or two ground forces')
    valid_planets = {planet.planet_id for planet in active_tile.planets
                     if active_tile.planet_owners.get(planet.planet_id) == beneficiary.faction}
    found = {}
    for tile in board.values():
        for unit in tile.units:
            if unit.unit_id in destinations:
                found[unit.unit_id] = (tile, unit)
    if set(found) != set(destinations):
        raise ValueError('Ground force no longer exists')
    if any(unit.owner != beneficiary.faction or unit.kind not in ('infantry', 'mech') or
           planet_id not in valid_planets
           for unit_id, planet_id in destinations.items() for _, unit in (found[unit_id],)):
        raise ValueError('Mercer requires owned ground forces and controlled planets')
    if not can_transfer_infantry(
            beneficiary, board,
            [(found[unit_id][0], found[unit_id][1], active_tile,
              UnitLocation(Region.PLANET, planet_id))
             for unit_id, planet_id in destinations.items()]):
        raise ValueError('Infantry tokens need a plastic infantry anchor on each destination planet.')
    exhaust_leader(owner, 'nomadagentmercer')
    for unit_id, planet_id in destinations.items():
        tile, unit = found[unit_id]
        tile.units.remove(unit)
        unit.location = UnitLocation(Region.PLANET, planet_id)
        active_tile.units.append(unit)


def use_nomad_thundarian(owner) -> bool:
    """Caller discards both sides' current rolls and repeats the Roll Dice step."""
    return exhaust_leader(owner, 'nomadagentthundarian')


def use_titans_agent(owner, hits: int) -> int:
    """Cancel one incoming hit before it is assigned."""
    _require_agent(owner, 'titansagent')
    if hits < 1:
        raise ValueError('No hit to cancel')
    exhaust_leader(owner, 'titansagent')
    return hits - 1


def use_keleres_agent(owner, beneficiary, commodity_payment: int) -> int:
    """Spend commodities directly in place of trade goods for this payment."""
    _require_agent(owner, 'keleresagent')
    if commodity_payment < 1 or commodity_payment > beneficiary.commodities:
        raise ValueError('Not enough commodities for this payment')
    exhaust_leader(owner, 'keleresagent')
    beneficiary.commodities -= commodity_payment
    return commodity_payment


def use_naaz_hero(owner, exploration):
    """Gain a relic; caller then offers up to two strategy secondaries."""
    _require_hero(owner, 'naazhero')
    outcome = exploration.relics.draw(owner.faction)
    purge_leader(owner, 'naazhero')
    return outcome


def use_empyrean_hero(owner, exploration, board):
    """Place frontier tokens and return owned-ship systems to explore."""
    _require_hero(owner, 'empyreanhero')
    eligible = []
    for tile in board.values():
        if tile.planets:
            continue
        tokens = exploration.space_tokens.setdefault(tile.position, [])
        if 'frontier' not in tokens:
            tokens.append('frontier')
        if any(unit.owner == owner.faction and unit.location.region == Region.SPACE and
               UNIT_TYPES[unit.kind]['ship'] for unit in tile.units):
            eligible.append(tile)
    purge_leader(owner, 'empyreanhero')
    # Each returned tile is explored through ExplorationController.explore_frontier.
    # The caller resolves card choices before proceeding to the next tile.
    return tuple(eligible)


def use_mahact_hero(owner, board, source_tile, destination_tile, *, neighbors=None):
    """Force two fleets together; caller begins combat without retreat or movement effects.

    ``neighbors`` can be the movement controller's faction-aware adjacency
    function so special wormhole connections are honored.
    """
    _require_hero(owner, 'mahacthero')
    if source_tile.position not in board or destination_tile.position not in board:
        raise ValueError('Choose systems on the game board')
    adjacent = (neighbors(source_tile.position) if neighbors else
                board.neighbors(source_tile.position))
    if destination_tile not in adjacent:
        raise ValueError('The destination must be adjacent')
    moving = [unit for unit in source_tile.units if unit.location.region == Region.SPACE]
    attacker_owners = {unit.owner for unit in moving if UNIT_TYPES[unit.kind]['ship']}
    defender_owners = {unit.owner for unit in destination_tile.units
                       if unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']}
    if len(attacker_owners) != 1 or len(defender_owners) != 1 or \
            attacker_owners == defender_owners:
        raise ValueError('Choose adjacent systems with different players’ ships')
    carrier_ids = {unit.unit_id for unit in moving}
    cargo = [unit for unit in source_tile.units
             if unit.location.region == Region.TRANSPORT and
             unit.location.carrier_id in carrier_ids]
    for faction in {unit.owner for unit in cargo if unit.kind == 'infantry'}:
        if not can_transfer_infantry(
                faction, board,
                [(source_tile, unit, destination_tile, unit.location)
                 for unit in cargo if unit.owner == faction]):
            raise ValueError('Infantry tokens need a plastic infantry anchor at the destination.')
    for unit in (*moving, *cargo):
        source_tile.units.remove(unit)
        destination_tile.units.append(unit)
    purge_leader(owner, 'mahacthero')
    return next(iter(attacker_owners)), next(iter(defender_owners))


def use_keleres_kuuasi_hero(owner, board, tile, ship_kinds: tuple[str, ...]):
    """Place the flagship and up to two cruisers/destroyers at combat start."""
    _require_hero(owner, 'keleresherokuuasi')
    if (owner.faction != 'keleresa' or tile.position not in board or
            not any(tile.planet_owners.get(planet.planet_id) == owner.faction
                    for planet in tile.planets) or
            any(unit.owner == owner.faction and unit.kind == 'flagship'
                for system in board.values() for unit in system.units) or
            len(ship_kinds) > 2 or
            any(kind not in ('cruiser', 'destroyer') for kind in ship_kinds)):
        raise ValueError('Kuuasi requires a controlled planet and up to two escorts')
    placed = []
    for kind in ('flagship', *ship_kinds):
        unit = Unit(f'{owner.faction}-{kind}-{uuid4().hex}', kind, owner.faction,
                    owner.color_code, UnitLocation(Region.SPACE))
        tile.units.append(unit)
        placed.append(unit)
    purge_leader(owner, 'keleresherokuuasi')
    return tuple(placed)


def use_keleres_harka_hero(owner, action_deck, *, rng=None):
    """Draw the next three component-action cards and reshuffle the rest."""
    _require_hero(owner, 'keleresheroharka')
    from action_cards import ACTION_CARD_DEFS, CARD_EFFECT_ALIASES
    drawn, revealed = [], []
    while action_deck.cards and len(drawn) < 3:
        alias = action_deck.draw()
        definition = ACTION_CARD_DEFS.get(CARD_EFFECT_ALIASES.get(alias, alias), {})
        if definition.get('window', '').lower() == 'as an action':
            drawn.append(alias)
        else:
            revealed.append(alias)
    action_deck.cards.extend(revealed)
    (rng or random).shuffle(action_deck.cards)
    owner.action_cards.extend(drawn)
    purge_leader(owner, 'keleresheroharka')
    return tuple(drawn)


def use_nomad_hero(owner):
    """Allow the flagship and transported units to leave activated systems."""
    _require_hero(owner, 'nomadhero')
    # The hero stays beside the board until the end of this game round.
    owner.leaders['nomadhero'].exhausted = True
    owner.hero_round_flags.add('nomad_command_token_exit')


def use_titans_hero(owner):
    """Attach Ul to Elysium and ready its planet card."""
    _require_hero(owner, 'titanshero')
    card = next((card for card in owner.planets if card.planet.planet_id == 'elysium'), None)
    if card is None:
        raise ValueError('Titans hero requires Elysium under your control')
    purge_leader(owner, 'titanshero')
    card.exhausted = False
    owner.hero_attachments.add('elysium')


def clear_round_hero_effects(player):
    if 'nomad_command_token_exit' in player.hero_round_flags:
        player.leaders['nomadhero'].purged = True
    player.hero_round_flags.clear()


def cabal_hero_targets(board, cabal):
    """List non-fighter ships threatened by a Dimensional Tear."""
    tears = [tile for tile in board.values() if any(
        unit.owner == cabal.faction and unit.kind == 'spacedock' for unit in tile.units)]
    threatened = {tile.position for tear in tears
                  for tile in (tear, *board.neighbors(tear.position))}
    return [(tile, unit) for tile in board.values() if tile.position in threatened
            for unit in tile.units if unit.owner != cabal.faction and
            unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship'] and
            unit.kind != 'fighter']


def use_cabal_hero(owner, board, rng):
    """Roll for each threatened ship, capturing results of 1–3 and their cargo."""
    _require_hero(owner, 'cabalhero')
    results = []
    for tile, ship in cabal_hero_targets(board, owner):
        roll = rng.randint(1, 10)
        results.append((ship.unit_id, roll))
        if roll > 3:
            continue
        cargo = [unit for unit in tile.units if unit.location.region == Region.TRANSPORT and
                 unit.location.carrier_id == ship.unit_id]
        for unit in (ship, *cargo):
            tile.units.remove(unit)
            owner.captured_units[unit.kind] = owner.captured_units.get(unit.kind, 0) + 1
    purge_leader(owner, 'cabalhero')
    return tuple(results)
