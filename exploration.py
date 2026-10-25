"""Prophecy of Kings planetary/frontier exploration.

Exploration is a distinct state machine: drawing a card may require a choice,
and the card is moved to its final zone only after that choice is resolved.
The caller handles the timing window (newly controlled *uncontrolled* planet,
or a separate explore ability) and shows any returned events to the player.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import lru_cache
import json
from math import ceil
import random

from board import Planet, RESOURCES
from leaders import leader_available
from player import PlanetCard, command_tokens_in_reinforcements
from relics import RelicController, RelicOutcome
from secret_objectives import SecretObjectiveController
from technology import production_allowed, upgrade_profile
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profile
from unit_supply import can_place_infantry, can_transfer_infantry


TRAITS = ('cultural', 'hazardous', 'industrial', 'frontier')
EXPLORATION_DATA_FILES = ('explores.json', 'codex3.json')
_SOURCE_PLANET = object()
RESEARCH_FACILITIES = {
    'warfare': 'WARFARE', 'biotic': 'BIOTIC',
    'cybernetic': 'CYBERNETIC', 'propulsion': 'PROPULSION',
}
ATTACHMENT_BONUSES = {
    'ds': (2, 1), 'pw': (0, 2), 'toe': (0, 1),
    'ls': (1, 2), 'mw': (2, 0), 'rw': (1, 0),
    'nanoforge': (2, 2),
}
ATTACHMENT_TOKEN_STEMS = {
    'dmz': 'dmz', 'ds': 'dysonsphere', 'pw': 'paradiseworld',
    'toe': 'tombofemphidia', 'ls': 'lazaxsurvivors', 'mw': 'miningworld',
    'rw': 'richworld', 'nanoforge': 'nanoforge',
    'warfare': 'warfare', 'biotic': 'biotic',
    'cybernetic': 'cybernetic', 'propulsion': 'propulsion',
}
FRONTIER_COMMAND_AND_GOODS = {
    'minent': (1, 1),
    'ent': (1, 2),
    'majent': (1, 3),
    'kel1': (2, 0),
    'kel2': (2, 0),
}


@dataclass(frozen=True)
class ExplorationCard:
    alias: str
    name: str
    trait: str
    resolution: str
    text: str
    source: str


@lru_cache(maxsize=1)
def exploration_catalog() -> dict[str, ExplorationCard]:
    catalog = {}
    for filename in EXPLORATION_DATA_FILES:
        path = RESOURCES / 'data/explores' / filename
        for data in json.loads(path.read_text(encoding='utf-8')):
            card = ExplorationCard(data['id'], data['name'], data['type'].lower(),
                                   data['resolution'].lower(), data['text'], data['source'])
            if card.alias in catalog or card.trait not in TRAITS:
                raise ValueError(f'Invalid exploration card: {card.alias}')
            catalog[card.alias] = card
    return catalog


@dataclass(frozen=True)
class ExplorationOutcome:
    card: ExplorationCard | None
    choices: tuple[str, ...] = ()
    events: tuple[dict, ...] = ()
    resolved: bool = True
    offered_cards: tuple[ExplorationCard, ...] = ()


@dataclass(frozen=True)
class PendingExploration:
    faction: str
    position: tuple[int, int]
    planet_id: str | None
    alias: str
    choices: tuple[str, ...]
    offered_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingTerragenesis:
    faction: str
    position: tuple[int, int]
    planet_id: str


class ExplorationController:
    def __init__(self, board, players, *, rng=None, action_cards=None,
                 decks=None, relic_deck=None, secret_objective_deck=None):
        self.board = board
        self.players = {player.faction: player for player in players}
        self.rng = rng or random.Random()
        self.action_cards = action_cards
        self.catalog = exploration_catalog()
        self.decks: dict[str, list[str]] = {
            trait: list(aliases) for trait, aliases in
            (decks or {trait: [card.alias for card in self.catalog.values()
                              if card.trait == trait] for trait in TRAITS}).items()
        }
        if decks is None:
            for deck in self.decks.values():
                self.rng.shuffle(deck)
        if set(self.decks) != set(TRAITS) or any(
            alias not in self.catalog or self.catalog[alias].trait != trait
            for trait, deck in self.decks.items() for alias in deck
        ):
            raise ValueError('Exploration decks contain an unknown or wrong-trait card.')
        self.discards: dict[str, list[str]] = {trait: [] for trait in TRAITS}
        self.purged: set[str] = set()
        self.attachments: dict[str, list[str]] = {}
        self.terragenesis_pending: PendingTerragenesis | None = None
        self.attached_relics: dict[str, str] = {}
        self.legendary_planets: set[str] = set()
        self.destroyed_planets: set[str] = set()
        self.space_tokens: dict[tuple[int, int], list[str]] = {
            tile.position: ['frontier'] for tile in board.values() if not tile.planets
        }
        self.fragments: dict[str, list[str]] = {faction: [] for faction in self.players}
        self.kept_cards: dict[str, list[str]] = {faction: [] for faction in self.players}
        self.common_cards: list[str] = []
        self.pending: PendingExploration | None = None
        self.freelancer_offer: tuple[str, tuple[int, int], str | None] | None = None
        self.next_unit_number = 1
        self.relics = RelicController(players, rng=self.rng, deck=relic_deck)
        self.secret_objectives = SecretObjectiveController(
            players, rng=self.rng, deck=secret_objective_deck)

    def snapshot(self):
        return deepcopy((self.decks, self.discards, self.purged, self.attachments,
                         self.attached_relics, self.legendary_planets,
                         self.destroyed_planets, self.terragenesis_pending,
                         self.space_tokens, self.fragments, self.kept_cards,
                         self.common_cards, self.pending, self.freelancer_offer,
                         self.next_unit_number,
                         {tile.position: tile.definition for tile in self.board.values()},
                         self.relics.snapshot(), self.secret_objectives.snapshot()))

    def restore(self, snapshot):
        (self.decks, self.discards, self.purged, self.attachments,
         self.attached_relics, self.legendary_planets,
         self.destroyed_planets, self.terragenesis_pending,
         self.space_tokens, self.fragments, self.kept_cards,
         self.common_cards, self.pending, self.freelancer_offer,
         self.next_unit_number,
         definitions, relic_snapshot, secret_snapshot) = deepcopy(snapshot)
        for position, definition in definitions.items():
            if position in self.board:
                self.board[position].definition = definition
        self.relics.restore(relic_snapshot)
        self.secret_objectives.restore(secret_snapshot)

    def planet_values(self, planet: Planet) -> tuple[int, int]:
        resources, influence = planet.resources, planet.influence
        specialties = set(planet.tech_specialties)
        for alias in self.attachments.get(planet.planet_id, ()):
            extra_resources, extra_influence = ATTACHMENT_BONUSES.get(alias, (0, 0))
            if alias in RESEARCH_FACILITIES:
                if specialties:
                    extra_resources, extra_influence = 1, 1
                else:
                    specialties.add(RESEARCH_FACILITIES[alias])
            resources += extra_resources
            influence += extra_influence
        return resources, influence

    def planet_specialties(self, planet: Planet) -> tuple[str, ...]:
        specialties = list(planet.tech_specialties)
        for alias in self.attachments.get(planet.planet_id, ()):
            if alias in RESEARCH_FACILITIES and not specialties:
                specialties.append(RESEARCH_FACILITIES[alias])
        return tuple(dict.fromkeys(specialties))

    def planet_is_blocked(self, planet_id: str) -> bool:
        return 'dmz' in self.attachments.get(planet_id, ())

    def planet_is_legendary(self, planet: Planet) -> bool:
        if planet.planet_id in self.legendary_planets:
            return True
        path = RESOURCES / 'planets' / f'{planet.planet_id}.json'
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding='utf-8'))
        return bool(data.get('legendaryAbilityName'))

    def _draw(self, trait: str) -> ExplorationCard | None:
        if trait not in TRAITS:
            raise ValueError(f'Unknown exploration trait: {trait}')
        deck = self.decks[trait]
        if not deck and self.discards[trait]:
            deck.extend(self.discards[trait])
            self.discards[trait].clear()
            self.rng.shuffle(deck)
        return self.catalog[deck.pop()] if deck else None

    def explore_planet(self, player, tile, planet_id: str, *, trait=None,
                       auto_resolve=False, draw_count=1) -> ExplorationOutcome:
        if self.pending is not None or self.freelancer_offer is not None:
            raise ValueError('Resolve the current exploration first.')
        planet = next((candidate for candidate in tile.planets
                       if candidate.planet_id == planet_id), None)
        if planet_id in self.destroyed_planets:
            raise ValueError('Destroyed planets cannot be explored.')
        if planet is None or tile.planet_owners.get(planet_id) != player.faction:
            raise ValueError('Only a controlled planet in this system can be explored.')
        traits = tuple(part.strip().lower() for part in (planet.planet_type or '').split(','))
        traits = tuple(part for part in traits if part in TRAITS[:-1])
        if not traits or planet.faction_homeworld:
            return ExplorationOutcome(None)
        chosen_trait = (trait or traits[0]).lower()
        if chosen_trait not in traits:
            raise ValueError('Choose one of the planet’s exploration traits.')
        if draw_count not in (1, 2):
            raise ValueError('Draw one exploration card, or two with Distant Suns.')
        cards = tuple(card for _ in range(draw_count)
                      if (card := self._draw(chosen_trait)) is not None)
        if len(cards) == 2:
            offered = tuple(card.alias for card in cards)
            self.pending = PendingExploration(player.faction, tile.position, planet_id,
                                              offered[0], offered, offered)
            if auto_resolve:
                selected = self.resolve_pending(offered[0])
                if not selected.resolved:
                    return self.resolve_pending(selected.choices[0])
                return selected
            return ExplorationOutcome(None, offered, (), False, cards)
        return self._prepare(player, tile, planet_id,
                             cards[0] if cards else None, auto_resolve=auto_resolve)

    def explore_frontier(self, player, tile, *, auto_resolve=False) -> ExplorationOutcome:
        if self.pending is not None or self.freelancer_offer is not None:
            raise ValueError('Resolve the current exploration first.')
        if 'frontier' not in self.space_tokens.get(tile.position, ()):
            raise ValueError('There is no frontier token in this system.')
        if not any(unit.owner == player.faction and unit.location.region == Region.SPACE
                   and UNIT_TYPES[unit.kind]['ship'] for unit in tile.units):
            raise ValueError('Exploring a frontier token requires one of your ships.')
        self.space_tokens[tile.position].remove('frontier')
        card = self._draw('frontier')
        return self._prepare(player, tile, None, card, auto_resolve=auto_resolve)

    def _prepare(self, player, tile, planet_id, card, *, auto_resolve):
        if card is None:
            return ExplorationOutcome(None)
        options = self._choices(player, tile, planet_id, card)
        self.pending = PendingExploration(player.faction, tile.position,
                                          planet_id, card.alias, options)
        if auto_resolve or not options:
            return self.resolve_pending(options[0] if options else None)
        return ExplorationOutcome(card, options, (), False)

    def _choices(self, player, tile, planet_id, card) -> tuple[str, ...]:
        alias = card.alias
        if alias.startswith('frln'):
            return ('produce_one_unit', 'decline')
        if alias.startswith('mo'):
            return ('decline',) if self.planet_is_blocked(planet_id) else \
                ('place_infantry', 'decline')
        if alias.startswith(('vfs', 'exp', 'cm')):
            if alias.startswith('vfs') and not command_tokens_in_reinforcements(player, self.board):
                return ('decline',)
            on_planet = [unit for unit in tile.units if unit.owner == player.faction and
                         unit.location.region == Region.PLANET and
                         unit.location.planet_id == planet_id]
            options = []
            if any(unit.kind == 'mech' for unit in on_planet):
                options.append('use_mech')
            if any(unit.kind == 'infantry' for unit in on_planet):
                options.append('remove_infantry')
            return tuple(options + ['decline'])
        if alias.startswith('aw'):
            return ('gain_commodities', 'convert_commodities')
        if alias.startswith('fb'):
            options = ['gain_commodity']
            if self.action_cards is not None and \
                    (player.faction == 'yssaril' or len(player.action_cards) < 7):
                if player.trade_goods:
                    options.append('draw_card_trade_good')
                if player.commodities:
                    options.append('draw_card_commodity')
            return tuple(options)
        if alias.startswith('lf'):
            options = ['gain_commodity']
            if ('mech' in UNIT_TYPES and not self.planet_is_blocked(planet_id) and
                    sum(unit.owner == player.faction and unit.kind == 'mech'
                        for source in self.board.values() for unit in source.units) < 4):
                if player.trade_goods:
                    options.append('place_mech_trade_good')
                if player.commodities:
                    options.append('place_mech_commodity')
            return tuple(options)
        if alias.startswith('ms'):
            return ('replenish_commodities', 'convert_commodities')
        if alias == 'ion':
            return ('alpha', 'beta')
        return ()

    def resolve_pending(self, choice: str | None = None) -> ExplorationOutcome:
        pending = self.pending
        if pending is None:
            raise ValueError('No exploration is pending.')
        if pending.choices and choice not in pending.choices:
            raise ValueError('Choose a valid exploration card option.')
        player = self.players[pending.faction]
        tile = self.board[pending.position]
        if pending.offered_aliases:
            for alias in pending.offered_aliases:
                if alias != choice:
                    self.discards[self.catalog[alias].trait].append(alias)
            selected_card = self.catalog[choice]
            next_choices = self._choices(player, tile, pending.planet_id, selected_card)
            self.pending = PendingExploration(player.faction, tile.position,
                                              pending.planet_id, choice, next_choices)
            if not next_choices:
                return self.resolve_pending()
            return ExplorationOutcome(selected_card, next_choices, (), False)
        card = self.catalog[pending.alias]
        self._validate_cost(player, tile, pending.planet_id, card, choice)
        # Declining an optional exploration effect is a real resolution: the
        # card is discarded, but none of its costs or benefits are applied.
        events = ([{'type': 'declined'}] if choice == 'decline' else
                  self._resolve(player, tile, pending.planet_id, card, choice))
        self.pending = None
        if card.resolution == 'attach':
            self.attachments.setdefault(pending.planet_id, []).append(card.alias)
        elif card.resolution == 'fragment':
            self.fragments[player.faction].append(card.alias)
        elif card.alias in {'gw', 'gamma', 'mirage'}:
            self.purged.add(card.alias)
        elif card.alias == 'ion':
            self.common_cards.append(card.alias)
        elif card.alias.startswith('ed'):
            # Enigmatic Device stays faceup until its component action purges it.
            pass
        else:
            self.discards[card.trait].append(card.alias)
        return ExplorationOutcome(card, (), tuple(events), True)

    def _validate_cost(self, player, tile, planet_id, card, choice):
        if choice in {'draw_card_trade_good', 'place_mech_trade_good'} and player.trade_goods < 1:
            raise ValueError('This choice requires 1 trade good.')
        if choice in {'draw_card_commodity', 'place_mech_commodity'} and player.commodities < 1:
            raise ValueError('This choice requires 1 commodity.')
        if choice == 'remove_infantry' and not any(
            unit.owner == player.faction and unit.kind == 'infantry' and
            unit.location.region == Region.PLANET and unit.location.planet_id == planet_id
            for unit in tile.units
        ):
            raise ValueError('No infantry is available on this planet.')
        if choice == 'use_mech' and not any(
            unit.owner == player.faction and unit.kind == 'mech' and
            unit.location.region == Region.PLANET and unit.location.planet_id == planet_id
            for unit in tile.units
        ):
            raise ValueError('No mech is available on this planet.')
        if choice and choice.startswith('place_mech_') and 'mech' not in UNIT_TYPES:
            raise ValueError('Mech units are not available in this game setup.')
        if choice and choice.startswith('place_mech_') and sum(
            unit.owner == player.faction and unit.kind == 'mech'
            for source in self.board.values() for unit in source.units
        ) >= 4:
            raise ValueError('All four mechs are already on the board.')

    def _place_unit(self, player, tile, planet_id, kind):
        if kind not in UNIT_TYPES:
            return False
        if (kind == 'infantry' and not can_place_infantry(
                player, self.board, tile, UnitLocation(Region.PLANET, planet_id))):
            return False
        if self.planet_is_blocked(planet_id):
            return False
        # Use an ID namespace separate from production and starting fleets.
        while any(unit.unit_id == f'explore-{player.faction}-{self.next_unit_number}'
                  for tile_on_board in self.board.values() for unit in tile_on_board.units):
            self.next_unit_number += 1
        unit = Unit(f'explore-{player.faction}-{self.next_unit_number}', kind,
                    player.faction, player.color_code,
                    UnitLocation(Region.PLANET, planet_id))
        self.next_unit_number += 1
        tile.units.append(unit)
        return True

    def _resolve(self, player, tile, planet_id, card, choice) -> list[dict]:
        alias = card.alias
        events: list[dict] = []
        if card.resolution == 'fragment':
            return [{'type': 'fragment', 'alias': alias}]
        if card.resolution == 'attach':
            if alias == 'dmz':
                displaced = tuple(unit for unit in tile.units
                                  if unit.location.region == Region.PLANET and
                                  unit.location.planet_id == planet_id and
                                  unit.kind in {'infantry', 'mech'})
                if not can_transfer_infantry(
                        player, self.board,
                        [(tile, unit, tile, UnitLocation(Region.SPACE_AREA))
                         for unit in displaced]):
                    raise ValueError('Infantry tokens need a plastic infantry anchor in the system space area.')
                for unit in tuple(tile.units):
                    if unit.location.region != Region.PLANET or unit.location.planet_id != planet_id:
                        continue
                    if unit.kind in {'pds', 'spacedock'}:
                        tile.units.remove(unit)
                    elif unit.kind in {'infantry', 'mech'}:
                        unit.location = UnitLocation(Region.SPACE_AREA)
                events.append({'type': 'demilitarized_zone', 'planet_id': planet_id})
            return events + [{'type': 'attachment', 'planet_id': planet_id, 'alias': alias}]
        if alias in {'gw', 'gamma'}:
            self.space_tokens.setdefault(tile.position, []).append('gamma')
            return [{'type': 'gamma_wormhole', 'position': tile.position}]
        if alias == 'ion':
            self.space_tokens.setdefault(tile.position, []).append(f'ion_{choice}')
            return [{'type': 'ion_storm', 'position': tile.position, 'side': choice}]
        if alias == 'mirage':
            data = json.loads((RESOURCES / 'planets/mirage.json').read_text(encoding='utf-8'))
            data['positionInTile'] = {'x': 172.5, 'y': 149.5}
            mirage = Planet.from_data(data)
            if not any(planet.planet_id == 'mirage' for planet in tile.planets):
                tile.definition = replace(tile.definition, planets=tile.planets + (mirage,))
                tile.planet_owners['mirage'] = player.faction
                player.planets.append(PlanetCard(mirage))
            return [{'type': 'mirage', 'position': tile.position}]
        if alias.startswith('frln') and choice == 'produce_one_unit':
            self.freelancer_offer = (player.faction, tile.position, planet_id)
            events.append({'type': 'production', 'position': tile.position,
                           'unit_limit': 1, 'influence_as_resources': True})
        elif alias.startswith('mo') and choice == 'place_infantry':
            if self._place_unit(player, tile, planet_id, 'infantry'):
                events.append({'type': 'unit_placed', 'kind': 'infantry', 'planet_id': planet_id})
        elif alias.startswith(('vfs', 'exp', 'cm')) and choice != 'decline':
            if choice == 'remove_infantry':
                infantry = next(unit for unit in tile.units if unit.owner == player.faction and
                                unit.kind == 'infantry' and unit.location.region == Region.PLANET
                                and unit.location.planet_id == planet_id)
                tile.units.remove(infantry)
            if alias.startswith('vfs'):
                if command_tokens_in_reinforcements(player, self.board):
                    player.pending_commands += 1
                    events.append({'type': 'allocate_command_token', 'count': 1})
            elif alias.startswith('exp'):
                planet_card = next((entry for entry in player.planets
                                    if entry.planet.planet_id == planet_id), None)
                if planet_card is not None:
                    planet_card.exhausted = False
                    events.append({'type': 'planet_readied', 'planet_id': planet_id})
            else:
                player.trade_goods += 1
                events.append({'type': 'trade_goods', 'count': 1})
        elif alias.startswith('aw'):
            if choice == 'gain_commodities':
                player.commodities = min(self.relics.commodity_limit(player),
                                         player.commodities + 2)
            else:
                converted = min(2, player.commodities)
                player.commodities -= converted
                player.trade_goods += converted
            events.append({'type': 'commodities_changed'})
        elif alias.startswith('fb'):
            events.extend(self._industrial_choice(player, choice, 'action_card'))
        elif alias.startswith('lf'):
            events.extend(self._industrial_choice(player, choice, 'mech', tile, planet_id))
        elif alias.startswith('dv'):
            drawn = self.secret_objectives.draw(player)
            events.append({'type': 'draw_secret_objective', 'count': len(drawn)})
            if player.faction in self.secret_objectives.pending_returns:
                events.append({'type': 'secret_objective_return_required',
                               'count': self.secret_objectives.pending_returns[player.faction]})
        elif alias.startswith('ed'):
            self.kept_cards[player.faction].append(alias)
            events.append({'type': 'enigmatic_device', 'alias': alias})
        elif alias.startswith('lc'):
            if self.action_cards is not None:
                self.action_cards.draw(player, 2)
            events.append({'type': 'draw_action_cards', 'count': 2})
        elif alias.startswith('ms'):
            if choice == 'replenish_commodities':
                player.commodities = self.relics.commodity_limit(player)
            else:
                player.trade_goods += player.commodities
                player.commodities = 0
            events.append({'type': 'commodities_changed'})
        elif alias in FRONTIER_COMMAND_AND_GOODS:
            requested_commands, goods = FRONTIER_COMMAND_AND_GOODS[alias]
            gained_commands = min(requested_commands,
                                  command_tokens_in_reinforcements(player, self.board))
            if gained_commands:
                player.pending_commands += gained_commands
                events.append({'type': 'allocate_command_token',
                               'count': gained_commands})
            if goods:
                player.trade_goods += goods
                events.append({'type': 'trade_goods', 'count': goods})
        elif alias == 'dw':
            relic = self.relics.draw(player.faction)
            events.append({'type': 'relic_drawn', 'alias': relic.alias,
                           'details': relic.events} if relic.alias else
                          {'type': 'relic_deck_empty'})
        else:
            raise ValueError(f'No exploration effect is implemented for {alias}.')
        return events

    def _industrial_choice(self, player, choice, benefit, tile=None, planet_id=None):
        if choice == 'gain_commodity':
            player.commodities = min(self.relics.commodity_limit(player),
                                     player.commodities + 1)
            return [{'type': 'commodities_changed'}]
        if choice.endswith('_trade_good'):
            player.trade_goods -= 1
        elif choice.endswith('_commodity'):
            player.commodities -= 1
        if benefit == 'action_card':
            if self.action_cards is not None:
                self.action_cards.draw(player, 1)
            return [{'type': 'draw_action_cards', 'count': 1}]
        if self._place_unit(player, tile, planet_id, 'mech'):
            return [{'type': 'unit_placed', 'kind': 'mech', 'planet_id': planet_id}]
        return [{'type': 'unit_unavailable', 'kind': 'mech'}]

    def freelancers_cost(self, player, movement, kind: str) -> int:
        """Return the payable cost of the single unit in a Freelancers offer."""
        if kind not in movement.PRODUCIBLE_KINDS or not production_allowed(player, kind):
            raise ValueError('This unit cannot be produced with Freelancers.')
        raw_cost = ceil(movement.unit_cost(kind, player))
        if kind == 'flagship' and player.faction == 'nomad' and \
                leader_available(player, 'nomadcommander'):
            raw_cost = 0
        return raw_cost

    def produce_freelancers(self, player, movement, kind: str | None, *,
                            planet_id: str | None = _SOURCE_PLANET,
                            resource_planets=(), influence_planets=(), trade_goods=0):
        """Resolve a Freelancers offer with one unit and an explicit mixed payment.

        A ``None`` kind declines the offer. The caller must run the normal
        capacity and fleet checks after placement, as with any production.
        """
        offer = self.freelancer_offer
        if offer is None or offer[0] != player.faction or movement.board is not self.board:
            raise ValueError('No Freelancers production is available to this player.')
        if kind is None:
            self.freelancer_offer = None
            return {'type': 'production_declined'}
        if kind not in movement.PRODUCIBLE_KINDS or not production_allowed(player, kind):
            raise ValueError('This unit cannot be produced with Freelancers.')
        tile = self.board[offer[1]]
        occupied = sum(unit.owner == player.faction and unit.kind == kind
                       for system in self.board.values() for unit in system.units)
        plastic_limits = {'mech': 4, 'carrier': 4, 'destroyer': 8, 'cruiser': 8,
                          'dreadnought': 5, 'flagship': 1, 'warsun': 2}
        if occupied >= plastic_limits.get(kind, float('inf')):
            raise ValueError('No unit of this type remains in reinforcements.')
        if (UNIT_TYPES[kind]['ship'] and any(
                unit.owner != player.faction and unit.location.region == Region.SPACE and
                UNIT_TYPES[unit.kind]['ship'] for unit in tile.units)):
            raise ValueError('Cannot produce a ship in a blockaded system.')
        if UNIT_TYPES[kind]['ship']:
            location = UnitLocation(Region.SPACE)
        else:
            destination = offer[2] if planet_id is _SOURCE_PLANET else planet_id
            if destination is not None and (tile.planet_owners.get(destination) == player.faction
                                            and not self.planet_is_blocked(destination)
                                            and destination not in self.destroyed_planets):
                location = UnitLocation(Region.PLANET, destination)
            elif player.faction == 'saar' and destination is None:
                dock = next((unit for unit in tile.units if unit.owner == player.faction and
                             unit.kind == 'spacedock' and unit.location.region == Region.SPACE), None)
                if dock is None:
                    raise ValueError('Ground forces need a controlled planet or Floating Factory.')
                location = UnitLocation(Region.TRANSPORT, carrier_id=dock.unit_id)
            else:
                raise ValueError('Choose a controlled, available planet in this system.')
        if kind == 'infantry' and not can_place_infantry(player, self.board, tile, location):
            raise ValueError('No plastic infantry remains to anchor this planet or system.')
        resources = tuple(resource_planets)
        influence = tuple(influence_planets)
        chosen = resources + influence
        if (len(chosen) != len(set(chosen)) or not isinstance(trade_goods, int) or
                trade_goods < 0 or trade_goods > player.trade_goods):
            raise ValueError('Choose distinct ready planets and available trade goods.')
        cards = {card.planet.planet_id: card for card in player.planets if not card.exhausted}
        if any(planet not in cards for planet in chosen):
            raise ValueError('Only ready controlled planets can pay for Freelancers.')
        cost = self.freelancers_cost(player, movement, kind)
        payment = (sum(movement.planet_resources(cards[planet].planet)
                       for planet in resources) +
                   sum(movement.planet_influence(cards[planet].planet)
                       for planet in influence) + trade_goods)
        if payment < cost:
            raise ValueError(f'Freelancers requires {cost} resources or influence.')
        while any(unit.unit_id == f'explore-{player.faction}-{self.next_unit_number}'
                  for system in self.board.values() for unit in system.units):
            self.next_unit_number += 1
        unit = Unit(f'explore-{player.faction}-{self.next_unit_number}', kind,
                    player.faction, player.color_code, location,
                    profile_id=(upgrade_profile(player, kind) or {}).get('id'))
        self.next_unit_number += 1
        for planet in chosen:
            cards[planet].exhausted = True
        player.trade_goods -= trade_goods
        tile.units.append(unit)
        self.freelancer_offer = None
        return {'type': 'unit_produced', 'kind': kind, 'unit_id': unit.unit_id,
                'position': tile.position, 'cost': cost,
                'check_capacity_and_fleet': True}

    def purge_fragments_for_relic(self, faction: str, card_ids: tuple[str, ...],
                                  *, required=3) -> RelicOutcome:
        if len(card_ids) != required or len(set(card_ids)) != required:
            raise ValueError(f'Purge exactly {required} distinct relic fragments.')
        available = self.fragments[faction]
        if any(alias not in available or self.catalog[alias].resolution != 'fragment'
               for alias in card_ids):
            raise ValueError('Selected relic fragments are not owned by this player.')
        types = {self.catalog[alias].trait for alias in card_ids}
        types.discard('frontier')  # Unknown fragments are wild.
        if len(types) > 1:
            raise ValueError('Relic fragments must share one trait (unknown is wild).')
        for alias in card_ids:
            available.remove(alias)
            self.purged.add(alias)
        return self.relics.draw(faction)

    def transfer_fragment(self, source_faction: str, target_faction: str,
                          alias: str) -> None:
        """Transfer one fragment during a transaction (timing is caller-owned)."""
        if (source_faction == target_faction or target_faction not in self.fragments or
                alias not in self.fragments[source_faction]):
            raise ValueError('Choose a fragment owned by another player.')
        self.fragments[source_faction].remove(alias)
        self.fragments[target_faction].append(alias)

    def use_nano_forge(self, player, planet_id: str) -> dict:
        if 'nanoforge' not in self.relics.owned[player.faction]:
            raise ValueError('Nano-Forge is not owned by this player.')
        match = next(((tile, planet) for tile in self.board.values() for planet in tile.planets
                      if planet.planet_id == planet_id), None)
        if match is None:
            raise ValueError('Planet not found.')
        tile, planet = match
        if (tile.planet_owners.get(planet_id) != player.faction or
                planet.faction_homeworld or self.planet_is_legendary(planet) or
                planet_id in self.destroyed_planets):
            raise ValueError('Choose a controlled non-home, non-legendary planet.')
        self.relics.owned[player.faction].remove('nanoforge')
        self.attached_relics[planet_id] = 'nanoforge'
        self.legendary_planets.add(planet_id)
        self.attachments.setdefault(planet_id, []).append('nanoforge')
        return {'type': 'nano_forge', 'planet_id': planet_id}

    def use_titan_prototype(self, owner, recipient, *, planet_id=None, kind=None,
                            planet_ids: tuple[str, ...] = (), trade_goods=0) -> dict:
        """Resolve JR-XS455-O's action after the chosen player accepts/declines."""
        if not self.relics.is_ready(owner.faction, 'titanprototype'):
            raise ValueError('JR-XS455-O is not ready.')
        if kind is None:
            self.relics.exhaust(owner.faction, 'titanprototype')
            recipient.trade_goods += 1
            return {'type': 'trade_goods', 'faction': recipient.faction, 'count': 1}
        if kind not in {'pds', 'spacedock'} or planet_id is None:
            raise ValueError('Choose a PDS or space dock on a controlled planet.')
        target = next(((tile, planet) for tile in self.board.values() for planet in tile.planets
                       if planet.planet_id == planet_id), None)
        if target is None or target[0].planet_owners.get(planet_id) != recipient.faction or \
                self.planet_is_blocked(planet_id) or planet_id in self.destroyed_planets:
            raise ValueError('Choose a controlled planet that can receive a structure.')
        on_board = [unit for tile in self.board.values() for unit in tile.units
                    if unit.owner == recipient.faction and unit.kind == kind]
        if len(on_board) >= {'pds': 6, 'spacedock': 3}[kind]:
            raise ValueError('No structures of that kind remain in reinforcements.')
        on_planet = [unit for unit in target[0].units
                     if unit.location.region == Region.PLANET and
                     unit.location.planet_id == planet_id and unit.owner == recipient.faction]
        if kind == 'spacedock' and any(unit.kind == 'spacedock' for unit in on_planet):
            raise ValueError('This planet already has a space dock.')
        if kind == 'pds' and sum(unit.kind == 'pds' for unit in on_planet) >= 2:
            raise ValueError('A planet can have at most two PDS units.')
        if len(set(planet_ids)) != len(planet_ids):
            raise ValueError('Choose each payment planet only once.')
        cards = {card.planet.planet_id: card for card in recipient.planets}
        if trade_goods < 0 or trade_goods > recipient.trade_goods or any(
            card_id not in cards or cards[card_id].exhausted for card_id in planet_ids
        ):
            raise ValueError('Choose ready controlled planets and available trade goods.')
        value = trade_goods + sum(self.planet_values(cards[card_id].planet)[0]
                                  for card_id in planet_ids)
        if value < 3:
            raise ValueError('JR-XS455-O costs 3 resources to place a structure.')
        for card_id in planet_ids:
            cards[card_id].exhausted = True
        recipient.trade_goods -= trade_goods
        self._place_unit(recipient, target[0], planet_id, kind)
        self.relics.exhaust(owner.faction, 'titanprototype')
        return {'type': 'unit_placed', 'kind': kind, 'planet_id': planet_id,
                'spent_resources': value}

    def use_stellar_converter(self, player, planet_id: str) -> dict:
        if 'stellarconverter' not in self.relics.owned[player.faction]:
            raise ValueError('Stellar Converter is not owned by this player.')
        match = next(((tile, planet) for tile in self.board.values() for planet in tile.planets
                      if planet.planet_id == planet_id), None)
        if match is None:
            raise ValueError('Planet not found.')
        tile, planet = match
        if (planet.faction_homeworld or planet_id == 'mecatolrex' or
                self.planet_is_legendary(planet) or
                planet_id in self.destroyed_planets or
                tile.planet_owners.get(planet_id) == player.faction):
            raise ValueError('Stellar Converter needs another non-home, non-legendary planet.')
        bombarding = any(
            unit.owner == player.faction and unit.location.region == Region.SPACE and
            unit_profile(unit).get('bombardHitsOn')
            for adjacent in self.board.neighbors(tile.position) for unit in adjacent.units
        )
        if not bombarding:
            raise ValueError('No adjacent friendly unit has Bombardment.')
        tile.units[:] = [unit for unit in tile.units if not
                         (unit.location.region == Region.PLANET and
                          unit.location.planet_id == planet_id)]
        tile.planet_owners.pop(planet_id, None)
        for target in self.players.values():
            target.planets[:] = [card for card in target.planets
                                 if card.planet.planet_id != planet_id]
        self.purged.update(self.attachments.pop(planet_id, ()))
        self.destroyed_planets.add(planet_id)
        self.relics.purge(player.faction, 'stellarconverter')
        return {'type': 'planet_destroyed', 'planet_id': planet_id,
                'position': tile.position}

    def use_crown_of_emphidia(self, player, tile, planet_id: str,
                              *, auto_resolve=False) -> ExplorationOutcome:
        if not self.relics.is_ready(player.faction, 'emphidia'):
            raise ValueError('The Crown of Emphidia is not ready.')
        if self.pending is not None:
            raise ValueError('Resolve the current exploration first.')
        # Validation occurs before exhaustion.  The card is still exhausted
        # when the exploration draw has no available card.
        if (tile.planet_owners.get(planet_id) != player.faction or
                planet_id in self.destroyed_planets or
                not any(planet.planet_id == planet_id and not planet.faction_homeworld
                        and (planet.planet_type or '').lower() in TRAITS[:-1]
                        for planet in tile.planets)):
            raise ValueError('Choose a controlled explorable planet.')
        self.relics.exhaust(player.faction, 'emphidia')
        return self.explore_planet(player, tile, planet_id, auto_resolve=auto_resolve)

    def crown_tomb_controlled(self, faction: str) -> bool:
        return any('toe' in self.attachments.get(planet.planet_id, ()) and
                   tile.planet_owners.get(planet.planet_id) == faction
                   for tile in self.board.values() for planet in tile.planets)

    def use_enigmatic_device(self, player, alias: str, technology_alias: str,
                             planet_ids: tuple[str, ...], trade_goods: int = 0) -> dict:
        """Spend six resources, then purge a faceup Enigmatic Device.

        This is a component action; the turn controller must check the action
        window and count it as the player's action.
        """
        if alias not in self.kept_cards[player.faction] or not alias.startswith('ed'):
            raise ValueError('This Enigmatic Device is not in your play area.')
        if len(set(planet_ids)) != len(planet_ids):
            raise ValueError('Choose each payment planet only once.')
        cards = {card.planet.planet_id: card for card in player.planets}
        if trade_goods < 0 or trade_goods > player.trade_goods or any(
            planet_id not in cards or cards[planet_id].exhausted for planet_id in planet_ids
        ):
            raise ValueError('Choose ready controlled planets and available trade goods.')
        value = trade_goods + sum(self.planet_values(cards[planet_id].planet)[0]
                                  for planet_id in planet_ids)
        if value < 6:
            raise ValueError('Enigmatic Device costs 6 resources.')
        from technology import (available_technologies, missing_prerequisites,
                                research_technology)
        tech = next((tech for tech in available_technologies(player)
                     if tech['alias'] == technology_alias), None)
        if tech is None or technology_alias in player.technologies or \
                missing_prerequisites(player, tech) > 0:
            raise ValueError('Choose an available technology whose prerequisites are met.')
        research_technology(player, technology_alias, self.board)
        for planet_id in planet_ids:
            cards[planet_id].exhausted = True
        player.trade_goods -= trade_goods
        self.kept_cards[player.faction].remove(alias)
        self.purged.add(alias)
        return {'type': 'technology_researched', 'alias': technology_alias,
                'spent_resources': value}

    def flip_ion_storm(self, position: tuple[int, int]) -> str:
        tokens = self.space_tokens.get(position, ())
        for index, token in enumerate(tokens):
            if token in {'ion_alpha', 'ion_beta'}:
                next_side = 'ion_beta' if token == 'ion_alpha' else 'ion_alpha'
                tokens[index] = next_side
                return next_side
        raise ValueError('No ion storm token is in this system.')
