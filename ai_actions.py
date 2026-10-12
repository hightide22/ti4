"""Bot decisions for cards and combat, using only public state and its own hand."""
from math import comb

from action_cards import canonical_action_card
from ai_combat import win_probability
from ai_economy import MODEL_LIMITS
from units import Region, UNIT_TYPES, unit_profile


UNIT_VALUE = {'infantry': .5, 'fighter': .5, 'destroyer': 1, 'cruiser': 2,
              'carrier': 3, 'dreadnought': 4, 'flagship': 8, 'warsun': 12,
              'spacedock': 4, 'pds': 2}


def reinforcement_available(ai, player, kind):
    return sum(unit.owner == player.faction and unit.kind == kind
               for tile in ai.window.board.values() for unit in tile.units) < MODEL_LIMITS[kind]


def loss_value(tile, unit, *, sustain=False, assigned=()):
    """Keep dice, transport capacity and loaded ground forces alive."""
    if sustain and not unit.damaged and unit_profile(unit).get('sustainDamage') and \
            unit.unit_id not in assigned:
        return -10.0
    cargo = [other for other in tile.units if other.location.region == Region.TRANSPORT
             and other.location.carrier_id == unit.unit_id]
    value = UNIT_VALUE.get(unit.kind, 1) + 2 * len(cargo)
    if unit.capacity and unit.location.region == Region.SPACE:
        # The last transport is strategically useful even before loading.
        alternatives = [other for other in tile.units if other is not unit and
                        other.owner == unit.owner and other.capacity and
                        other.location.region == Region.SPACE]
        if not alternatives and any(other.owner == unit.owner and other.kind == 'infantry'
                                    for other in tile.units):
            value += 4
    return value


def resolve_card_choice(ai):
    """Finish only a bot's own prompt; never take over a human's choice."""
    cards = ai.window.action_cards
    pending = cards.pending
    if not pending or not ai.is_bot(pending['player']):
        return False
    choices = cards.pending_choices()
    if not choices:
        cards.cancel_pending()
        return True
    session = ai.window.movement.session
    effect = pending['effect']
    if effect in ('direct_hit', 'courageous'):
        units = {unit.unit_id: unit for unit in session.target.units}
        choice = max((key for key, _ in choices), key=lambda key: (
            loss_value(session.target, units[key]), key))
    elif effect == 'war_effort':
        player = pending['player']
        if not reinforcement_available(ai, player, 'cruiser'):
            cards.cancel_pending()
            return True
        legal = [key for key, _ in choices if
                 len(ai.window.movement._fleet_ships_at(ai.window.board[key], player.faction)) <
                 ai.window.movement.fleet_supply(player)]
        if not legal:
            cards.cancel_pending()
            return True
        choice = max(legal, key=lambda key: reinforcement_value(ai, player, ai.window.board[key]))
    elif effect == 'upgrade':
        if not reinforcement_available(ai, pending['player'], 'dreadnought'):
            cards.cancel_pending()
            return True
        legal = [key for key, _ in choices if key not in session.selected]
        if not legal:
            cards.cancel_pending()
            return True
        choice = min(legal)
    else:
        # Every eligible dock fires the same Experimental Battlestation dice.
        choice = choices[0][0]
    cards.resolve_pending(choice)
    return True


