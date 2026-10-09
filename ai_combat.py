"""Deterministic odds estimates for the AI's proposed battles.

These previews never consume the game's dice. They model simultaneous combat
rolls, sustain damage and the opening anti-fighter barrage over a fixed set of
trials. They are deliberately conservative about ties and prolonged battles.
"""

from functools import lru_cache
from random import Random

from units import unit_profile


LOSS_VALUE = {'fighter': 0, 'infantry': 0, 'destroyer': 2, 'cruiser': 3,
              'carrier': 5, 'dreadnought': 6, 'flagship': 8, 'warsun': 10}
TRIALS = 192
MAX_ROUNDS = 12


def _spec(unit, technologies, carries_ground=False):
    profile = unit_profile(unit)
    sustain = bool(profile.get('sustainDamage')) and not unit.damaged
    hp = 1 + (2 if 'nes' in technologies else 1) * sustain
    threshold = int(profile.get('combatHitsOn') or 11) + (unit.owner == 'jolnar')
    return (unit.kind, threshold, int(profile.get('combatDieCount') or 1), hp,
            int(profile.get('afbHitsOn') or 11), int(profile.get('afbDieCount') or 1),
            unit.capacity, bool(carries_ground))


def _roll_hits(fleet, rng):
    return sum(rng.randrange(1, 11) >= spec[1]
               for spec in fleet for _ in range(spec[2]))


def _take_hits(fleet, hits, *, nonfighters=False):
    for _ in range(hits):
        if not fleet:
            break
        # Sustaining a hit keeps a die in the next combat round. Once every
        # sustain is spent, remove the least valuable unit first.
        candidates = [index for index, spec in enumerate(fleet)
                      if not nonfighters or spec[0] != 'fighter']
        if not candidates:
            candidates = range(len(fleet))
        victim = min(candidates, key=lambda index: (
            fleet[index][3] <= 1, LOSS_VALUE.get(fleet[index][0], 4), index))
        fleet[victim][3] -= 1
        if fleet[victim][3] == 0:
            fleet.pop(victim)


def _barrage(firing, targets, rng):
    if not any(spec[0] == 'fighter' for spec in targets):
        return 0
    return sum(rng.randrange(1, 11) >= spec[4]
               for spec in firing if spec[4] <= 10 for _ in range(spec[5]))


@lru_cache(maxsize=512)
def _estimate(attacker_specs, defender_specs, space, cannon_specs, require_transport,
              linked_transport):
    transport_index = 7 if linked_transport else 6
    if require_transport and not any(spec[transport_index] for spec in attacker_specs):
        return 0.0
    if not defender_specs and not cannon_specs:
        return 1.0
    if not attacker_specs:
        return 0.0
    rng = Random(0x7144)
    wins = 0
    for _ in range(TRIALS):
        attackers = [list(spec) for spec in attacker_specs]
        defenders = [list(spec) for spec in defender_specs]
        for threshold, dice, graviton in cannon_specs:
            _take_hits(attackers, sum(rng.randrange(1, 11) >= threshold
                                      for _ in range(dice)), nonfighters=graviton)
        if space:
            attacker_barrage = _barrage(attackers, defenders, rng)
            defender_barrage = _barrage(defenders, attackers, rng)
            for fleet, hits in ((defenders, attacker_barrage), (attackers, defender_barrage)):
                for _ in range(hits):
                    fighter = next((index for index, spec in enumerate(fleet)
                                    if spec[0] == 'fighter'), None)
                    if fighter is None:
                        break
                    fleet.pop(fighter)
        for _ in range(MAX_ROUNDS):
            if not attackers or not defenders:
                break
            attack_hits = _roll_hits(attackers, rng)
            defense_hits = _roll_hits(defenders, rng)
            _take_hits(defenders, attack_hits)
            _take_hits(attackers, defense_hits)
        if attackers and not defenders and (not require_transport or
                                            any(spec[transport_index] for spec in attackers)):
            wins += 1
    return wins / TRIALS


def win_probability(attackers, defenders, players, *, space=True, cannons=(),
                    require_transport=False, cargo_carriers=None):
    """Estimate survival and victory, optionally requiring a loaded carrier to live.

    ``cargo_carriers`` contains IDs of ships carrying the required ground
    forces.  When omitted, the older capacity-only transport check applies.
    """
    technologies = {player.faction: player.technologies for player in players}
    carrier_ids = set(cargo_carriers) if cargo_carriers is not None else None
    attacker_specs = tuple(sorted(
        (_spec(unit, technologies.get(unit.owner, ()),
               carrier_ids is not None and unit.unit_id in carrier_ids)
         for unit in attackers), key=lambda spec: (*spec[:7], -spec[7])))
    defender_specs = tuple(sorted(_spec(unit, technologies.get(unit.owner, ()))
                                  for unit in defenders))
    attacker_tech = technologies.get(attackers[0].owner, ()) if attackers else ()
    plasma_used = set()
    cannon_specs = []
    for cannon in cannons:
        profile = unit_profile(cannon)
        cannon_tech = technologies.get(cannon.owner, ())
        plasma = int('ps' in cannon_tech and cannon.owner not in plasma_used)
        if plasma:
            plasma_used.add(cannon.owner)
        cannon_specs.append((int(profile['spaceCannonHitsOn']) + int('amd' in attacker_tech),
                             int(profile.get('spaceCannonDieCount') or 1) + plasma,
                             'gls' in cannon_tech))
    return _estimate(attacker_specs, defender_specs, space, tuple(sorted(cannon_specs)),
                     require_transport, carrier_ids is not None)
