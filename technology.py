"""Technology cards, prerequisites, and researched unit profiles."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
from urllib.parse import unquote
from uuid import uuid4

from board import RESOURCES
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profiles


COLORS = {'B': 'PROPULSION', 'G': 'BIOTIC', 'R': 'WARFARE', 'Y': 'CYBERNETIC'}
COLOR_CODES = {name: code for code, name in COLORS.items()}
FACTIONS = {'sol', 'jolnar', 'letnev', 'hacan'}


@lru_cache(maxsize=1)
def technology_catalog():
    records = json.loads((RESOURCES / 'data/technologies/pok.json').read_text(encoding='utf-8'))
    return {tech['alias']: tech for tech in records
            if tech.get('source') in ('base', 'pok') and
            (not tech.get('faction') or tech['faction'] in FACTIONS)}


def technology_image(tech):
    relative = unquote(tech['imageURL'].split('/resources/', 1)[1].split('?', 1)[0])
    return RESOURCES / relative


def is_unit_upgrade(tech):
    return 'UNITUPGRADE' in tech.get('types', ())


def available_technologies(player):
    catalog = technology_catalog()
    replacements = {tech['baseUpgrade'] for tech in catalog.values()
                    if tech.get('faction') == player.faction and tech.get('baseUpgrade')}
    return [tech for tech in catalog.values()
            if tech.get('faction') in (None, player.faction) and tech['alias'] not in replacements]


def upgrade_profile(player, kind):
    """Return the researched profile for a kind, including faction replacements."""
    definitions, _ = unit_profiles()
    choices = [profile for profile in definitions.values()
               if profile.get('baseType') == kind and profile.get('requiredTechId') in player.technologies
               and profile.get('faction') in (None, player.faction)]
    if not choices:
        return None
    choices.sort(key=lambda profile: profile.get('faction') == player.faction, reverse=True)
    return choices[0]


def unit_upgrade(player, kind):
    """The technology card that upgrades this player's unit kind, if any."""
    definitions, _ = unit_profiles()
    for tech in available_technologies(player):
        if is_unit_upgrade(tech) and any(
                profile.get('baseType') == kind and profile.get('requiredTechId') == tech['alias']
                and profile.get('faction') in (None, player.faction)
                for profile in definitions.values()):
            return tech
    return None


def research_technology(player, alias, board):
    tech = next((card for card in available_technologies(player) if card['alias'] == alias), None)
    if tech is None or alias in player.technologies:
        raise ValueError('This technology is unavailable or already researched.')
    player.technologies = player.technologies | {alias}
    if is_unit_upgrade(tech):
        for tile in board.values():
            for unit in tile.units:
                if unit.owner == player.faction:
                    profile = upgrade_profile(player, unit.kind)
                    if profile:
                        unit.profile_id = profile['id']
    return tech


def _specialty_coverage(missing, specialties):
    """Find the most prerequisites that selected specialty planets can cover."""
    best = len(missing)

    def visit(index, needed):
        nonlocal best
        if index == len(specialties):
            best = min(best, len(needed))
            return
        visit(index + 1, needed)
        for code in set(specialties[index]):
            if code in needed:
                remainder = needed.copy()
                remainder.remove(code)
                visit(index + 1, remainder)

    visit(0, list(missing))
    return best


def missing_prerequisites(player, tech, specialty_planets=(), use_aida=False):
    """Return how many requirements remain after owned colors and chosen skips."""
    owned = Counter()
    for alias in player.technologies:
        card = technology_catalog().get(alias)
        if card and not is_unit_upgrade(card):
            owned.update(COLOR_CODES[typ] for typ in card.get('types', ()) if typ in COLOR_CODES)
    required = Counter(tech.get('requirements', ''))
    missing = list((required - owned).elements())
    specialties = [tuple(COLOR_CODES[typ] for typ in card.planet.tech_specialties
                         if typ in COLOR_CODES) for card in specialty_planets]
    remaining = _specialty_coverage(missing, specialties)
    if player.faction == 'jolnar' and not is_unit_upgrade(tech):
        remaining -= 1
    if use_aida and is_unit_upgrade(tech) and 'aida' in player.technologies and \
            'aida' not in player.exhausted_technologies:
        remaining -= 1
    return max(0, remaining)


def unit_stats(player, kind):
    """Current values shown on the unit sheet, including faction base units."""
    definitions, factions = unit_profiles()
    upgraded = upgrade_profile(player, kind)
    if upgraded:
        return upgraded
    for profile_id in factions[player.faction].get('units', ()):
        profile = definitions.get(profile_id)
        if profile and profile.get('baseType') == kind:
            return profile
    return definitions[kind]


def production_allowed(player, kind):
    return kind in UNIT_TYPES and (kind != 'warsun' or 'ws' in player.technologies)


def restore_infantry_on_cards(player, board):
    """Place Infantry II / Spec Ops II survivors in a controlled home system."""
    options = [(tile, planet) for tile in board.values() for planet in tile.planets
               if planet.faction_homeworld == player.faction and
               tile.planet_owners.get(planet.planet_id) == player.faction]
    if not options:
        return 0
    tile, planet = options[0]
    placed = sum(unit.owner == player.faction and unit.kind == 'infantry'
                 for system in board.values() for unit in system.units)
    count = min(sum(player.infantry_on_cards.values()), max(0, 12 - placed))
    for _ in range(count):
        tile.units.append(Unit(f'{player.faction}-returned-{uuid4().hex}', 'infantry',
                               player.faction, player.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id),
                               profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
    remaining = count
    for alias in tuple(player.infantry_on_cards):
        taken = min(remaining, player.infantry_on_cards[alias])
        remaining -= taken
        player.infantry_on_cards[alias] -= taken
        if not player.infantry_on_cards[alias]:
            del player.infantry_on_cards[alias]
    return count