def reinforcement_value(ai, player, tile):
    ctl = ai.window.movement
    front = sum(any(owner not in (None, player.faction) for owner in other.planet_owners.values())
                for other in ctl.neighbors(tile, player.faction))
    threats = sum(UNIT_VALUE.get(unit.kind, 1) for other in ctl.neighbors(tile, player.faction)
                  for unit in other.units if unit.owner != player.faction and
                  unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship'])
    return (8 + min(8, threats) + min(4, front * 2) +
            3 * (player.faction not in tile.command_tokens), tile.position)


def war_effort_plan(ai, player):
    cards = ai.window.action_cards
    if not hasattr(cards, 'playable'):
        return None
    playable = [(index, alias) for index, alias in cards.playable(player, None)
                if canonical_action_card(alias) == 'war_effort']
    if not playable or not reinforcement_available(ai, player, 'cruiser'):
        return None
    ctl = ai.window.movement
    eligible = [tile for tile in ai.window.board.values()
                if any(unit.owner == player.faction and unit.location.region == Region.SPACE
                       and UNIT_TYPES[unit.kind]['ship'] for unit in tile.units)
                and len(ctl._fleet_ships_at(tile, player.faction)) < ctl.fleet_supply(player)]
    if not eligible:
        return None
    target = max(eligible, key=lambda tile: reinforcement_value(ai, player, tile))
    return reinforcement_value(ai, player, target)[0], playable[0][0], target.position


def play_useful_card(ai, session):
    cards, ctl = ai.window.action_cards, ai.window.movement
    if getattr(cards, 'pending', None):
        return resolve_card_choice(ai)
    if getattr(session, 'fire_team_pending', None):
        faction = session.fire_team_pending
        if faction not in ai.bot_factions:
            return False
        missed = {index for index, roll in enumerate(session.combat_rolls.get(faction, ()))
                  if not roll['hit']}
        session.fire_team_selected = missed
        if missed:
            cards.resolve_fire_team(faction)
        else:
            session.fire_team_pending = None
        return True
    candidates = []
    for player in cards.participants(session):
        if not ai.is_bot(player):
            continue
        faction = player.faction
        own = ctl.combat_units(session, faction) if faction in session.combat_factions else []
        enemy = [unit for other in session.combat_factions if other != faction
                 for unit in ctl.combat_units(session, other)]
        odds = None
        for index, alias in cards.playable(player, session):
            effect = canonical_action_card(alias)
            score = 0
            if effect in ('morale_boost', 'fighter_prototype', 'skilled_retreat', 'intercept'):
                if odds is None:
                    odds = win_probability(own, enemy, ctl.players,
                                           space=session.combat_type == 'space') if enemy else 1
                if effect == 'skilled_retreat' and odds < .3:
                    score = 120
                elif effect == 'intercept' and odds >= .65:
                    score = 30
                elif effect == 'morale_boost' and .08 <= odds < .9:
                    score = 20 + len(own)
                elif effect == 'fighter_prototype' and odds < .93:
                    fighters = sum(unit.kind == 'fighter' for unit in own)
                    score = 30 + fighters if fighters >= 2 else 0
            elif effect == 'shields_holding':
                hits = session.combat_hits.get(faction, 0)
                free_hits = sum(bool(unit_profile(unit).get('sustainDamage')) and not unit.damaged
                                for unit in own)
                if hits >= 2 or hits > free_hits:
                    score = 90
            elif effect == 'emergency_repairs':
                score = 70
            elif effect in ('direct_hit', 'courageous'):
                score = 100
            elif effect == 'fire_team':
                score = 80 if any(not roll['hit'] for roll in
                                 session.combat_rolls.get(faction, ())) else 0
            elif effect == 'experimental_battlestation':
                score = 50
            elif effect == 'maneuvering_jets':
                first = session.space_cannon_events[0] if session.space_cannon_events else {}
                if first.get('target', session.player.faction) == faction:
                    score = 60
            elif effect == 'bunker':
                score = 45
            elif effect == 'disable' and ctl.landing_forces(session):
                score = 50
            elif effect == 'salvage':
                score = 20 if any(p.commodities for p in ctl.players
                                  if p.faction in session.combat_factions and p is not player) else 0
            elif effect == 'upgrade':
                score = 40 if any(unit.owner == faction and unit.kind == 'cruiser' and
                                  unit.unit_id not in session.selected
                                  for unit in session.target.units) and reinforcement_available(
                                      ai, player, 'dreadnought') else 0
            # Movement bonus cards are used by the route planner, not spent
            # just because a timing window happens to be open.
            if score:
                candidates.append((score, -index, player.faction, player, index, alias))
    if not candidates:
        return False
    _, _, _, player, index, alias = max(candidates, key=lambda item: item[:3])
    cards.play(player, index)
    # These prompts are bot decisions; finish them before exposing another UI frame.
    while getattr(cards, 'pending', None) and ai.is_bot(cards.pending['player']):
        resolve_card_choice(ai)
    ai.report(player, f'Played action card {cards.name_for(alias)}.', .8)
    return True


def plan_bombardment(session):
    """Spread bombardment by marginal expected casualties, avoiding empty planets."""
    faction, tile = session.player.faction, session.target
    defenders = {planet.planet_id: sum(unit.owner != faction and unit.kind == 'infantry' and
                  unit.location.region == Region.PLANET and unit.location.planet_id == planet.planet_id
                  for unit in tile.units) for planet in tile.planets}
    distributions = {planet_id: [1.0] for planet_id, count in defenders.items() if count}
    values = {planet.planet_id: 1 + .15 * planet.resources + .08 * planet.influence
              for planet in tile.planets}
    units = {unit.unit_id: unit for unit in tile.units}
    plasma = int('ps' in session.player.technologies)
    for unit_id in session.bombard_targets:
        profile = unit_profile(units[unit_id])
        dice = int(profile.get('bombardDieCount') or 1) + plasma
        plasma = 0
        options = []
        for planet_id, current in distributions.items():
            penalty = 4 if tile.planet_owners.get(planet_id) in session.bunker_factions else 0
            p = max(0, min(1, (11 - int(profile['bombardHitsOn']) - penalty) / 10))
            rolls = [comb(dice, n) * p ** n * (1 - p) ** (dice - n) for n in range(dice + 1)]
            combined = [0.0] * (len(current) + dice)
            for i, before in enumerate(current):
                for j, after in enumerate(rolls):
                    combined[i + j] += before * after
            count = defenders[planet_id]
            expected = lambda dist: sum(min(hits, count) * chance for hits, chance in enumerate(dist))
            benefit = (expected(combined) - expected(current)) * values[planet_id]
            options.append((benefit, planet_id, combined))
        if options:
            _, planet_id, combined = max(options, key=lambda option: option[:2])
            session.bombard_targets[unit_id] = planet_id
            distributions[planet_id] = combined
        else:
            session.bombard_targets[unit_id] = None
