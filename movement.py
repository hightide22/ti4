from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import combinations
from math import ceil
import random
from uuid import uuid4

from player import PlanetCard
from leaders import leader_available
from units import Region, Unit, UnitLocation, UNIT_TYPES, mobile_ship, unit_profile, unit_profiles
from technology import (production_allowed, upgrade_profile, copy_technology,
                        technology_catalog, is_unit_upgrade)


class MovementError(ValueError):
    pass


def capital_ship(unit):
    return UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter'


def cargo_cost(unit):
    value = unit_profile(unit).get('capacityUsed')
    return int(value) if value is not None else 1


def format_die_result(value, modifier, hit):
    result = f'{value}{modifier:+d}={value + modifier}' if modifier else str(value)
    return f'{result} {"HIT" if hit else "miss"}'


@dataclass
class Source:
    tile: object
    ships: list
    passengers: list
    routes: dict


@dataclass
class Snapshot:
    player: object
    pools: dict
    tiles: list
    locations: list
    planet_cards: list
    card_states: list
    currencies: list
    technology_states: list
    action_hands: list
    action_state: object | None = None
    extra_player_states: list = field(default_factory=list)
    exploration_state: object | None = None

    @classmethod
    def capture(cls, board, player, players, action_controller=None, exploration=None):
        all_players = players or [player]
        cards = [(other, list(other.planets)) for other in all_players]
        return cls(player, dict(player.command_pools),
                   [(tile, list(tile.units), set(tile.command_tokens), dict(tile.planet_owners),
                     tile.custodians_present, set(getattr(tile, 'sleeper_tokens', ())))
                    for tile in board.values()],
                   [(unit, unit.location, unit.damaged, unit.profile_id)
                    for tile in board.values() for unit in tile.units],
                   cards,
                   [(card, card.exhausted) for _, player_cards in cards for card in player_cards],
                   [(other, other.trade_goods, other.commodities) for other in all_players],
                   [(other, set(other.exhausted_technologies), dict(other.infantry_on_cards),
                     frozenset(other.technologies))
                    for other in all_players],
                   [(other, list(other.action_cards)) for other in all_players],
                   (action_controller, action_controller.snapshot()) if action_controller else None,
                   [(other, {name: deepcopy(getattr(other, name)) for name in (
                       'leaders', 'scored_objectives', 'scored_secret_objectives',
                       'foreign_fleet_tokens', 'foreign_command_tokens_out',
                       'captured_units', 'hero_attachments', 'hero_round_flags',
                       'pending_commands', 'exhausted_legendary_abilities')
                      if hasattr(other, name)})
                    for other in all_players],
                   (exploration, exploration.snapshot()) if exploration else None)

    def restore(self):
        self.player.command_pools.clear()
        self.player.command_pools.update(self.pools)
        for tile, units, tokens, owners, custodians_present, sleepers in self.tiles:
            tile.units[:] = units
            tile.command_tokens.clear()
            tile.command_tokens.update(tokens)
            tile.planet_owners.clear()
            tile.planet_owners.update(owners)
            tile.custodians_present = custodians_present
            if hasattr(tile, 'sleeper_tokens'):
                tile.sleeper_tokens.clear()
                tile.sleeper_tokens.update(sleepers)
        for unit, location, damaged, profile_id in self.locations:
            unit.location = location
            unit.damaged = damaged
            unit.profile_id = profile_id
        for player, cards in self.planet_cards:
            player.planets[:] = cards
        for card, exhausted in self.card_states:
            card.exhausted = exhausted
        for player, trade_goods, commodities in self.currencies:
            player.trade_goods = trade_goods
            player.commodities = commodities
        for player, exhausted, infantry, technologies in self.technology_states:
            player.exhausted_technologies.clear()
            player.exhausted_technologies.update(exhausted)
            player.infantry_on_cards.clear()
            player.infantry_on_cards.update(infantry)
            player.technologies = technologies
        for player, hand in self.action_hands:
            player.action_cards[:] = hand
        for player, attributes in self.extra_player_states:
            for name, value in attributes.items():
                setattr(player, name, deepcopy(value))
        if self.action_state:
            controller, state = self.action_state
            controller.restore(state)
        if self.exploration_state:
            controller, state = self.exploration_state
            controller.restore(state)


@dataclass
class SessionCheckpoint:
    snapshot: Snapshot
    stage: str
    landings: dict[str, str | None] = field(default_factory=dict)
    bombard_targets: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def capture(cls, controller, session, stage):
        return cls(Snapshot.capture(controller.board, session.player, controller.players,
                                    controller.action_cards, controller.exploration),
                   stage, dict(session.landings), dict(session.bombard_targets))

    def restore(self, session):
        self.snapshot.restore()
        session.stage = self.stage
        session.landings = dict(self.landings)
        session.outcome = ''
        session.capacity_required = 0
        session.capacity_selected.clear()
        session.capacity_position = None
        session.capacity_faction = None
        session.capacity_queue.clear()
        session.capacity_next_stage = None
        session.capacity_affected.clear()
        session.fleet_queue.clear()
        session.cargo_selected.clear()
        session.overflow_required = 0
        session.overflow_selected.clear()
        session.overflow_position = None
        session.overflow_faction = None
        session.overflow_next_stage = None
        if self.stage == 'invasion':
            session.landed_planets.clear()
            session.captured_planets.clear()
            session.ground_planets.clear()
            session.ground_planet_index = 0
            session.defense_checked = False
            session.defense_log.clear()
        elif self.stage == 'bombardment':
            session.bombard_targets = dict(self.bombard_targets)
            session.bombard_rolls.clear()
            session.bombard_log.clear()
            session.bombardment_resolved = False
        elif self.stage == 'production':
            session.production_choices.clear()
            session.production_planets.clear()
            session.trade_goods_to_spend = 0
            session.self_assembly_planet = None
            session.aida_production_discount = False
            session.amalgamation_choices.clear()
        elif self.stage == 'movement':
            session.gravity_rift_log.clear()
            session.combat_rolls.clear()
            session.combat_hits.clear()
            session.combat_assignments.clear()
            session.combat_needs_resolution = False
            session.overflow_required = 0
            session.overflow_selected.clear()
            session.overflow_next_stage = None


@dataclass
class UndoEntry:
    snapshot: Snapshot
    resumed_session: object | None = None
    checkpoint: SessionCheckpoint | None = None
    counts_as_action: bool = True


@dataclass
class Session:
    player: object
    target: object
    sources: dict
    snapshot: Snapshot
    strategic_production: bool = False
    selected: set[str] = field(default_factory=set)
    cargo_selected: set[str] = field(default_factory=set)
    pickup_sources: dict = field(default_factory=dict)
    stage: str = 'movement'
    landings: dict[str, str | None] = field(default_factory=dict)
    cannon_log: list[str] = field(default_factory=list)
    cannon_checked: bool = False
    space_cannon_events: list[dict] = field(default_factory=list)
    space_cannon_target_faction: str | None = None
    space_cannon_next_stage: str | None = None
    space_cannon_nes_cancel: int = 0
    space_cannon_direct_hit: tuple[str, str] | None = None
    experimental_window_closed: bool = False
    disabled_pds: set[str] = field(default_factory=set)
    bunker_factions: set[str] = field(default_factory=set)
    invasion_window_closed: bool = False
    afb_rolls: dict[str, list[dict]] = field(default_factory=dict)
    afb_log: list[str] = field(default_factory=list)
    assault_log: list[str] = field(default_factory=list)
    assault_queue: list[str] = field(default_factory=list)
    afb_resolved: bool = False
    retreat_announced: str | None = None
    retreat_blocked_round: int | None = None
    retreat_declined_round: int | None = None
    retreat_log: str = ''
    combat_winner: str | None = None
    pending_direct_hits: dict[str, list[str]] = field(default_factory=dict)
    pending_courageous: dict[str, list[dict]] = field(default_factory=dict)
    hit_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    bombard_targets: dict[str, str | None] = field(default_factory=dict)
    bombard_rolls: list[dict] = field(default_factory=list)
    bombard_log: list[str] = field(default_factory=list)
    bombardment_resolved: bool = False
    bombardment_cancelled: bool = False
    defense_checked: bool = False
    defense_log: list[str] = field(default_factory=list)
    outcome: str = ''
    overflow_required: int = 0
    overflow_selected: set[str] = field(default_factory=set)
    overflow_next_stage: str | None = None
    overflow_position: tuple | None = None
    overflow_faction: str | None = None
    capacity_required: int = 0
    capacity_selected: set[str] = field(default_factory=set)
    capacity_position: tuple | None = None
    capacity_faction: str | None = None
    capacity_queue: list[tuple[tuple, str]] = field(default_factory=list)
    capacity_next_stage: str | None = None
    capacity_affected: list[tuple[tuple, str]] = field(default_factory=list)
    fleet_queue: list[tuple[tuple, str]] = field(default_factory=list)
    freelancer_resume_stage: str | None = None
    freelancer_temporary: bool = False
    production_limit: int = 0
    production_sites: list[tuple[str, int]] = field(default_factory=list)
    production_choices: dict[str, int] = field(default_factory=dict)
    production_planets: set[str] = field(default_factory=set)
    self_assembly_planet: str | None = None
    aida_production_discount: bool = False
    amalgamation_choices: dict[str, int] = field(default_factory=dict)
    scanlink_used: bool = False
    integrated_queue: list[str] = field(default_factory=list)
    integrated_current: str | None = None
    trade_goods_to_spend: int = 0
    combat_round: int = 0
    combat_factions: tuple[str, ...] = ()
    combat_rolls: dict[str, list[dict]] = field(default_factory=dict)
    combat_bonus_dice: dict[str, int] = field(default_factory=dict)
    combat_bonus_sources: dict[str, str] = field(default_factory=dict)
    combat_hits: dict[str, int] = field(default_factory=dict)
    combat_assignments: dict[str, list[str]] = field(default_factory=dict)
    munitions_ready: set[str] = field(default_factory=set)
    munitions_available: set[str] = field(default_factory=set)
    munitions_spent_round: set[tuple[str, int]] = field(default_factory=set)
    reroll_selected: dict[str, set[int]] = field(default_factory=dict)
    gravity_bonus_ids: set[str] = field(default_factory=set)
    gravity_rift_log: list[str] = field(default_factory=list)
    movement_checkpoint: SessionCheckpoint | None = None
    fighter_carriers: dict[str, str] = field(default_factory=dict)
    spatial_conduit_active: bool = False
    combat_needs_resolution: bool = False
    combat_sustained_this_round: set[str] = field(default_factory=set)
    sardakk_retaliation_resolving: bool = False
    magen_suppressed: str | None = None
    combat_type: str = 'space'
    space_combat_resolved: bool = False
    combat_planet_id: str | None = None
    ground_planets: list[str] = field(default_factory=list)
    ground_planet_index: int = 0
    landed_planets: list[str] = field(default_factory=list)
    captured_planets: list[str] = field(default_factory=list)
    rolled_any_dice: bool = False
    bombardment_checkpoint: SessionCheckpoint | None = None
    invasion_checkpoint: SessionCheckpoint | None = None
    production_checkpoint: SessionCheckpoint | None = None
    movement_bonus: int = 0
    combat_modifiers: dict[str, int] = field(default_factory=dict)
    fighter_combat_modifiers: dict[str, int] = field(default_factory=dict)
    skilled_retreat: bool = False
    played_action_windows: set[tuple] = field(default_factory=set)
    fire_team_pending: str | None = None
    fire_team_selected: set[int] = field(default_factory=set)
    moved_ship_ids: set[str] = field(default_factory=set)
    mentak_ambush_resolved: bool = False
    mentak_ambush_rolls: list[dict] = field(default_factory=list)
    nekro_tech_gained: set[str] = field(default_factory=set)
    yin_devotion_used: set[str] = field(default_factory=set)
    custodian_payment_log: list[str] = field(default_factory=list)
    custodian_payment_failed: bool = False
    foresight_faction: str | None = None
    foresight_destinations: tuple[tuple[int, int], ...] = ()
    foresight_affected: list[tuple[tuple[int, int], str]] = field(default_factory=list)
    foresight_next_stage: str | None = None

    @property
    def choices(self):
        return {u.unit_id: u for source in (*self.sources.values(), *self.pickup_sources.values())
                for u in source.ships + source.passengers}

    def ships(self, source):
        return [u for u in source.ships if u.unit_id in self.selected and
                u.unit_id not in self.cargo_selected]

    def passengers(self, source):
        independent_ships = {ship.unit_id for candidate in self.sources.values()
                             for ship in candidate.ships}
        return [u for u in source.passengers if u.unit_id in self.cargo_selected or
                (u.unit_id in self.selected and u.unit_id not in independent_ships)]

    def carried(self, source, ships=None):
        ids = {u.unit_id for u in (self.ships(source) if ships is None else ships)}
        return [u for u in source.tile.units if u.location.region == Region.TRANSPORT and u.location.carrier_id in ids]

    def cargo_values(self, source):
        cargo = self.passengers(source) + self.carried(source)
        return sum(cargo_cost(unit) for unit in cargo), sum(u.capacity for u in self.ships(source))

    def all_passengers(self):
        units = {}
        independent_ships = {unit.unit_id for source in self.sources.values() for unit in source.ships}
        for source in (*self.sources.values(), *self.pickup_sources.values()):
            for unit in source.passengers:
                if ((unit.unit_id in self.selected or unit.unit_id in self.cargo_selected) and
                        (unit.unit_id not in independent_ships or unit.unit_id in self.cargo_selected)):
                    units[unit.unit_id] = unit
        return list(units.values())

    def passenger_origin(self, unit_id):
        return next((source.tile for source in (*self.sources.values(), *self.pickup_sources.values())
                     if any(unit.unit_id == unit_id for unit in source.passengers)), None)

    def _validate_cargo(self, ship_ids, passenger_ids):
        ships = [unit for source in self.sources.values() for unit in source.ships
                 if unit.unit_id in ship_ids]
        remaining = {ship.unit_id: ship.capacity for ship in ships}
        for source in self.sources.values():
            for cargo in source.tile.units:
                if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id in remaining:
                    remaining[cargo.location.carrier_id] -= cargo_cost(cargo)
        pending = []
        for unit_id in passenger_ids:
            unit = self.choices.get(unit_id)
            origin = self.passenger_origin(unit_id)
            candidates = [ship.unit_id for source in self.sources.values() for ship in ships
                          if ship in source.ships and ship.capacity > 0 and
                          origin is not None and origin.position in source.routes.get(ship.unit_id, ())]
            if unit is None or origin is None or not candidates:
                raise MovementError('Select a ship whose route passes through this system before loading cargo')
            pending.append((len(candidates), unit_id, candidates))
        for _, unit_id, candidates in sorted(pending):
            carrier_id = next((ship_id for ship_id in candidates
                               if remaining[ship_id] >= cargo_cost(self.choices[unit_id])), None)
            if carrier_id is None:
                raise MovementError('Not enough capacity across the selected ships for this cargo')
            remaining[carrier_id] -= cargo_cost(self.choices[unit_id])

    def toggle(self, unit_id):
        unit = self.choices.get(unit_id)
        if unit is None:
            raise MovementError('This unit cannot move to the activated system')
        ship_source = next((source for source in self.sources.values() if unit in source.ships), None)
        source = ship_source or next((s for s in (*self.sources.values(), *self.pickup_sources.values())
                                     if unit in s.passengers), None)
        if source is None:
            raise MovementError('This unit cannot move to the activated system')
        if unit_id in self.cargo_selected:
            trial = self.selected | {unit_id}
            if sum(candidate in self.gravity_bonus_ids for candidate in trial) > 1:
                raise MovementError('Gravity Drive can boost only one ship in this action.')
            self.cargo_selected.remove(unit_id)
            self.selected = trial
            return
        trial = self.selected ^ {unit_id}
        if unit in source.passengers and unit not in source.ships and unit_id not in self.selected:
            selected_ships = [ship for movement_source in self.sources.values() for ship in movement_source.ships
                              if ship.unit_id in trial and ship.unit_id not in self.cargo_selected]
            self._validate_cargo({ship.unit_id for ship in selected_ships},
                                 {candidate.unit_id for candidate in self.all_passengers()} | {unit_id})
        elif ship_source and unit_id in self.selected:
            remaining_ships = [ship for movement_source in self.sources.values()
                               for ship in movement_source.ships
                               if ship.unit_id in trial and ship.unit_id not in self.cargo_selected]
            passengers = {candidate.unit_id for candidate in self.all_passengers()}
            if passengers:
                self._validate_cargo({ship.unit_id for ship in remaining_ships}, passengers)
        if sum(unit_id in self.gravity_bonus_ids for unit_id in trial) > 1:
            raise MovementError('Gravity Drive can boost only one ship in this action.')
        self.selected = trial

    def toggle_cargo(self, unit_id):
        unit = self.choices.get(unit_id)
        if unit is None or not any(unit in source.passengers
                                   for source in (*self.sources.values(), *self.pickup_sources.values())):
            raise MovementError('This unit cannot be loaded during this movement')
        if unit_id in self.cargo_selected:
            self.cargo_selected.remove(unit_id)
            return
        # Fighter II can either move under its own power or be carried. Selecting
        # the cargo row switches it from the former mode to the latter.
        trial_selected = set(self.selected)
        trial_selected.discard(unit_id)
        trial_cargo = set(self.cargo_selected)
        trial_cargo.add(unit_id)
        passengers = {candidate.unit_id for candidate in self.all_passengers()
                      if candidate.unit_id in trial_selected or candidate.unit_id in trial_cargo}
        passengers.add(unit_id)
        ships = {ship.unit_id for source in self.sources.values() for ship in source.ships
                 if ship.unit_id in trial_selected and ship.unit_id not in trial_cargo}
        self._validate_cargo(ships, passengers)
        self.selected = trial_selected
        self.cargo_selected = trial_cargo

class MovementController:
    GROUND_FORCES = frozenset({'infantry', 'mech'})
    PRODUCIBLE_KINDS = {'infantry', 'mech', 'fighter', 'destroyer', 'cruiser', 'carrier',
                        'dreadnought', 'flagship', 'warsun'}

    def __init__(self, board, players=()):
        self.board = board
        self.players = list(players)
        self.session = None
        self.history: list[UndoEntry] = []
        self.action_cards = None
        self.exploration = None
        self.exploration_is_bot = lambda _player: False
        self.exploration_queue = []
        self.exploration_outcomes = []

    @staticmethod
    def _independent_fighter(unit, player):
        return (unit.kind == 'fighter' and unit.location.region == Region.SPACE and
                'ff2' in player.technologies and unit_profile(unit).get('moveValue', 0) > 0)

    @staticmethod
    def _movement_passengers(tile, player):
        return [unit for unit in tile.units if unit.owner == player.faction and
                ((unit.kind == 'fighter' and unit.location.region == Region.SPACE) or
                 (unit.kind in MovementController.GROUND_FORCES and unit.location.region in
                  (Region.PLANET, Region.SPACE, Region.SPACE_AREA)))]

    def _refresh_pickup_sources(self, session):
        """Expose cargo in systems traversed by at least one currently legal route."""
        pickup_positions = set()
        for source in session.sources.values():
            ships = {unit.unit_id: unit for unit in source.ships}
            for unit_id, route in source.routes.items():
                if ships[unit_id].capacity > 0:
                    pickup_positions.update(route[1:])
        session.pickup_sources = {}
        for position in sorted(pickup_positions):
            tile = self.board[position]
            if tile is not session.target and session.player.faction in tile.command_tokens:
                continue
            passengers = self._movement_passengers(tile, session.player)
            if passengers:
                session.pickup_sources[position] = Source(tile, [], passengers, {})
        eligible = {unit.unit_id for source in (*session.sources.values(), *session.pickup_sources.values())
                    for unit in source.ships + source.passengers}
        session.selected.intersection_update(eligible)
        session.cargo_selected.intersection_update(eligible)

    @staticmethod
    def _selected_passengers(session):
        units = {}
        independent_ships = {ship.unit_id for source in session.sources.values() for ship in source.ships}
        for source in (*session.sources.values(), *session.pickup_sources.values()):
            for unit in source.passengers:
                if unit.unit_id in session.selected or unit.unit_id in session.cargo_selected:
                    # Fighter II is either moving independently or being carried.
                    if unit.unit_id in independent_ships and unit.unit_id not in session.cargo_selected:
                        continue
                    units[unit.unit_id] = unit
        return list(units.values())

    @staticmethod
    def _assign_cargo(session, ships, passengers):
        ship_ids = {ship.unit_id for ship in ships}
        session._validate_cargo(ship_ids, {unit.unit_id for unit in passengers})
        # Validation assigns constrained cargo first. Repeat that deterministic
        # order while recording the chosen carrier for transported infantry.
        remaining = {ship.unit_id: ship.capacity for ship in ships}
        for source in session.sources.values():
            for cargo in source.tile.units:
                if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id in remaining:
                    remaining[cargo.location.carrier_id] -= cargo_cost(cargo)
        ordered = []
        for unit in passengers:
            origin = session.passenger_origin(unit.unit_id)
            candidates = [(source, ship) for source in session.sources.values()
                          for ship in ships if ship in source.ships and
                          origin.position in source.routes.get(ship.unit_id, ()) and
                          ship.capacity > 0]
            ordered.append((len(candidates), origin, unit, candidates))
        assignments = []
        for _, origin, unit, candidates in sorted(ordered, key=lambda item: item[0]):
            chosen = next(((source, ship) for source, ship in candidates
                           if remaining[ship.unit_id] >= cargo_cost(unit)), None)
            if chosen is None:
                raise MovementError('Not enough capacity on ships whose routes pass through the pickup system')
            source, carrier = chosen
            remaining[carrier.unit_id] -= cargo_cost(unit)
            location = (UnitLocation(Region.SPACE) if unit.kind == 'fighter' else
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
            if unit.kind == 'fighter':
                session.fighter_carriers[unit.unit_id] = carrier.unit_id
            assignments.append((origin, unit, location))
        return assignments

    def faction_player(self, faction):
        return next((player for player in self.players if player.faction == faction), None)

    def planet_resources(self, planet):
        value = self.exploration.planet_values(planet)[0] if self.exploration else planet.resources
        return value + self.hero_planet_bonus(planet.planet_id)

    def planet_influence(self, planet):
        value = self.exploration.planet_values(planet)[1] if self.exploration else planet.influence
        return value + self.hero_planet_bonus(planet.planet_id)

    def hero_planet_bonus(self, planet_id):
        return 3 if any(planet_id in player.hero_attachments for player in self.players) else 0

    def resolve_pillage(self, neighbor, currency='trade_goods'):
        """Resolve Mentak's Pillage after a qualifying neighbor gain or transaction."""
        if neighbor is None or neighbor.trade_goods < 3:
            return 0
        mentak = self.faction_player('mentak')
        if mentak is None or mentak is neighbor:
            return 0
        mentak_systems = [tile for tile in self.board.values()
                          if mentak.faction in tile.planet_owners.values() or
                          any(unit.owner == mentak.faction for unit in tile.units)]
        neighbor_positions = {tile.position for tile in self.board.values()
                              if neighbor.faction in tile.planet_owners.values() or
                              any(unit.owner == neighbor.faction for unit in tile.units)}
        adjacent = any(tile.position in neighbor_positions or any(
            other.position in neighbor_positions for other in self.neighbors(tile, mentak.faction))
                       for tile in mentak_systems)
        if not adjacent:
            return 0
        if currency == 'trade_goods':
            neighbor.trade_goods -= 1
            mentak.trade_goods += 1
        elif (currency == 'commodities' and neighbor.commodities > 0 and
              mentak.commodities < (self.exploration.relics.commodity_limit(mentak)
                                    if self.exploration else mentak.commodity_limit)):
            neighbor.commodities -= 1
            mentak.commodities += 1
        else:
            return 0
        return 1

    def peace_accords(self, player):
        """Resolve Xxcha's Diplomacy follow-up by claiming the best legal empty planet."""
        if player.faction != 'xxcha':
            return None
        controlled_positions = {tile.position for tile in self.board.values()
                                if player.faction in tile.planet_owners.values()}
        adjacent_positions = {neighbor.position for tile in self.board.values()
                              if tile.position in controlled_positions
                              for neighbor in self.board.neighbors(tile.position)}
        candidates = []
        for tile in self.board.values():
            if tile.number == 18 or tile.position not in adjacent_positions:
                continue
            for planet in tile.planets:
                if tile.planet_owners.get(planet.planet_id) == player.faction:
                    continue
                occupied = any(unit.location.region == Region.PLANET and
                               unit.location.planet_id == planet.planet_id for unit in tile.units)
                if not occupied:
                    candidates.append((tile, planet))
        if not candidates:
            return None
        tile, planet = max(candidates, key=lambda item: (
            item[1].resources + item[1].influence, item[1].resources,
            item[1].influence, item[1].name))
        tile.planet_owners[planet.planet_id] = player.faction
        for other in self.players:
            other.planets[:] = [card for card in other.planets
                                if card.planet.planet_id != planet.planet_id]
        player.planets.append(PlanetCard(planet))
        return planet

    def has_tech(self, faction, alias):
        player = self.faction_player(faction)
        return bool(player and alias in player.technologies)

    def destroy_unit(self, tile, unit, session=None, source_factions=()):
        """Destroy a unit and resolve technology reactions to its destruction."""
        player = self.faction_player(unit.owner)
        in_combat = bool(session and session.combat_type in ('space', 'ground') and
                         session.stage in ('space_combat', 'ground_combat', 'combat_end',
                                           'assault_choice'))
        if (in_combat and unit.owner != 'nekro' and 'nekro' in session.combat_factions and
                'nekro' not in session.nekro_tech_gained):
            nekro = self.faction_player('nekro')
            candidates = sorted((alias for alias in getattr(player, 'technologies', ())
                                 if alias not in getattr(nekro, 'technologies', ()) and
                                 alias in technology_catalog() and
                                 technology_catalog()[alias].get('source') == 'base' and
                                 technology_catalog()[alias].get('faction') in
                                 (None, unit.owner)),
                                key=lambda alias: (not is_unit_upgrade(technology_catalog()[alias]), alias))
            if nekro and candidates:
                try:
                    copy_technology(nekro, candidates[0], self.board,
                                    allow_faction_technology=True)
                    session.nekro_tech_gained.add('nekro')
                except ValueError:
                    pass
        if (session and session.combat_type == 'space' and session.stage in
                ('space_combat', 'combat_end', 'assault_choice') and
                UNIT_TYPES[unit.kind]['ship'] and source_factions):
            threshold = self.combat_threshold(unit.owner, unit_profile(unit))
            available_stage = ('space_combat' if session.stage == 'assault_choice' else
                               'combat_end' if session.combat_needs_resolution else session.stage)
            for faction in source_factions:
                if faction != unit.owner:
                    session.pending_courageous.setdefault(unit.owner, []).append(
                        {'threshold': threshold, 'opponents': (faction,),
                         'stage': available_stage})
        if player and unit.kind == 'infantry':
            upgrade = 'so2' if unit.owner == 'sol' and 'so2' in player.technologies else \
                      'inf2' if 'inf2' in player.technologies else None
            if upgrade and random.randint(1, 10) >= (5 if upgrade == 'so2' else 6):
                player.infantry_on_cards[upgrade] = player.infantry_on_cards.get(upgrade, 0) + 1
                if session:
                    session.rolled_any_dice = True
            if (unit.owner == 'cabal' and unit.location.region == Region.PLANET and
                    any(other is not unit and other.owner == 'cabal' and
                        other.kind == 'mech' and other.location == unit.location
                        for other in tile.units)):
                player.captured_units['infantry'] = player.captured_units.get('infantry', 0) + 1
        if player and unit.kind == 'mech' and 'sar' in player.technologies:
            player.trade_goods += 1
            self.resolve_pillage(player)
        cabal = self.faction_player('cabal')
        cabal_flagship = any(other is not unit and other.owner == 'cabal' and
                             other.kind == 'flagship' and other.location.region == Region.SPACE
                             for other in tile.units)
        if (cabal is not None and cabal_flagship and
                unit.kind not in ('pds', 'spacedock')):
            cabal.captured_units[unit.kind] = cabal.captured_units.get(unit.kind, 0) + 1
        elif in_combat and session is not None and 'cabal' in session.combat_factions:
            if cabal is not None:
                from pok_factions import cabal_capture_destroyed
                cabal_capture_destroyed(cabal, unit, during_combat=True)
        tile.units.remove(unit)
        if mobile_ship(unit):
            for cargo in list(tile.units):
                if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id == unit.unit_id:
                    self.destroy_unit(tile, cargo, session, source_factions)

    def neighbors(self, tile, faction=None):
        neighbors = list(self.board.neighbors(tile.position))
        def wormholes(system):
            types = set(system.wormholes)
            if self.exploration is not None:
                for token in self.exploration.space_tokens.get(system.position, ()):
                    if token == 'gamma':
                        types.add('GAMMA')
                    elif token.startswith('ion_'):
                        types.add(token[4:].upper())
            return types
        tile_holes = wormholes(tile)
        if tile_holes:
            neighbors.extend(other for other in self.board.values()
                             if other is not tile and tile_holes & wormholes(other)
                             and other not in neighbors)
        if faction == 'ghost' and tile_holes & {'ALPHA', 'BETA'}:
            neighbors.extend(other for other in self.board.values()
                             if other is not tile and wormholes(other) & {'ALPHA', 'BETA'}
                             and other not in neighbors)
        return neighbors

    @staticmethod
    def is_gravity_rift(tile):
        return ('gravity_rift' in tile.anomalies or
                any(unit.owner == 'cabal' and unit.kind == 'spacedock'
                    for unit in tile.units))

    @staticmethod
    def ignores_gravity_rift(unit, tile):
        return unit.owner == 'cabal' and any(
            dock.owner == 'cabal' and dock.kind == 'spacedock' for dock in tile.units)

    @staticmethod
    def roll_d10(session):
        session.rolled_any_dice = True
        return random.randint(1, 10)

    def route(self, origin, target, unit, player, bonus=0, move_bonus=0, ignore_enemy=False):
        nomad_hero_exit = (player.faction == 'nomad' and unit.kind == 'flagship' and
                           'nomad_command_token_exit' in player.hero_round_flags)
        if ((player.faction in origin.command_tokens and not nomad_hero_exit) or
                origin is target or unit.move_value <= 0):
            return None
        slipstream = int(player.faction == 'ghost' and
                         (origin.number == 51 or bool(set(origin.wormholes) & {'ALPHA', 'BETA'})))
        budget = (min(unit.move_value, 1) if 'nebula' in origin.anomalies and
                  player.faction != 'empyrean' else unit.move_value) + bonus + move_bonus + slipstream
        queue = deque([(origin, (origin.position,))])
        seen = {(origin.position, self.is_gravity_rift(origin))}
        while queue:
            tile, path = queue.popleft()
            rift_bonus = int(any(self.is_gravity_rift(self.board[position]) for position in path))
            if len(path) - 1 >= budget + rift_bonus:
                continue
            adjacent = self.neighbors(tile, player.faction)
            if player.faction == 'nomad' and unit.kind == 'flagship':
                adjacent.extend(candidate for candidate in self.board.values()
                                if candidate is not tile and candidate not in adjacent and
                                any(mech.owner == 'nomad' and mech.kind == 'mech'
                                    for mech in candidate.units))
            for neighbor in adjacent:
                state = (neighbor.position, rift_bonus or self.is_gravity_rift(neighbor))
                if state in seen:
                    continue
                if ('supernova' in neighbor.anomalies and player.faction != 'muaat') or \
                        ('supernova' in neighbor.anomalies and neighbor is target):
                    continue
                if 'asteroid_field' in neighbor.anomalies and 'amd' not in player.technologies:
                    continue
                next_path = path + (neighbor.position,)
                if neighbor is target:
                    return next_path
                enemy = any(u.owner != player.faction and mobile_ship(u)
                            and u.location.region == Region.SPACE for u in neighbor.units)
                if (enemy and 'lwd' not in player.technologies and not ignore_enemy) or \
                        ('nebula' in neighbor.anomalies and player.faction != 'empyrean'):
                    continue
                seen.add(state)
                queue.append((neighbor, next_path))
        return None

    def activate(self, player, position):
        if self.session:
            raise MovementError('Finish or cancel the current activation first')
        target = self.board[position]
        if player.faction in target.command_tokens:
            raise MovementError('You have already activated this system')
        if player.command_pools['tactical'] <= 0:
            raise MovementError('No command tokens in the tactical reserve')
        sources = {}
        gravity_bonus_ids = set()
        for origin in self.board.values():
            routes = {}
            ships = []
            for unit in origin.units:
                independent_fighter = self._independent_fighter(unit, player)
                if unit.owner == player.faction and ((mobile_ship(unit) and unit.kind != 'fighter') or independent_fighter):
                    path = (target.position,) if origin is target else self.route(origin, target, unit, player)
                    if not path and 'gd' in player.technologies:
                        path = self.route(origin, target, unit, player, bonus=1)
                        if path:
                            gravity_bonus_ids.add(unit.unit_id)
                    if path:
                        ships.append(unit)
                        routes[unit.unit_id] = path
            if ships:
                passengers = self._movement_passengers(origin, player)
                sources[origin.position] = Source(origin, ships, passengers, routes)
        snapshot = Snapshot.capture(self.board, player, self.players or [player],
                                    self.action_cards, self.exploration)
        player.command_pools['tactical'] -= 1
        target.command_tokens.add(player.faction)
        self.session = Session(player, target, sources, snapshot)
        self._magen_activation(target)
        self.session.gravity_bonus_ids = gravity_bonus_ids
        self._refresh_pickup_sources(self.session)
        for opponent in self.players:
            if opponent is not player and 'ers' in opponent.technologies and any(
                    unit.owner == opponent.faction and unit.location.region == Region.SPACE and
                    UNIT_TYPES[unit.kind]['ship'] for unit in target.units):
                opponent.trade_goods += 4
                self.resolve_pillage(opponent)
        return self.session

    def scanlink_planets(self):
        """Planets in the activated system eligible for the optional Scanlink explore."""
        session = self.session
        if (not session or session.stage != 'movement' or session.scanlink_used or
                self.exploration is None or 'sdn' not in session.player.technologies):
            return ()
        return tuple(planet.planet_id for planet in session.target.planets
                     if planet.planet_type.lower() in self.exploration.decks and
                     session.target.planet_owners.get(planet.planet_id) == session.player.faction and
                     any(unit.owner == session.player.faction and
                            unit.location.region == Region.PLANET and
                            unit.location.planet_id == planet.planet_id
                            for unit in session.target.units))

    def explore_with_scanlink(self, planet_id):
        """Use Scanlink Drone Network once, before movement is confirmed."""
        if planet_id not in self.scanlink_planets():
            raise MovementError('Scanlink requires your unit on a planet in the active system.')
        if self.exploration.pending is not None:
            raise MovementError('Resolve the current exploration before using Scanlink.')
        session = self.session
        session.scanlink_used = True
        self.exploration_queue.append((session.player, session.target, planet_id))
        self.resolve_exploration_queue()
        return self.exploration_outcomes[-1][1] if self.exploration_outcomes else None

    def _magen_activation(self, tile):
        """Codex IV Magen places one infantry with each ready structure."""
        for structure in tuple(tile.units):
            if structure.kind not in ('pds', 'spacedock'):
                continue
            owner = self.faction_player(structure.owner)
            if owner is None or 'md' not in owner.technologies:
                continue
            deployed = sum(unit.owner == owner.faction and unit.kind == 'infantry'
                           for system in self.board.values() for unit in system.units)
            if deployed >= 12:
                continue
            location = (UnitLocation(Region.PLANET, structure.location.planet_id)
                        if structure.location.region == Region.PLANET else
                        UnitLocation(Region.TRANSPORT, carrier_id=structure.unit_id))
            tile.units.append(Unit(f'{owner.faction}-magen-{uuid4().hex}', 'infantry',
                                   owner.faction, owner.color_code, location,
                                   profile_id=(upgrade_profile(owner, 'infantry') or {}).get('id')))

    def use_spatial_conduit(self):
        session = self.session
        if not session or session.stage != 'movement' or session.spatial_conduit_active:
            raise MovementError('Spatial Conduit Cylinder is unavailable.')
        player, target = session.player, session.target
        if 'scc' not in player.technologies or 'scc' in player.exhausted_technologies or not any(
                unit.owner == player.faction for unit in target.units):
            raise MovementError('Spatial Conduit Cylinder requires your units in the activated system.')
        player.exhausted_technologies.add('scc')
        session.spatial_conduit_active = True
        for origin in self.board.values():
            if origin is target or player.faction in origin.command_tokens:
                continue
            source = session.sources.get(origin.position)
            for unit in origin.units:
                if unit.owner != player.faction or unit.location.region != Region.SPACE or \
                        not mobile_ship(unit) or not unit.move_value:
                    continue
                if unit.kind == 'fighter' and 'ff2' not in player.technologies:
                    continue
                if source is None:
                    source = Source(origin, [], [], {})
                    session.sources[origin.position] = source
                if unit not in source.ships:
                    source.ships.append(unit)
                    source.routes[unit.unit_id] = (origin.position, target.position)
            if source is not None:
                source.passengers = [u for u in origin.units if u.owner == player.faction and
                                     ((u.kind == 'fighter' and u.location.region == Region.SPACE) or
                                     (u.kind in self.GROUND_FORCES and u.location.region in
                                       (Region.PLANET, Region.SPACE, Region.SPACE_AREA)))]

        self._refresh_pickup_sources(session)

    def apply_flank_speed(self, session):
        """Rebuild eligible movement routes after the activation-window bonus."""
        session.movement_bonus = 1
        sources = {}
        gravity_bonus_ids = set()
        for origin in self.board.values():
            routes, ships = {}, []
            for unit in origin.units:
                independent_fighter = self._independent_fighter(unit, session.player)
                if unit.owner != session.player.faction or not ((mobile_ship(unit) and unit.kind != 'fighter') or independent_fighter):
                    continue
                path = ((session.target.position,) if origin is session.target else
                        self.route(origin, session.target, unit, session.player,
                                   bonus=session.movement_bonus))
                if not path and 'gd' in session.player.technologies:
                    path = self.route(origin, session.target, unit, session.player,
                                      bonus=session.movement_bonus + 1)
                    if path:
                        gravity_bonus_ids.add(unit.unit_id)
                if not path and session.spatial_conduit_active and origin is not session.target and \
                        session.player.faction not in origin.command_tokens:
                    path = (origin.position, session.target.position)
                if path:
                    ships.append(unit)
                    routes[unit.unit_id] = path
            if ships:
                passengers = self._movement_passengers(origin, session.player)
                sources[origin.position] = Source(origin, ships, passengers, routes)
        session.sources = sources
        session.gravity_bonus_ids = gravity_bonus_ids
        self._refresh_pickup_sources(session)

    def apply_silence_space(self, session, position):
        if not session or session.stage != 'movement':
            raise MovementError('In the Silence of Space can only be used during movement.')
        origin = self.board[position]
        player = session.player
        if player.faction in origin.command_tokens:
            raise MovementError('Ships cannot move from a system you have already activated.')
        routes, ships = {}, []
        gravity_bonus_ids = set()
        for unit in origin.units:
            independent_fighter = self._independent_fighter(unit, player)
            if unit.owner != player.faction or not ((mobile_ship(unit) and unit.kind != 'fighter') or independent_fighter):
                continue
            path = self.route(origin, session.target, unit, player, ignore_enemy=True)
            if not path and 'gd' in player.technologies:
                path = self.route(origin, session.target, unit, player, move_bonus=1,
                                  ignore_enemy=True)
                if path:
                    gravity_bonus_ids.add(unit.unit_id)
            if path:
                ships.append(unit)
                routes[unit.unit_id] = path
        if not ships:
            raise MovementError('No ships in that system can reach the activated system.')
        source = session.sources.get(origin.position)
        if source is None:
            source = Source(origin, [], [], {})
            session.sources[origin.position] = source
        source.ships = ships
        source.routes.update(routes)
        source.passengers = self._movement_passengers(origin, player)
        session.gravity_bonus_ids.update(gravity_bonus_ids)
        session.selected.intersection_update(session.choices)
        self._refresh_pickup_sources(session)

    def add_command_token(self, player, position):
        tile = self.board[position]
        if player.faction in tile.command_tokens:
            raise MovementError('This system already has your command token')
        if player.command_pools['tactical'] <= 0:
            raise MovementError('No command tokens in the tactical reserve')
        player.command_pools['tactical'] -= 1
        tile.command_tokens.add(player.faction)

    def orbital_drop(self, player, planet_id):
        if player.faction != 'sol':
            raise MovementError('Orbital Drop is a Sol faction ability')
        if self.session:
            raise MovementError('Finish the current action first')
        if player.command_pools['strategic'] <= 0:
            raise MovementError('Orbital Drop requires 1 token in the strategy pool')
        target = next((tile for tile in self.board.values()
                       if tile.planet_owners.get(planet_id) == player.faction and
                       any(planet.planet_id == planet_id for planet in tile.planets)), None)
        if target is None:
            raise MovementError('Choose a planet you control')
        infantry_count = sum(unit.owner == player.faction and unit.kind == 'infantry'
                             for tile in self.board.values() for unit in tile.units)
        if infantry_count + 2 > 12:
            raise MovementError('Orbital Drop needs 2 infantry in your reinforcements')
        snapshot = Snapshot.capture(self.board, player, self.players,
                                    exploration=self.exploration)
        player.command_pools['strategic'] -= 1
        for _ in range(2):
            target.units.append(Unit(f'{player.faction}-orbital-{uuid4().hex}', 'infantry',
                                     player.faction, player.color_code,
                                     UnitLocation(Region.PLANET, planet_id=planet_id),
                                     profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
        self.history.append(UndoEntry(snapshot))
        return target

    def star_forge(self, player, position, kind):
        if player.faction != 'muaat':
            raise MovementError('Star Forge is an Embers of Muaat faction ability')
        if self.session:
            raise MovementError('Finish the current action first')
        if kind not in ('fighter', 'destroyer'):
            raise MovementError('Choose two fighters or one destroyer')
        if player.command_pools['strategic'] <= 0:
            raise MovementError('Star Forge requires 1 token in the strategy pool')
        target = self.board.get(position)
        if target is None or not any(unit.owner == player.faction and unit.kind == 'warsun' and
                                     unit.location.region == Region.SPACE for unit in target.units):
            raise MovementError('Choose a system with one of your War Suns')
        count = 2 if kind == 'fighter' else 1
        available = {'fighter': 10, 'destroyer': 8}[kind] - sum(
            unit.owner == player.faction and unit.kind == kind
            for tile in self.board.values() for unit in tile.units)
        if available < count:
            raise MovementError(f'Not enough {kind} units in reinforcements')
        snapshot = Snapshot.capture(self.board, player, self.players,
                                    self.action_cards, self.exploration)
        player.command_pools['strategic'] -= 1
        for _ in range(count):
            target.units.append(Unit(f'{player.faction}-star-forge-{uuid4().hex}', kind,
                                     player.faction, player.color_code, UnitLocation(Region.SPACE),
                                     profile_id=(upgrade_profile(player, kind) or {}).get('id')))
        session = Session(player, target, {}, snapshot)
        session.stage = 'complete'
        self.session = session
        self._check_capacity_and_fleet(session, [(target.position, player.faction)], 'complete')
        if session.stage == 'complete':
            self.finish()
        return target

    def mitosis(self, player, planet_id):
        if player.faction != 'arborec':
            raise MovementError('Mitosis is an Arborec faction ability')
        infantry_count = sum(unit.owner == player.faction and unit.kind == 'infantry'
                             for tile in self.board.values() for unit in tile.units)
        if infantry_count >= 12:
            raise MovementError('No infantry remain in your reinforcements')
        target = next((tile for tile in self.board.values()
                       if tile.planet_owners.get(planet_id) == player.faction and
                       any(planet.planet_id == planet_id for planet in tile.planets)), None)
        if target is None:
            raise MovementError('Choose a planet you control')
        target.units.append(Unit(f'arborec-mitosis-{uuid4().hex}', 'infantry', player.faction,
                                 player.color_code, UnitLocation(Region.PLANET, planet_id),
                                 profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
        return target

    @staticmethod
    def fleet_supply(player):
        return (player.command_pools['fleet'] +
                (2 if player.faction == 'letnev' else 0) +
                len(getattr(player, 'foreign_fleet_tokens', ())))

    @staticmethod
    def combat_threshold(faction, profile):
        return (int(profile.get('combatHitsOn') or 10) +
                (1 if faction == 'jolnar' else 0) -
                (1 if faction == 'sardakk' else 0))

    def spend_munitions(self, faction):
        session = self.session
        if not session or session.stage != 'space_combat' or session.combat_type != 'space' or session.combat_needs_resolution:
            raise MovementError('Munitions Reserves can only be used before a space-combat roll')
        if faction != 'letnev' or faction not in session.combat_factions:
            raise MovementError('Only the Barony of Letnev can use Munitions Reserves')
        player = next((player for player in self.players if player.faction == faction), None)
        if player is None or player.trade_goods < 2:
            raise MovementError('Munitions Reserves costs 2 trade goods')
        round_number = session.combat_round + 1
        if (faction, round_number) in session.munitions_spent_round:
            raise MovementError('Munitions Reserves has already been used this round')
        player.trade_goods -= 2
        session.munitions_spent_round.add((faction, round_number))
        session.munitions_ready.add(faction)

    def toggle_combat_reroll(self, faction, roll_index):
        session = self.session
        rolls = session.combat_rolls.get(faction, []) if session else []
        if (not session or session.stage != 'space_combat' or not session.combat_needs_resolution or
                faction not in session.munitions_available or roll_index < 0 or roll_index >= len(rolls)):
            raise MovementError('No Munitions Reserves reroll is available for this die')
        if rolls[roll_index].get('rerolled'):
            raise MovementError('A die can only be rerolled once')
        selected = session.reroll_selected.setdefault(faction, set())
        if roll_index in selected:
            selected.remove(roll_index)
        else:
            selected.add(roll_index)

    def reroll_selected_combat_dice(self, faction):
        session = self.session
        selected = session.reroll_selected.get(faction, set()) if session else set()
        if (not session or session.stage != 'space_combat' or not session.combat_needs_resolution or
                faction not in session.munitions_available or not selected):
            raise MovementError('Select at least one eligible die to reroll')
        for index in sorted(selected):
            roll = session.combat_rolls[faction][index]
            natural = self.roll_d10(session)
            roll['natural'] = natural
            roll['value'] = natural + roll.get('modifier', 0)
            roll['hit'] = roll['value'] >= roll.get('threshold',
                self.combat_threshold(faction, unit_profile(
                    next(unit for unit in session.target.units if unit.unit_id == roll['unit_id']))))
            roll['rerolled'] = True
        session.munitions_available.remove(faction)
        session.reroll_selected.pop(faction, None)
        outgoing_hits = {owner: sum(bool(roll['hit']) for roll in session.combat_rolls.get(owner, []))
                         for owner in session.combat_factions}
        session.combat_hits = {owner: sum(hits for shooter, hits in outgoing_hits.items()
                                           if shooter != owner)
                               for owner in session.combat_factions}
        session.hit_sources = {owner: tuple(shooter for shooter, hits in outgoing_hits.items()
                                            if shooter != owner and hits)
                               for owner in session.combat_factions}
        session.combat_assignments = {owner: [] for owner in session.combat_factions}
        return len(selected)

    def confirm(self):
        session = self.session
        if not session:
            raise MovementError('No active movement')
        if session.stage != 'movement':
            raise MovementError('Ship movement has already been completed')
        transfers = []
        session.fighter_carriers.clear()
        selected_ships = []
        for source in session.sources.values():
            ships = session.ships(source)
            selected_ships.extend(ships)
            transfers.extend((source.tile, ship, UnitLocation(Region.SPACE)) for ship in ships)
            transfers.extend((source.tile, cargo, cargo.location) for cargo in session.carried(source, ships))
        transfers.extend(self._assign_cargo(session, selected_ships,
                                            self._selected_passengers(session)))
        # Apply only after the entire selection has passed validation.
        ships_entering_target = {ship.unit_id for source in session.sources.values()
                                 if source.tile is not session.target
                                 for ship in session.ships(source)}
        transferred_ids = set()
        for origin, unit, _ in transfers:
            if (unit.unit_id in transferred_ids or
                    sum(candidate is unit for candidate in origin.units) != 1):
                raise MovementError('A selected unit is no longer available in its source system. Cancel and reactivate.')
            transferred_ids.add(unit.unit_id)
        session.movement_checkpoint = SessionCheckpoint.capture(self, session, 'movement')
        session.gravity_rift_log.clear()
        lost_ships = set()
        for source in session.sources.values():
            for ship in session.ships(source):
                for position in source.routes.get(ship.unit_id, ())[:-1]:
                    rift = self.board[position]
                    if not self.is_gravity_rift(rift) or self.ignores_gravity_rift(ship, rift):
                        continue
                    value = self.roll_d10(session)
                    lost = value <= 3
                    session.gravity_rift_log.append(
                        f'{ship.owner.upper()} {ship.kind.title()} in tile {rift.system_id}: '
                        f'gravity rift roll {value} — {"removed" if lost else "survived"}.')
                    if lost:
                        lost_ships.add(ship.unit_id)
                        break
        for origin, unit, location in transfers:
            origin.units.remove(unit)
            if unit.unit_id in lost_ships or session.fighter_carriers.get(unit.unit_id) in lost_ships or (
                    location.region == Region.TRANSPORT and location.carrier_id in lost_ships):
                continue
            unit.location = location
            session.target.units.append(unit)
            if unit.unit_id in ships_entering_target:
                session.moved_ship_ids.add(unit.unit_id)
        session.cannon_log = []
        session.landings = ({unit.unit_id: None for unit in self.landing_forces(session)}
                            if session.target.planets else {})
        next_stage = ('space_combat' if self.hostile_space_factions(session.target, session.player.faction)
                      else 'invasion')
        affected = [(origin.position, session.player.faction) for origin, _, _ in transfers]
        affected.append((session.target.position, session.player.faction))
        foresight_destinations = self.foresight_options(session)
        if foresight_destinations:
            session.foresight_faction = 'naalu'
            session.foresight_destinations = tuple(tile.position for tile in foresight_destinations)
            session.foresight_affected = affected
            session.foresight_next_stage = next_stage
            session.stage = 'foresight'
            return len(transfers)
        self._check_capacity_and_fleet(session, affected, next_stage)
        return len(transfers)

    def foresight_options(self, session=None):
        """Return systems eligible for the Naalu Foresight reaction."""
        session = session or self.session
        if not session or session.player.faction == 'naalu':
            return []
        naalu = self.faction_player('naalu')
        if (naalu is None or naalu.command_pools['strategic'] <= 0 or
                not any(unit.owner == 'naalu' and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] for unit in session.target.units)):
            return []
        options = []
        for tile in self.neighbors(session.target, 'naalu'):
            if 'naalu' in tile.command_tokens:
                continue
            if any(unit.owner != 'naalu' and unit.location.region == Region.SPACE and
                   UNIT_TYPES[unit.kind]['ship'] for unit in tile.units):
                continue
            options.append(tile)
        return sorted(options, key=lambda tile: (tile.name, tile.position))

    def resolve_foresight(self, position=None):
        """Accept or decline Naalu Foresight, then resume the interrupted action."""
        session = self.session
        if not session or session.stage != 'foresight':
            raise MovementError('Naalu Foresight is not awaiting a decision.')
        naalu = self.faction_player('naalu')
        if naalu is None:
            raise MovementError('Naalu is no longer in this game.')
        affected = list(session.foresight_affected)
        next_stage = session.foresight_next_stage or 'invasion'
        if position is not None:
            tile = next((candidate for candidate in self.foresight_options(session)
                         if candidate.position == position), None)
            if tile is None:
                raise MovementError('Choose an eligible adjacent system for Foresight.')
            ships = [unit for unit in session.target.units if unit.owner == 'naalu' and
                     unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
            ship_ids = {unit.unit_id for unit in ships}
            moving = ships + [unit for unit in session.target.units
                              if unit.owner == 'naalu' and unit.location.region == Region.TRANSPORT and
                              unit.location.carrier_id in ship_ids]
            for unit in moving:
                session.target.units.remove(unit)
                tile.units.append(unit)
            naalu.command_pools['strategic'] -= 1
            tile.command_tokens.add('naalu')
            affected.extend(((session.target.position, 'naalu'), (tile.position, 'naalu')))
            session.retreat_log = f'Naalu Foresight moved its fleet to {tile.name}.'
        else:
            session.retreat_log = 'Naalu declined Foresight.'
        session.foresight_faction = None
        session.foresight_destinations = ()
        session.foresight_affected.clear()
        session.foresight_next_stage = None
        self._check_capacity_and_fleet(session, affected, next_stage)
        return position is not None

    @staticmethod
    def hostile_space_factions(tile, faction):
        return tuple(sorted({unit.owner for unit in tile.units
                             if unit.owner != faction and unit.location.region == Region.SPACE and
                             UNIT_TYPES[unit.kind]['ship']}))

    def _capacity_units(self, tile, faction):
        return [unit for unit in tile.units if unit.owner == faction and
                ((unit.kind == 'fighter' and unit.location.region in (Region.SPACE, Region.TRANSPORT)) or
                 (unit.kind in self.GROUND_FORCES and unit.location.region in
                  (Region.TRANSPORT, Region.SPACE, Region.SPACE_AREA)))]

    def _capacity_value(self, tile, faction):
        return sum(unit.capacity for unit in tile.units if unit.owner == faction and
                   unit.location.region == Region.SPACE and
                   (capital_ship(unit) or (faction == 'saar' and unit.kind == 'spacedock')))

    def _fighter_ii(self, unit):
        player = self.faction_player(unit.owner)
        return unit.kind == 'fighter' and player is not None and 'ff2' in player.technologies

    def capacity_overflow_units(self, tile, faction):
        """Return basic fighters and transported ground forces eligible to destroy."""
        return [unit for unit in self._capacity_units(tile, faction)
                if not (unit.kind == 'fighter' and unit.location.region == Region.SPACE and
                        self._fighter_ii(unit))]

    def _capacity_accounting(self, tile, faction):
        units = self._capacity_units(tile, faction)
        capacity = self._capacity_value(tile, faction)
        ground_forces = sum(
            (1 if unit.owner == 'argent' and unit.kind == 'mech' and
                  unit.location.region == Region.SPACE and
                  not any(ship.owner == faction and ship.location.region == Region.SPACE and
                          UNIT_TYPES[ship.kind]['ship'] and ship.capacity > 0
                          for ship in tile.units)
             else cargo_cost(unit)) for unit in units if unit.kind in self.GROUND_FORCES)
        base_fighters = [unit for unit in units if unit.kind == 'fighter' and
                         not self._fighter_ii(unit)]
        fighter_ii_in_space = sorted((unit for unit in units if unit.kind == 'fighter' and
                                      unit.location.region == Region.SPACE and self._fighter_ii(unit)),
                                     key=lambda unit: unit.unit_id)
        fighter_ii_transported = [unit for unit in units if unit.kind == 'fighter' and
                                  unit.location.region == Region.TRANSPORT and self._fighter_ii(unit)]
        dock_slots = sum(
            (12 if unit_profile(unit).get('id') == 'cabal_spacedock2' else 6)
            if faction == 'cabal' else 3
            for unit in tile.units if unit.owner == faction and unit.kind == 'spacedock')
        free_base = min(dock_slots, len(base_fighters))
        remaining_dock_slots = max(0, dock_slots - free_base)
        free_transported_fighter_ii = min(remaining_dock_slots, len(fighter_ii_transported))
        remaining_dock_slots -= free_transported_fighter_ii
        free_space_fighter_ii = min(remaining_dock_slots, len(fighter_ii_in_space))
        used = (ground_forces + len(base_fighters) - free_base + len(fighter_ii_transported) -
                free_transported_fighter_ii + len(fighter_ii_in_space) - free_space_fighter_ii)
        eligible_fighter_ii = fighter_ii_in_space[free_space_fighter_ii:]
        excess_count = max(0, used - capacity)
        fleet_fighter_count = min(len(eligible_fighter_ii), excess_count)
        return {
            'capacity': capacity,
            'used': used,
            'fleet_fighter_ids': [unit.unit_id for unit in eligible_fighter_ii[:fleet_fighter_count]],
            'overflow': max(0, excess_count - fleet_fighter_count),
        }

    def capacity_overflow(self, tile, faction):
        return self._capacity_accounting(tile, faction)['overflow']

    def _fleet_ships_at(self, tile, faction):
        ships = [unit for unit in tile.units if unit.owner == faction and
                 unit.location.region == Region.SPACE and capital_ship(unit)]
        accounting = self._capacity_accounting(tile, faction)
        fighters = {unit.unit_id: unit for unit in tile.units}
        return ships + [fighters[unit_id] for unit_id in accounting['fleet_fighter_ids']]

    def fleet_ships(self, session):
        tile = self.board.get(session.overflow_position) if session.overflow_position else session.target
        faction = session.overflow_faction or session.player.faction
        return self._fleet_ships_at(tile, faction)

    def fleet_ship_count(self, session):
        return len(self.fleet_ships(session))

    def _check_capacity_and_fleet(self, session, affected, next_stage):
        unique = []
        for position, faction in affected:
            key = (position, faction)
            if key not in unique and position in self.board:
                unique.append(key)
        session.capacity_affected = unique
        session.capacity_next_stage = next_stage
        session.overflow_next_stage = next_stage
        session.capacity_queue = list(unique)
        session.fleet_queue = []
        self._advance_capacity_checks(session)

    def check_freelancers_limits(self, player, position, snapshot):
        """Check a Freelancers unit, then resume the interrupted exploration."""
        session = self.session
        if session is None:
            session = Session(player, self.board[position], {}, snapshot)
            session.freelancer_temporary = True
            self.session = session
        else:
            session.freelancer_resume_stage = session.stage
        self._check_capacity_and_fleet(session, [(position, player.faction)],
                                       'freelancer_resume')

    def _advance_capacity_checks(self, session):
        while session.capacity_queue:
            position, faction = session.capacity_queue.pop(0)
            tile = self.board[position]
            required = self.capacity_overflow(tile, faction)
            if not required:
                continue
            session.capacity_position = position
            session.capacity_faction = faction
            session.capacity_required = required
            session.capacity_selected.clear()
            session.stage = 'capacity_overflow'
            return
        session.capacity_position = None
        session.capacity_faction = None
        session.capacity_required = 0
        session.capacity_selected.clear()
        session.fleet_queue = list(session.capacity_affected)
        self._advance_fleet_checks(session)

    def _advance_fleet_checks(self, session):
        while session.fleet_queue:
            position, faction = session.fleet_queue.pop(0)
            tile = self.board[position]
            player = self.faction_player(faction)
            if player is None:
                continue
            session.overflow_position = position
            session.overflow_faction = faction
            excess = max(0, len(self._fleet_ships_at(tile, faction)) - self.fleet_supply(player))
            if not excess:
                continue
            session.stage = 'fleet_overflow'
            session.overflow_required = excess
            session.overflow_selected.clear()
            return
        session.overflow_position = None
        session.overflow_faction = None
        session.overflow_required = 0
        session.overflow_selected.clear()
        self._continue_after_limits(session, session.capacity_next_stage)

    def _continue_after_limits(self, session, next_stage):
        if next_stage == 'freelancer_resume':
            if session.freelancer_temporary:
                self.history.append(UndoEntry(session.snapshot, counts_as_action=False))
                self.session = None
            else:
                session.stage = session.freelancer_resume_stage or 'production'
                session.freelancer_resume_stage = None
            self.resolve_exploration_queue()
        elif next_stage in ('invasion', 'space_combat'):
            self.continue_after_movement(session, next_stage)
        elif next_stage == 'integrated_continue':
            self.advance_integrated_production(session)
        elif next_stage == 'after_cannon':
            self._finish_after_space_cannon(session)
        elif next_stage in ('after_combat', 'after_retreat'):
            self.prepare_invasion(session)
        else:
            session.stage = 'complete'
            self.finish()

    def continue_after_movement(self, session, next_stage):
        session.space_cannon_next_stage = next_stage
        if not session.cannon_checked:
            experimental_available = bool(session.moved_ship_ids and self.action_cards and any(
                self.action_cards.can_play(player.faction, 'experimental_battlestation', session)
                for player in self.players))
            if experimental_available and not session.experimental_window_closed:
                session.stage = 'space_cannon_action'
                return
            if not self.resolve_space_cannon(session):
                return
        self.continue_after_space_cannon(session)

    def continue_after_space_cannon(self, session):
        if session.stage == 'space_cannon_direct_hit' and session.space_cannon_direct_hit:
            faction, unit_id = session.space_cannon_direct_hit
            pending = session.pending_direct_hits.get(faction, [])
            if unit_id in pending:
                pending.remove(unit_id)
            session.space_cannon_direct_hit = None
        if session.space_cannon_events and not self.resolve_space_cannon_hits(session):
            return
        factions = {unit.owner for unit in session.target.units
                    if unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']}
        factions.add(session.player.faction)
        self._check_capacity_and_fleet(
            session, [(session.target.position, faction) for faction in sorted(factions)],
            'after_cannon')

    def _finish_after_space_cannon(self, session):
        next_stage = session.space_cannon_next_stage or 'invasion'
        if next_stage == 'space_combat' and self.hostile_space_factions(
                session.target, session.player.faction) and self.combat_units(session, session.player.faction):
            self.start_combat(session)
        else:
            if next_stage == 'space_combat':
                session.space_combat_resolved = True
            self.prepare_invasion(session)

    def resolve_space_cannon(self, session):
        if session.cannon_checked:
            return True
        session.cannon_checked = True
        active_faction = session.player.faction
        shooters = self._space_cannon_shooters(session)
        if not shooters:
            return self._finish_space_cannon_rolls(session)

        active_shooters = [shooter for shooter in shooters if shooter[1].owner == active_faction]
        targets = self.space_cannon_target_options(session)
        if active_shooters and len(targets) > 1 and session.space_cannon_target_faction is None:
            session.stage = 'space_cannon_choose_target'
            return False
        target_faction = session.space_cannon_target_faction or (targets[0] if targets else None)
        return self._roll_space_cannon(session, shooters, target_faction)

    def space_cannon_target_options(self, session):
        """Players whose ships the active player's Space Cannon may target."""
        active_faction = session.player.faction
        return sorted({unit.owner for unit in session.target.units
                       if unit.owner != active_faction and unit.location.region == Region.SPACE and
                       UNIT_TYPES[unit.kind]['ship']})

    def choose_space_cannon_target(self, faction):
        session = self.session
        if not session or session.stage != 'space_cannon_choose_target':
            raise MovementError('There is no Space Cannon target to choose')
        if faction not in self.space_cannon_target_options(session):
            raise MovementError('Choose a player with ships in the active system')
        session.space_cannon_target_faction = faction
        shooters = self._space_cannon_shooters(session)

        resolved = self._roll_space_cannon(session, shooters, faction)
        if resolved:
            self.continue_after_space_cannon(session)
        return resolved

    def _space_cannon_shooters(self, session):
        shooters = []
        neighbor_cache = {}
        for source in self.board.values():
            for unit in source.units:
                profile = unit_profile(unit)
                if ((unit.kind != 'pds' and not (unit.owner == 'xxcha' and
                                                unit.kind == 'mech')) or
                        unit.unit_id in session.disabled_pds or
                        not profile.get('spaceCannonHitsOn')):
                    continue
                if source is not session.target:
                    if not profile.get('deepSpaceCannon') and not (
                            unit.owner == 'xxcha' and unit.kind == 'mech'):
                        continue
                    adjacent = neighbor_cache.setdefault(
                        unit.owner, {tile.position for tile in self.neighbors(session.target, unit.owner)})
                    if source.position not in adjacent:
                        continue
                shooters.append((source, unit, profile))
        titans = self.faction_player('titans')
        if titans:
            from pok_factions import titans_hero_space_cannon
            ability = titans_hero_space_cannon(titans, session.target)
            if ability is not None:
                profile = {'spaceCannonHitsOn': ability[0], 'spaceCannonDieCount': ability[1]}
                cannon = Unit('titans-elysium-hero-cannon', 'pds', 'titans',
                              titans.color_code, UnitLocation(Region.PLANET, 'elysium'))
                shooters.append((session.target, cannon, profile))
        return shooters

    def _roll_space_cannon(self, session, shooters, active_target):
        active_faction = session.player.faction
        plasma_used = set()
        argent = self.faction_player('argent')
        argent_cannon = None
        if argent and leader_available(argent, 'argentcommander'):
            candidates = [(int(profile['spaceCannonHitsOn']), cannon.unit_id)
                          for _, cannon, profile in shooters if cannon.owner == 'argent']
            if candidates:
                argent_cannon = min(candidates)[1]
        for source, cannon, profile in shooters:
            if getattr(session, 'solar_flare', False) and cannon.owner != active_faction:
                continue
            target_faction = active_target if cannon.owner == active_faction else active_faction
            if target_faction is None:
                continue
            target_ships = [unit for unit in session.target.units if unit.owner == target_faction and
                            unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
            if not target_ships:
                continue
            if (target_faction == 'argent' and cannon.owner != 'argent' and
                    any(unit.owner == 'argent' and unit.kind == 'flagship' and
                        unit.location.region == Region.SPACE for unit in session.target.units)):
                continue
            shooter = self.faction_player(cannon.owner)
            plasma_bonus = bool(shooter and 'ps' in shooter.technologies and
                                cannon.owner not in plasma_used)
            extra = int(plasma_bonus)
            if plasma_bonus:
                plasma_used.add(cannon.owner)
            argent_bonus = cannon.unit_id == argent_cannon
            extra += int(argent_bonus)
            dice = [self.roll_d10(session) for _ in range(int(profile.get('spaceCannonDieCount') or 1) + extra)]
            hits = sum(value >= int(profile['spaceCannonHitsOn']) for value in dice)
            graviton = bool(shooter and 'gls' in shooter.technologies and
                            'gls' not in shooter.exhausted_technologies and
                            any(unit.kind == 'fighter' for unit in target_ships) and
                            any(unit.kind != 'fighter' for unit in target_ships))
            if graviton:
                shooter.exhausted_technologies.add('gls')
            results = ', '.join(format_die_result(
                value, 0, value >= int(profile['spaceCannonHitsOn']))
                for value in dice)
            session.cannon_log.append(
                f'{cannon.owner.upper()} {cannon.kind.title()} in tile {source.system_id} '
                f'targeting {target_faction.upper()} '
                f'({profile["spaceCannonHitsOn"]}+): {results} → {hits} hit(s)' +
                (' · Plasma Scoring +1 die' if plasma_bonus else '') +
                (' · Argent commander +1 die' if argent_bonus else ''))
            for _ in range(hits):
                candidates = [unit for unit in target_ships if unit in session.target.units]
                if not candidates:
                    break
                if graviton:
                    nonfighters = [unit for unit in candidates if unit.kind != 'fighter']
                    if nonfighters:
                        candidates = nonfighters
                session.space_cannon_events.append({'owner': cannon.owner,
                                                    'target': target_faction,
                                                    'candidates': [unit.unit_id for unit in candidates],
                                                    'graviton': graviton})
        return self._finish_space_cannon_rolls(session)

    def _finish_space_cannon_rolls(self, session):
        if session.space_cannon_events and self.action_cards:
            session.stage = 'space_cannon_response'
            if any(self.action_cards.can_play(player.faction, 'maneuvering_jets', session)
                   for player in self.players):
                return False
        return self.resolve_space_cannon_hits(session)

    def resolve_space_cannon_hits(self, session):
        while session.space_cannon_events:
            event = session.space_cannon_events[0]
            candidates = self.space_cannon_assignment_candidates(session)
            if not candidates:
                session.space_cannon_events.pop(0)
                continue
            if session.space_cannon_nes_cancel:
                session.space_cannon_nes_cancel -= 1
                session.space_cannon_events.pop(0)
                session.cannon_log.append('Non-Euclidean Shielding canceled 1 additional hit.')
                continue
            session.stage = 'space_cannon_assign'
            return False
        return True

    def space_cannon_assignment_candidates(self, session):
        if not session or not session.space_cannon_events:
            return []
        event = session.space_cannon_events[0]
        candidates = [unit for unit in session.target.units
                      if unit.unit_id in event['candidates'] and
                      unit.owner == event.get('target', session.player.faction) and
                      unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        if event.get('graviton'):
            nonfighters = [unit for unit in candidates if unit.kind != 'fighter']
            if nonfighters:
                candidates = nonfighters
        return candidates

    def assign_space_cannon_hit(self, unit_id):
        session = self.session
        if not session or session.stage != 'space_cannon_assign' or not session.space_cannon_events:
            raise MovementError('There is no Space Cannon hit waiting for assignment')
        candidates = self.space_cannon_assignment_candidates(session)
        target = next((unit for unit in candidates if unit.unit_id == unit_id), None)
        if target is None:
            raise MovementError('Choose an eligible ship to take the Space Cannon hit')

        event = session.space_cannon_events.pop(0)
        self.apply_hit(session.target, [target], session)
        if target in session.target.units and target.damaged and self.has_tech(target.owner, 'nes'):
            session.space_cannon_nes_cancel = 1
        outcome = 'damaged' if target in session.target.units and target.damaged else 'destroyed'
        session.cannon_log.append(f'{target.owner.upper()} {target.kind.title()} {outcome}')
        if target in session.target.units and target.damaged and event['owner'] != target.owner:
            session.pending_direct_hits.setdefault(event['owner'], []).append(target.unit_id)
            session.space_cannon_direct_hit = (event['owner'], target.unit_id)
            session.stage = 'space_cannon_direct_hit'
            if (self.action_cards and
                    self.action_cards.can_play(event['owner'], 'direct_hit', session)):
                return
            session.stage = 'space_cannon_assign'
            session.pending_direct_hits[event['owner']].remove(target.unit_id)
            session.space_cannon_direct_hit = None

        self.continue_after_space_cannon(session)

    def apply_hit(self, tile, candidates, session=None, source_factions=()):
        target = sorted(candidates, key=lambda unit: (
            not (self.can_sustain(tile, unit) and not unit.damaged), unit.unit_id))[0]
        if self.can_sustain(tile, target) and not target.damaged:
            self.sustain_damage(target)
        else:
            self.destroy_unit(tile, target, session, source_factions)
        return target

    def sustain_damage(self, unit):
        """Use SUSTAIN DAMAGE and resolve passive commander rewards."""
        unit.damaged = True
        player = self.faction_player(unit.owner)
        if player and unit.owner == 'letnev' and leader_available(player, 'letnevcommander'):
            player.trade_goods += 1

    def winnu_commander_bonus(self, tile):
        player = self.faction_player('winnu')
        if not player or not leader_available(player, 'winnucommander'):
            return 0
        if (tile.number == 18 or any(planet.faction_homeworld == 'winnu'
                                     for planet in tile.planets) or
                (self.exploration and any(self.exploration.planet_is_legendary(planet)
                                          for planet in tile.planets))):
            return 2
        return 0

    @staticmethod
    def can_sustain(tile, unit):
        if not unit_profile(unit).get('sustainDamage'):
            return False
        if unit.location.region != Region.PLANET or unit.kind not in ('infantry', 'mech'):
            return True
        return not any(other.owner == 'mentak' and unit.owner != 'mentak' and
                       other.kind == 'mech' and other.location == unit.location
                       for other in tile.units)

    def resolve_anti_fighter_barrage(self, session):
        session.afb_rolls.clear()
        session.afb_log.clear()
        factions = session.combat_factions
        # All AFB dice are rolled before casualties are removed.  The ability
        # can fire even without opposing fighters (notably Argent's Raid
        # Formation and its upgraded destroyer).
        initial_fighters = {faction: sum(unit.kind == 'fighter' for unit in
                                         self.combat_units(session, faction))
                            for faction in factions}
        argent = self.faction_player('argent')
        argent_barrage = None
        if argent and leader_available(argent, 'argentcommander'):
            candidates = [(int(unit_profile(ship)['afbHitsOn']), ship.unit_id)
                          for ship in self.combat_units(session, 'argent')
                          if unit_profile(ship).get('afbHitsOn')]
            if candidates:
                argent_barrage = min(candidates)[1]
        for faction in factions:
            rolls = []
            for ship in self.combat_units(session, faction):
                profile = unit_profile(ship)
                if not profile.get('afbHitsOn'):
                    continue
                base_dice = int(profile.get('afbDieCount') or 1)
                argent_bonus = ship.unit_id == argent_barrage
                for die_index in range(base_dice + int(argent_bonus)):
                    value = self.roll_d10(session)
                    rolls.append({'unit_id': ship.unit_id, 'kind': ship.kind, 'value': value,
                                  'hit': value >= int(profile['afbHitsOn']),
                                  'bonus_source': ('argentcommander' if
                                      argent_bonus and die_index >= base_dice else None)})
            session.afb_rolls[faction] = rolls
        for faction in factions:
            rolls = session.afb_rolls[faction]
            hits = sum(roll['hit'] for roll in rolls)
            enemies = [unit for enemy in factions if enemy != faction
                       for unit in self.combat_units(session, enemy) if unit.kind == 'fighter']
            destroyed = 0
            for enemy in sorted(enemies, key=lambda unit: unit.unit_id):
                if not hits:
                    break
                if enemy in session.target.units:
                    self.destroy_unit(session.target, enemy, session, (faction,))
                    destroyed += 1
                    hits -= 1
            if faction == 'argent':
                from pok_factions import argent_raid_targets
                excess = max(0, sum(roll['hit'] for roll in rolls) -
                             sum(count for owner, count in initial_fighters.items()
                                 if owner != faction))
                ships = [unit for enemy in factions if enemy != faction
                         for unit in self.combat_units(session, enemy)]
                for ship in argent_raid_targets(excess, 0, ships):
                    ship.damaged = True
            if faction == 'argent':
                anti_infantry_hits = sum(roll['hit'] and roll['value'] >= 9 and
                                        any(ship.unit_id == roll['unit_id'] and
                                            unit_profile(ship).get('id') == 'argent_destroyer2'
                                            for ship in session.target.units)
                                        for roll in rolls)
                space_infantry = sorted((unit for unit in session.target.units
                                        if unit.owner != faction and unit.kind == 'infantry' and
                                        unit.location.region in (Region.SPACE, Region.TRANSPORT)),
                                       key=lambda unit: unit.unit_id)
                for infantry in space_infantry[:anti_infantry_hits]:
                    self.destroy_unit(session.target, infantry, session, (faction,))
            session.afb_log.append(
                f'{faction.upper()} anti-fighter barrage: {sum(r["hit"] for r in rolls)} hit(s); '
                f'{destroyed} fighter(s) destroyed' +
                (' · Argent commander +1 die' if any(
                    roll.get('bonus_source') == 'argentcommander' for roll in rolls) else ''))
        session.afb_resolved = True

    def retreat_options(self, session, faction):
        fleet = self.combat_units(session, faction)
        if not fleet:
            return []
        options = []
        for tile in self.neighbors(session.target):
            hostile_fleet = any(unit.owner != faction and unit.location.region == Region.SPACE and
                                UNIT_TYPES[unit.kind]['ship'] for unit in tile.units)
            has_friendly = any(unit.owner == faction and unit.location.region == Region.SPACE and
                               UNIT_TYPES[unit.kind]['ship'] for unit in tile.units)
            if session.skilled_retreat:
                retreating_player = next((player for player in self.players if player.faction == faction),
                                         session.player)
                if (not hostile_fleet and faction not in tile.command_tokens and
                        self.action_cards and self.action_cards.has_reinforcement(retreating_player)):
                    options.append(tile)
                continue
            # A fleet may retreat into a system that already carries its faction's
            # command token.  Retreat does not spend another token.
            if not hostile_fleet and (has_friendly or
                                      (self.has_tech(faction, 'det') and
                                       not any(unit.owner != faction for unit in tile.units))):
                options.append(tile)
        return options

    def announce_retreat(self, faction=None):
        session = self.session
        if not session or session.stage != 'space_combat' or session.combat_needs_resolution:
            raise MovementError('Retreat can only be announced before combat rolls')
        faction = session.player.faction if faction is None else faction
        if faction not in session.combat_factions:
            raise MovementError('This faction is not participating in space combat')
        if session.retreat_blocked_round == session.combat_round + 1:
            raise MovementError('Your retreat was intercepted for this combat round.')
        if session.retreat_announced:
            raise MovementError('A retreat has already been announced')
        if not self.retreat_options(session, faction):
            raise MovementError('No adjacent system with your ships is available for retreat')
        session.retreat_announced = faction

    def decline_retreat(self, faction):
        session = self.session
        if (not session or session.stage != 'space_combat' or
                session.combat_needs_resolution or session.retreat_announced):
            raise MovementError('There is no retreat decision to decline')
        if faction not in session.combat_factions:
            raise MovementError('This faction is not participating in space combat')
        if not self.retreat_options(session, faction):
            raise MovementError('No adjacent system with your ships is available for retreat')
        session.retreat_declined_round = session.combat_round + 1

    def resolve_retreat(self, position):
        session = self.session
        if not session or session.stage != 'retreat_selection' or not session.retreat_announced:
            raise MovementError('No retreat destination is being selected')
        destination = self.board[position]
        if destination not in self.retreat_options(session, session.retreat_announced):
            raise MovementError('This system is not a valid retreat destination')
        ships = [unit for unit in session.target.units if unit.owner == session.retreat_announced and
                 unit.location.region == Region.SPACE and
                 (UNIT_TYPES[unit.kind]['ship'] or unit.profile_id == 'naaz_mech_space')]
        ship_ids = {unit.unit_id for unit in ships}
        retreating = ships + [unit for unit in session.target.units if unit.location.region == Region.TRANSPORT and
                              unit.location.carrier_id in ship_ids]
        for unit in retreating:
            session.target.units.remove(unit)
            destination.units.append(unit)
        self._reset_naaz_space_mechs(session)
        for unit in destination.units:
            if unit.owner == 'naaz' and unit.kind == 'mech' and \
                    unit.profile_id == 'naaz_mech_space':
                unit.profile_id = 'naaz_mech'
                unit.location = UnitLocation(Region.SPACE_AREA)
        retreating_player = next((player for player in self.players
                                  if player.faction == session.retreat_announced), session.player)
        if (session.skilled_retreat and session.retreat_announced not in destination.command_tokens and
                self.action_cards and self.action_cards.has_reinforcement(retreating_player)):
            destination.command_tokens.add(session.retreat_announced)
        elif not session.skilled_retreat and session.retreat_announced not in destination.command_tokens:
            destination.command_tokens.add(session.retreat_announced)
        session.retreat_log = f'{session.retreat_announced.upper()} retreated to tile {destination.system_id}.'
        session.space_combat_resolved = True
        factions = {session.retreat_announced, *session.combat_factions}
        affected = [(tile.position, faction) for tile in (session.target, destination)
                    for faction in factions]
        self._check_capacity_and_fleet(session, affected, 'after_retreat')

    def has_planetary_shield(self, tile):
        session = self.session if self.session and self.session.target is tile else None
        return any(unit.kind == 'pds' and unit_profile(unit).get('planetaryShield') and
                   (session is None or unit.unit_id not in session.disabled_pds)
                   for unit in tile.units)

    def prepare_invasion(self, session):
        session.stage = 'invasion_start'
        session.invasion_window_closed = False
        if self.action_cards and any(
                self.action_cards.can_play(player.faction, card, session)
                for player in self.players for card in player.action_cards):
            return
        self.continue_invasion_start(session)

    def continue_invasion_start(self, session):
        session.invasion_window_closed = True
        faction = session.player.faction
        hostile_ground = {unit.location.planet_id for unit in session.target.units
                          if unit.owner != faction and unit.location.region == Region.PLANET and
                          self.ground_combatant(unit)}
        bombers = [unit for unit in session.target.units if unit.owner == faction and
                   ((unit.location.region == Region.SPACE and
                     (unit_profile(unit).get('bombardHitsOn') or
                      (faction in getattr(session, 'blitz_factions', ()) and
                       UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter'))) or
                    (faction == 'l1z1x' and unit.kind == 'mech' and
                     unit.location.region == Region.PLANET and
                     unit.location.planet_id not in hostile_ground))]
        war_sun_present = any(unit.kind == 'warsun' and unit.owner == faction and
                              unit.location.region == Region.SPACE for unit in session.target.units)
        hostile_shield = any(unit.owner != faction and unit.unit_id not in session.disabled_pds and
                             unit.kind == 'pds' and
                             unit_profile(unit).get('planetaryShield') for unit in session.target.units) and \
            not war_sun_present and not (faction == 'l1z1x' and
                leader_available(session.player, 'l1z1xcommander'))
        session.bombard_targets.clear()
        if hostile_shield:
            session.bombardment_resolved = True
            session.bombardment_cancelled = True
            session.bombard_log.append('Bombardment canceled by planetary shield in this system.')
        elif hostile_ground and bombers:
            session.bombardment_cancelled = False
            session.bombard_targets = {unit.unit_id: None for unit in bombers}
            session.bombardment_resolved = False
            session.bombardment_checkpoint = SessionCheckpoint.capture(self, session, 'bombardment')
            session.stage = 'bombardment'
            return
        else:
            session.bombardment_resolved = True
            session.bombardment_cancelled = False
        self.begin_invasion(session)

    def cycle_bombardment_target(self, unit_id):
        session = self.session
        if not session or session.stage != 'bombardment' or unit_id not in session.bombard_targets:
            raise MovementError('This ship cannot bombard a planet')
        faction = session.player.faction
        options = [planet.planet_id for planet in session.target.planets
                   if any(unit.owner != faction and unit.location.region == Region.PLANET and
                          unit.location.planet_id == planet.planet_id and self.ground_combatant(unit)
                          for unit in session.target.units) and
                   not any(unit.unit_id == unit_id and unit.kind == 'mech' and
                           unit.location.region == Region.PLANET and
                           unit.location.planet_id == planet.planet_id
                           for unit in session.target.units)]
        choices = [None, *options]
        current = session.bombard_targets[unit_id]
        session.bombard_targets[unit_id] = choices[(choices.index(current) + 1) % len(choices)]

    def resolve_bombardment(self):
        session = self.session
        if not session or session.stage != 'bombardment':
            raise MovementError('Bombardment is not active')
        faction = session.player.faction
        plasma_available = 'ps' in session.player.technologies
        argent_bomber = None
        if faction == 'argent' and leader_available(session.player, 'argentcommander'):
            candidates = [(int(unit_profile(unit).get('bombardHitsOn') or 6), unit.unit_id)
                          for unit in session.target.units
                          if unit.unit_id in session.bombard_targets and
                          session.bombard_targets[unit.unit_id] is not None]
            if candidates:
                argent_bomber = min(candidates)[1]
        nes_cancel = {}
        for unit_id, planet_id in session.bombard_targets.items():
            if planet_id is None:
                continue
            ship = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
            if ship is None:
                continue
            profile = unit_profile(ship)
            threshold = int(profile.get('bombardHitsOn') or 6)
            rolls = []
            plasma_bonus = plasma_available
            argent_bonus = ship.unit_id == argent_bomber
            extra = int(plasma_bonus) + int(argent_bonus)
            plasma_available = False
            for _ in range(int(profile.get('bombardDieCount') or 1) + extra):
                natural_value = self.roll_d10(session)
                planet_owner = session.target.planet_owners.get(planet_id)
                modifier = -4 if planet_owner in session.bunker_factions else 0
                value = natural_value + modifier
                rolls.append({'unit_id': unit_id, 'kind': ship.kind, 'planet_id': planet_id,
                              'value': value, 'natural': natural_value, 'modifier': modifier,
                              'hit': value >= threshold})
            session.bombard_rolls.extend(rolls)
            hits = sum(roll['hit'] for roll in rolls) * (2 if 'x89c4' in session.player.technologies else 1)
            if 'x89c4' in session.player.technologies:
                for owner in self.players:
                    for card in owner.planets:
                        if card.planet.planet_id == planet_id:
                            card.exhausted = True
            destroyed = 0
            for _ in range(hits):
                if nes_cancel.get(planet_id, 0):
                    nes_cancel[planet_id] -= 1
                    continue
                defenders = [unit for unit in session.target.units if unit.owner != faction and
                             unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                             self.ground_combatant(unit)]
                if not defenders:
                    break
                target = self.apply_hit(session.target, defenders, session)
                if target in session.target.units and target.damaged and \
                        self.has_tech(target.owner, 'nes'):
                    nes_cancel[planet_id] = 1
                destroyed += 1
            planet = next(planet for planet in session.target.planets if planet.planet_id == planet_id)
            dice_results = ', '.join(format_die_result(roll['natural'], roll['modifier'], roll['hit'])
                                     for roll in rolls)
            session.bombard_log.append(
                f'{ship.kind.title()} bombarded {planet.name} ({threshold}+): '
                f'{dice_results} → {destroyed} hit(s) assigned.' +
                (' · Plasma Scoring +1 die' if plasma_bonus else '') +
                (' · Argent commander +1 die' if argent_bonus else ''))
        session.bombardment_resolved = True
        self.begin_invasion(session)

    def begin_invasion(self, session):
        live_forces = {unit.unit_id for unit in self.landing_forces(session)}
        session.landings = {unit_id: planet_id for unit_id, planet_id in session.landings.items()
                            if unit_id in live_forces}
        session.stage = 'invasion'

    def toggle_overflow_ship(self, unit_id):
        session = self.session
        if not session or session.stage != 'fleet_overflow':
            raise MovementError('Fleet limit is not being checked')
        ships = {unit.unit_id for unit in self.fleet_ships(session)}
        if unit_id not in ships:
            raise MovementError('This ship cannot be removed from the fleet')
        if unit_id in session.overflow_selected:
            session.overflow_selected.remove(unit_id)
        elif len(session.overflow_selected) < session.overflow_required:
            session.overflow_selected.add(unit_id)

    def toggle_capacity_overflow_unit(self, unit_id):
        session = self.session
        if not session or session.stage != 'capacity_overflow':
            raise MovementError('Capacity is not being checked')
        tile = self.board[session.capacity_position]
        eligible = {unit.unit_id for unit in self.capacity_overflow_units(tile, session.capacity_faction)}
        if unit_id not in eligible:
            raise MovementError('This unit does not exceed capacity')
        if unit_id in session.capacity_selected:
            session.capacity_selected.remove(unit_id)
        elif len(session.capacity_selected) < session.capacity_required:
            session.capacity_selected.add(unit_id)

    def resolve_capacity_overflow(self):
        session = self.session
        if not session or session.stage != 'capacity_overflow':
            raise MovementError('Capacity is not being checked')
        if len(session.capacity_selected) != session.capacity_required:
            raise MovementError(f'Select exactly {session.capacity_required} fighters or ground forces to destroy')
        tile = self.board[session.capacity_position]
        selected = set(session.capacity_selected)
        for unit in list(tile.units):
            if unit.unit_id in selected:
                tile.units.remove(unit)
        session.capacity_required = 0
        session.capacity_selected.clear()
        session.capacity_position = None
        session.capacity_faction = None
        self._advance_capacity_checks(session)

    def resolve_fleet_overflow(self):
        session = self.session
        if not session or session.stage != 'fleet_overflow':
            raise MovementError('Fleet limit is not being checked')
        if len(session.overflow_selected) != session.overflow_required:
            raise MovementError(f'Select exactly {session.overflow_required} ships or fighters to destroy')
        destroyed = set(session.overflow_selected)
        tile = self.board[session.overflow_position]
        for unit in list(tile.units):
            if unit.unit_id in destroyed or (
                    unit.location.region == Region.TRANSPORT and unit.location.carrier_id in destroyed):
                tile.units.remove(unit)
        next_stage = session.capacity_next_stage
        session.overflow_required = 0
        session.overflow_selected.clear()
        session.overflow_position = None
        session.overflow_faction = None
        session.overflow_next_stage = None
        self._check_capacity_and_fleet(session, session.capacity_affected, next_stage)

    def start_combat(self, session):
        session.combat_type = 'space'
        session.stage = 'space_combat'
        session.space_combat_resolved = False
        session.skilled_retreat = False
        session.combat_planet_id = None
        for unit in session.target.units:
            if (unit.owner == 'naaz' and unit.kind == 'mech' and
                    unit.location.region in (Region.TRANSPORT, Region.SPACE_AREA, Region.SPACE)):
                unit.location = UnitLocation(Region.SPACE)
                unit.profile_id = 'naaz_mech_space'
        session.combat_factions = (session.player.faction,) + self.hostile_space_factions(
            session.target, session.player.faction)
        if 'winnu' in session.combat_factions and session.target.number == 18:
            from leaders import notify_leader_event
            notify_leader_event(self.faction_player('winnu'), 'mecatol_combat')
        assault_users = [faction for faction in session.combat_factions
                         if self.has_tech(faction, 'asc') and sum(
                             unit.owner == faction and unit.location.region == Region.SPACE and
                             capital_ship(unit) for unit in session.target.units) >= 3]
        session.assault_log.clear()
        session.assault_queue = assault_users
        while session.assault_queue and not self.assault_victims(session):
            session.assault_queue.pop(0)
        if session.assault_queue and self.assault_victims(session):
            session.stage = 'assault_choice'
            return
        self.finish_space_combat_setup(session)

    def assault_victims(self, session):
        if not session.assault_queue:
            return []
        attacker = session.assault_queue[0]
        return [unit for unit in session.target.units if unit.owner != attacker and
                unit.location.region == Region.SPACE and capital_ship(unit)]

    def choose_assault_victim(self, unit_id):
        session = self.session
        if not session or session.stage != 'assault_choice':
            raise MovementError('Assault Cannon is not being resolved.')
        victim = next((unit for unit in self.assault_victims(session)
                       if unit.unit_id == unit_id), None)
        if victim is None:
            raise MovementError('Choose one of your non-fighter ships to destroy.')
        attacker = session.assault_queue.pop(0)
        self.destroy_unit(session.target, victim, session, (attacker,))
        session.assault_log.append(f'{attacker.upper()} Assault Cannon destroyed a '
                                   f'{victim.owner.upper()} {victim.kind}.')
        while session.assault_queue and not self.assault_victims(session):
            session.assault_queue.pop(0)
        if not session.assault_queue:
            self.finish_space_combat_setup(session)

    def finish_space_combat_setup(self, session):
        if not session.mentak_ambush_resolved:
            session.mentak_ambush_resolved = True
            mentak = self.faction_player('mentak')
            if mentak and 'mentak' in session.combat_factions:
                ambush_ships = sorted(
                    (unit for unit in self.combat_units(session, 'mentak')
                     if unit.kind in ('cruiser', 'destroyer')),
                    key=lambda unit: (self.combat_threshold('mentak', unit_profile(unit)),
                                      unit.kind, unit.unit_id))[:2]
                opponents = [faction for faction in session.combat_factions if faction != 'mentak']
                for index, ship in enumerate(ambush_ships):
                    value = self.roll_d10(session)
                    threshold = self.combat_threshold('mentak', unit_profile(ship))
                    hit = value >= threshold
                    session.mentak_ambush_rolls.append(
                        {'unit_id': ship.unit_id, 'kind': ship.kind, 'value': value,
                         'threshold': threshold, 'hit': hit})
                    if hit and opponents:
                        target = opponents[index % len(opponents)]
                        candidates = [unit.unit_id for unit in self.combat_units(session, target)]
                        session.space_cannon_events.append({
                            'owner': 'mentak', 'target': target, 'candidates': candidates,
                            'graviton': False, 'source': 'mentak_ambush'})
                if session.space_cannon_events:
                    session.space_cannon_next_stage = 'space_combat'
                    self.resolve_space_cannon_hits(session)
                    return
        self.resolve_anti_fighter_barrage(session)
        session.combat_factions = tuple(faction for faction in session.combat_factions
                                        if self.combat_units(session, faction))
        session.combat_round = 0
        session.combat_rolls.clear()
        session.combat_hits.clear()
        session.combat_assignments.clear()
        session.munitions_ready.clear()
        session.munitions_available.clear()
        session.reroll_selected.clear()
        session.combat_needs_resolution = False
        session.combat_modifiers.clear()
        session.fighter_combat_modifiers.clear()
        session.played_action_windows.clear()
        if len(session.combat_factions) > 1:
            session.stage = 'space_combat'
        else:
            self._reset_naaz_space_mechs(session)
            session.space_combat_resolved = True
            self._check_capacity_and_fleet(
                session, [(session.target.position, faction) for faction in session.combat_factions],
                'after_combat')

    @staticmethod
    def _reset_naaz_space_mechs(session):
        for unit in session.target.units:
            if unit.owner == 'naaz' and unit.kind == 'mech' and \
                    unit.profile_id == 'naaz_mech_space':
                unit.profile_id = 'naaz_mech'
                unit.location = UnitLocation(Region.SPACE_AREA)

    def start_ground_combat(self, session, planet_id):
        session.combat_type = 'ground'
        session.combat_planet_id = planet_id
        defenders = tuple(sorted({unit.owner for unit in session.target.units
                                  if unit.owner != session.player.faction and
                                  unit.location.region == Region.PLANET and
                                  unit.location.planet_id == planet_id and
                                  self.ground_combatant(unit)}))
        session.combat_factions = (session.player.faction,) + defenders
        if 'winnu' in session.combat_factions and session.target.number == 18:
            from leaders import notify_leader_event
            notify_leader_event(self.faction_player('winnu'), 'mecatol_combat')
        yin = self.faction_player('yin')
        if yin and 'yin' in session.combat_factions:
            candidates = sorted((unit for unit in session.target.units
                                 if unit.owner != 'yin' and unit.kind == 'infantry' and
                                 unit.location.region == Region.PLANET and
                                 unit.location.planet_id == planet_id),
                                key=lambda unit: (unit.damaged, unit.unit_id))
            controlled_planets = {planet_id for tile in self.board.values()
                                  for planet_id, owner in tile.planet_owners.items()
                                  if owner == 'yin'}
            ready_cards = [card for card in yin.planets if not card.exhausted and
                           card.planet.planet_id in controlled_planets]
            ready_cards.sort(key=lambda card: (-self.planet_influence(card.planet),
                                               self.planet_resources(card.planet),
                                               card.planet.planet_id))
            payment = []
            influence = 0
            for card in ready_cards:
                payment.append(card)
                influence += self.planet_influence(card.planet)
                if influence >= 2:
                    break
            owned_infantry = sum(unit.owner == 'yin' and unit.kind == 'infantry'
                                 for tile in self.board.values() for unit in tile.units)
            if candidates and influence >= 2 and owned_infantry < 12:
                for card in payment:
                    card.exhausted = True
                session.target.units.remove(candidates[0])
                session.target.units.append(Unit(f'yin-indoctrination-{uuid4().hex}', 'infantry',
                                                 'yin', yin.color_code,
                                                 UnitLocation(Region.PLANET, planet_id),
                                                 profile_id=(upgrade_profile(yin, 'infantry') or {}).get('id')))
                from leaders import notify_leader_event
                notify_leader_event(yin, 'used_faction_ability')
        session.combat_round = 0
        session.combat_rolls.clear()
        session.combat_hits.clear()
        session.combat_assignments.clear()
        session.combat_needs_resolution = False
        session.combat_sustained_this_round.clear()
        session.sardakk_retaliation_resolving = False
        session.magen_suppressed = None
        session.hit_sources.clear()
        for faction in session.combat_factions:
            player = self.faction_player(faction)
            if player and 'md' in player.technologies and any(
                    unit.owner == faction and unit.kind in ('pds', 'spacedock') and
                    unit.location.region == Region.PLANET and
                    unit.location.planet_id == planet_id for unit in session.target.units):
                for opponent in session.combat_factions:
                    if opponent != faction:
                        session.combat_hits[opponent] = session.combat_hits.get(opponent, 0) + 1
                        session.hit_sources[opponent] = (faction,)
        if session.combat_hits:
            session.combat_assignments = {faction: [] for faction in session.combat_factions}
            session.combat_needs_resolution = True
            session.assault_log.append('Magen Defense Grid produced 1 ground-combat hit.')
        session.combat_rolls.clear()
        session.combat_modifiers.clear()
        session.fighter_combat_modifiers.clear()
        session.stage = 'ground_combat'

    def combat_units(self, session, faction):
        if session.combat_type == 'ground':
            return [unit for unit in session.target.units if unit.owner == faction and
                    unit.location.region == Region.PLANET and
                    unit.location.planet_id == session.combat_planet_id and
                    self.ground_combatant(unit)]
        return [unit for unit in session.target.units if unit.owner == faction and
                unit.location.region == Region.SPACE and
                (UNIT_TYPES[unit.kind]['ship'] or
                 (unit.owner == 'naaz' and unit.profile_id == 'naaz_mech_space'))]

    @classmethod
    def ground_combatant(cls, unit):
        return unit.kind in cls.GROUND_FORCES or (unit.owner == 'titans' and unit.kind == 'pds')

    def combat_hit_capacity(self, session, faction):
        total = 0
        for unit in self.combat_units(session, faction):
            sustain = self.can_sustain(session.target, unit)
            bonus = 2 if sustain and not unit.damaged and self.has_tech(faction, 'nes') else \
                    1 if sustain and not unit.damaged else 0
            total += 1 + bonus
        return total

    def combat_assignment_target(self, session, faction, kind, unit_id=None):
        assigned = session.combat_assignments.setdefault(faction, [])
        counts = {unit_id: assigned.count(unit_id) for unit_id in set(assigned)}
        # Spread the first hit across intact sustain units before assigning a
        # second hit to any of them.
        for unit in sorted(self.combat_units(session, faction),
                           key=lambda item: (item.damaged,
                                             counts.get(item.unit_id, 0), item.unit_id)):
            if unit.kind != kind or (unit_id is not None and unit.unit_id != unit_id):
                continue
            bonus = 2 if self.can_sustain(session.target, unit) and not unit.damaged and \
                self.has_tech(faction, 'nes') else 1 if self.can_sustain(session.target, unit) and \
                not unit.damaged else 0
            max_hits = 1 + bonus
            if counts.get(unit.unit_id, 0) < max_hits:
                return unit
        return None

    def assign_combat_hit(self, faction, kind, unit_id=None):
        session = self.session
        if not session or session.stage not in ('space_combat', 'ground_combat') or not session.combat_needs_resolution:
            raise MovementError('Roll combat dice before assigning hits')
        hits = session.combat_hits.get(faction, 0)
        assignments = session.combat_assignments.setdefault(faction, [])
        required = min(hits, self.combat_hit_capacity(session, faction))
        if len(assignments) >= required:
            raise MovementError('All hits against this fleet have been assigned')
        unit = self.combat_assignment_target(session, faction, kind, unit_id)
        if unit is None:
            raise MovementError('No eligible unit of this type can take another hit')
        assignments.append(unit.unit_id)
        if (self.has_tech(faction, 'nes') and self.can_sustain(session.target, unit) and
                not unit.damaged and assignments.count(unit.unit_id) == 1 and len(assignments) < required):
            assignments.append(unit.unit_id)

    def combat_assignments_complete(self, session):
        return all(len(session.combat_assignments.get(faction, [])) >=
                   min(session.combat_hits.get(faction, 0), self.combat_hit_capacity(session, faction))
                   for faction in session.combat_factions)

    def supercharge_available(self, faction):
        """Whether Supercharge can be exhausted before the next combat roll."""
        session = self.session
        player = self.faction_player(faction)
        return bool(session and session.stage in ('space_combat', 'ground_combat') and
                    not session.combat_needs_resolution and faction in session.combat_factions and
                    self.combat_units(session, faction) and player and
                    'sc' in player.technologies and 'sc' not in player.exhausted_technologies)

    def combat_agent_die_options(self, faction):
        """Return legal Letnev/Sol agent targets before a combat roll."""
        session = self.session
        player = self.faction_player(faction)
        if (not session or session.stage not in ('space_combat', 'ground_combat') or
                session.combat_needs_resolution or faction not in session.combat_factions or
                player is None):
            return ()
        if session.combat_type == 'space' and faction == 'letnev' and \
                leader_available(player, 'letnevagent'):
            return tuple((unit, 'letnevagent') for unit in self.combat_units(session, faction))
        if session.combat_type == 'ground' and faction == 'sol' and \
                leader_available(player, 'solagent'):
            return tuple((unit, 'solagent') for unit in self.combat_units(session, faction)
                         if unit.kind in self.GROUND_FORCES)
        return ()

    def use_combat_agent_die(self, faction, unit_id):
        """Exhaust a combat agent and add its die to the selected unit's next roll."""
        options = self.combat_agent_die_options(faction)
        target = next(((unit, alias) for unit, alias in options
                       if unit.unit_id == unit_id), None)
        if target is None:
            raise MovementError('No combat agent can add a die to that unit now')
        unit, alias = target
        player = self.faction_player(faction)
        if not leader_available(player, alias):
            raise MovementError('That combat agent is already exhausted')
        from leaders import exhaust_leader
        exhaust_leader(player, alias)
        session = self.session
        session.combat_bonus_dice[unit.unit_id] = session.combat_bonus_dice.get(unit.unit_id, 0) + 1
        session.combat_bonus_sources[unit.unit_id] = alias

    def use_supercharge(self, faction):
        """Apply +1 to this faction's combat rolls in the upcoming round."""
        if not self.supercharge_available(faction):
            raise MovementError('Supercharge is unavailable at this combat timing window.')
        player = self.faction_player(faction)
        player.exhausted_technologies.add('sc')
        session = self.session
        session.combat_modifiers[faction] = session.combat_modifiers.get(faction, 0) + 1

    def advance_combat(self):
        session = self.session
        if not session or session.stage not in ('space_combat', 'ground_combat', 'combat_end',
                                                'space_combat_won'):
            raise MovementError('Combat is not active')
        if self.action_cards and self.action_cards.pending:
            raise MovementError('Finish the action-card choice first.')
        if session.fire_team_pending:
            raise MovementError('Finish Fire Team rerolls first.')
        if session.stage == 'space_combat_won':
            self.continue_after_salvage(session)
            return
        if session.stage == 'combat_end':
            self._finish_combat_round(session)
            return
        if session.combat_needs_resolution:
            if not self.combat_assignments_complete(session):
                raise MovementError('Assign all available hits before continuing')
            sustained_this_round = set(session.combat_sustained_this_round)
            newly_sustained = set()
            absorbed_extra = set()
            for faction in session.combat_factions:
                for unit_id in session.combat_assignments.get(faction, []):
                    unit = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
                    if unit is None:
                        continue
                    if unit_id in absorbed_extra:
                        absorbed_extra.remove(unit_id)
                        continue
                    if self.can_sustain(session.target, unit) and not unit.damaged:
                        self.sustain_damage(unit)
                        sustained_this_round.add(unit_id)
                        newly_sustained.add(unit_id)
                        for source in session.hit_sources.get(faction, ()):
                            session.pending_direct_hits.setdefault(source, []).append(unit_id)
                        if self.has_tech(faction, 'nes'):
                            absorbed_extra.add(unit_id)
                        continue
                    self.destroy_unit(session.target, unit, session,
                                      session.hit_sources.get(faction, ()))
            session.combat_sustained_this_round = sustained_this_round
            if session.combat_type == 'ground' and not session.sardakk_retaliation_resolving:
                retaliations = sum(unit.unit_id in newly_sustained and unit.owner == 'sardakk' and
                                   unit.kind == 'mech' for unit in session.target.units)
                opponents = [faction for faction in session.combat_factions if faction != 'sardakk' and
                             self.combat_units(session, faction)]
                if retaliations and opponents:
                    session.sardakk_retaliation_resolving = True
                    session.combat_hits = {faction: retaliations if faction in opponents else 0
                                           for faction in session.combat_factions}
                    session.hit_sources = {faction: ('sardakk',) for faction in opponents}
                    session.combat_assignments = {faction: [] for faction in session.combat_factions}
                    session.assault_log.append(f'Sardakk mech sustain produced {retaliations} hit(s).')
                    return
            for faction in session.combat_factions:
                if self.has_tech(faction, 'da'):
                    damaged = [unit for unit in self.combat_units(session, faction)
                               if unit.damaged and unit.unit_id not in sustained_this_round]
                    if damaged:
                        damaged[0].damaged = False
            session.combat_needs_resolution = False
            session.stage = 'combat_end'
            if self.action_cards and any(
                    self.action_cards.can_play(player.faction, alias, session)
                    for player in self.players for alias in player.action_cards
                    if player.faction in session.combat_factions):
                return
            self._finish_combat_round(session)
            return

        session.combat_round += 1
        session.combat_sustained_this_round.clear()
        session.sardakk_retaliation_resolving = False
        session.combat_rolls = {}
        session.combat_hits = {}
        session.combat_assignments = {faction: [] for faction in session.combat_factions}
        session.reroll_selected.clear()
        outgoing_hits = {}
        for faction in session.combat_factions:
            rolls = []
            active_units = ([] if session.combat_type == 'ground' and session.combat_round == 1 and
                            faction == session.magen_suppressed else self.combat_units(session, faction))
            for unit in active_units:
                profile = unit_profile(unit)
                base_dice = int(profile.get('combatDieCount') or 1)
                bonus_dice = session.combat_bonus_dice.get(unit.unit_id, 0)
                dice = base_dice + bonus_dice
                if (unit.owner == 'naaz' and unit.kind == 'mech' and
                        any(ship.owner == 'naaz' and ship.kind == 'flagship' and
                            ship.location.region == Region.SPACE for ship in session.target.units)):
                    dice += 1
                for die_index in range(dice):
                    value = self.roll_d10(session)
                    modifier = session.combat_modifiers.get(faction, 0)
                    if unit.kind == 'fighter':
                        modifier += session.fighter_combat_modifiers.get(faction, 0)
                    if unit.owner == 'mahact' and unit.kind == 'flagship':
                        player = self.faction_player('mahact')
                        if player and any(opponent != faction and
                                          opponent not in player.foreign_fleet_tokens
                                          for opponent in session.combat_factions):
                            modifier += 2
                    if faction == 'winnu':
                        modifier += self.winnu_commander_bonus(session.target)
                    if (unit.owner == 'naalu' and unit.kind == 'mech' and
                            self.exploration and any(opponent != faction and
                                self.exploration.fragments.get(opponent)
                                for opponent in session.combat_factions)):
                        modifier += 2
                    result = value + modifier
                    threshold = self.combat_threshold(faction, profile)
                    if (session.combat_type == 'ground' and faction == 'jolnar' and
                            unit.kind == 'infantry' and any(
                                other.owner == 'jolnar' and other.kind == 'mech' and
                                other.location.region == Region.PLANET and
                                other.location.planet_id == session.combat_planet_id
                                for other in session.target.units)):
                        threshold -= 1
                    rolls.append({'unit_id': unit.unit_id, 'kind': unit.kind, 'value': result,
                                  'natural': value, 'modifier': modifier, 'threshold': threshold,
                                  'hit': result >= threshold,
                                  'rerolled': False,
                                  'bonus_source': (session.combat_bonus_sources.get(unit.unit_id)
                                                   if die_index >= dice - bonus_dice else None)})
            session.combat_rolls[faction] = rolls
            outgoing_hits[faction] = sum(result['hit'] for result in rolls) * (
                2 if session.combat_type == 'ground' and self.has_tech(faction, 'x89c4') else 1)
        session.munitions_available = set(session.munitions_ready)
        session.munitions_ready.clear()
        session.combat_hits = {faction: sum(hits for shooter, hits in outgoing_hits.items()
                                             if shooter != faction)
                               for faction in session.combat_factions}
        session.hit_sources = {faction: tuple(shooter for shooter, hits in outgoing_hits.items()
                                              if shooter != faction and hits)
                               for faction in session.combat_factions}
        session.combat_needs_resolution = True
        session.combat_bonus_dice.clear()
        session.combat_bonus_sources.clear()

    def _finish_combat_round(self, session):
        session.combat_sustained_this_round.clear()
        session.sardakk_retaliation_resolving = False
        session.pending_direct_hits.clear()
        session.pending_courageous.clear()
        if session.combat_type == 'space' and session.combat_round > 0:
            yin = self.faction_player('yin')
            round_key = f'yin:{session.combat_round}'
            yin_ships = self.combat_units(session, 'yin') if yin and 'yin' in session.combat_factions else []
            opponents = [unit for faction in session.combat_factions if faction != 'yin'
                         for unit in self.combat_units(session, faction)]
            sacrifices = sorted((unit for unit in yin_ships if unit.kind in ('cruiser', 'destroyer')),
                                key=lambda unit: (unit.kind == 'cruiser', unit.unit_id))
            if sacrifices and opponents and round_key not in session.yin_devotion_used:
                session.yin_devotion_used.add(round_key)
                sacrifice = sacrifices[0]
                self.destroy_unit(session.target, sacrifice, session)
                from leaders import notify_leader_event
                notify_leader_event(yin, 'used_faction_ability')
                victim = self.apply_hit(session.target, opponents, session, ('yin',))
                outcome = 'damaged' if victim.damaged else 'destroyed'
                session.assault_log.append(
                    f'Yin Devotion sacrificed a {sacrifice.kind} to {outcome} '
                    f'{victim.owner.upper()} {victim.kind}.')
        own_alive = bool(self.combat_units(session, session.player.faction))
        enemies_alive = any(self.combat_units(session, faction)
                            for faction in session.combat_factions if faction != session.player.faction)
        if session.combat_type == 'ground':
            self.resolve_harrow(session)
            own_alive = bool(self.combat_units(session, session.player.faction))
            enemies_alive = any(self.combat_units(session, faction)
                                for faction in session.combat_factions if faction != session.player.faction)
        if session.combat_type == 'ground' and (not own_alive or not enemies_alive):
            self.finish_ground_battle(session, own_alive, enemies_alive)
        elif not own_alive or not enemies_alive:
            winner = next((faction for faction in session.combat_factions
                           if self.combat_units(session, faction)), None)
            session.combat_winner = winner
            self._reset_naaz_space_mechs(session)
            if winner == 'mahact':
                from pok_factions import mahact_gain_fleet_token
                mahact = self.faction_player('mahact')
                for faction in session.combat_factions:
                    defeated = self.faction_player(faction)
                    if faction != winner and defeated is not None:
                        mahact_gain_fleet_token(mahact, defeated, self.board)
            session.space_combat_resolved = True
            if winner and self.action_cards and any(
                    self.action_cards.can_play(winner, alias, session)
                    for player in self.players if player.faction == winner
                    for alias in player.action_cards):
                session.stage = 'space_combat_won'
                return
            self._check_capacity_and_fleet(
                session, [(session.target.position, faction) for faction in session.combat_factions],
                'after_combat')
        elif session.combat_type == 'space' and session.retreat_announced:
            session.stage = 'retreat_selection'
        else:
            session.stage = 'space_combat' if session.combat_type == 'space' else 'ground_combat'
            session.combat_rolls.clear()
            session.combat_hits.clear()
            session.combat_assignments = {faction: [] for faction in session.combat_factions}
            session.combat_modifiers.clear()
            session.fighter_combat_modifiers.clear()

    def resolve_harrow(self, session):
        if not any(unit.owner == 'l1z1x' and unit.location.region == Region.SPACE and
                   capital_ship(unit) for unit in session.target.units):
            return
        planet_id = session.combat_planet_id
        defenders = [unit for unit in session.target.units if unit.owner != 'l1z1x' and
                     self.ground_combatant(unit) and unit.location.region == Region.PLANET and
                     unit.location.planet_id == planet_id]
        if not defenders or any(unit.owner != 'l1z1x' and unit.location.region == Region.PLANET and
                                unit.location.planet_id == planet_id and
                                unit_profile(unit).get('planetaryShield')
                                for unit in session.target.units):
            return
        ships = [unit for unit in session.target.units if unit.owner == 'l1z1x' and
                 unit.location.region == Region.SPACE and capital_ship(unit) and
                 unit_profile(unit).get('bombardHitsOn')]
        hits = 0
        for ship in ships:
            profile = unit_profile(ship)
            threshold = int(profile['bombardHitsOn'])
            dice = int(profile.get('bombardDieCount') or 1)
            hits += sum(self.roll_d10(session) >= threshold for _ in range(dice))
        for _ in range(hits):
            remaining = [unit for unit in session.target.units if unit.owner != 'l1z1x' and
                         self.ground_combatant(unit) and unit.location.region == Region.PLANET and
                         unit.location.planet_id == planet_id]
            if not remaining:
                break
            self.apply_hit(session.target, remaining, session)

    def continue_after_salvage(self, session):
        if not session or session.stage != 'space_combat_won':
            raise MovementError('Space combat has not been won.')
        session.space_combat_resolved = True
        self._check_capacity_and_fleet(
            session, [(session.target.position, faction) for faction in session.combat_factions],
            'after_combat')

    def production_sites(self, session):
        sites = []
        for unit in session.target.units:
            if unit.owner != session.player.faction:
                continue
            value = unit_profile(unit).get('productionValue')
            if value is None:
                continue
            if (unit.kind == 'spacedock' and unit.owner == 'saar' and
                    unit.location.region == Region.SPACE):
                sites.append((None, int(value)))
                continue
            if unit.location.region != Region.PLANET:
                continue
            planet_id = unit.location.planet_id
            planet = next((planet for planet in session.target.planets if planet.planet_id == planet_id), None)
            if not planet or session.target.planet_owners.get(planet_id) != session.player.faction:
                continue
            if self.exploration and (self.exploration.planet_is_blocked(planet_id) or
                                     planet_id in self.exploration.destroyed_planets):
                continue
            if unit.kind == 'spacedock':
                value_text = str(value)
                production = self.planet_resources(planet) + int(value_text[1:]) if value_text.startswith('+') else int(value)
            else:
                production = int(value)
            if production > 0:
                sites.append((planet_id, production))
        return sites

    def ships_blocked_by_opponents(self, session):
        """TI4 rule 58.6: enemy ships in the system prevent producing ships."""
        return any(unit.owner != session.player.faction and
                   unit.location.region == Region.SPACE and
                   UNIT_TYPES[unit.kind]['ship']
                   for unit in session.target.units)

    def unit_cost(self, kind, player):
        costs = {'infantry': .5, 'mech': 2, 'fighter': .5, 'destroyer': 1, 'cruiser': 2,
                 'carrier': 3, 'dreadnought': 4, 'pds': 2,
                 'spacedock': 4, 'flagship': 8, 'warsun': 12}
        definitions, factions = unit_profiles()
        faction = factions.get(player.faction, {})
        for profile_id in faction.get('units', []):
            profile = definitions.get(profile_id, {})
            if profile.get('baseType') == kind and profile.get('cost') is not None:
                return float(profile['cost'])
        profile = definitions.get(kind, {})
        return float(profile['cost']) if profile.get('cost') is not None else costs[kind]

    def prepare_production(self, session):
        sites = self.production_sites(session)
        if not sites:
            session.stage = 'complete'
            self.finish()
            return False
        session.production_sites = sites
        session.production_limit = sum(value for _, value in sites)
        session.production_checkpoint = SessionCheckpoint.capture(self, session, 'production')
        session.production_choices.clear()
        session.production_planets.clear()
        session.trade_goods_to_spend = 0
        session.self_assembly_planet = None
        session.aida_production_discount = False
        session.amalgamation_choices.clear()
        session.stage = 'production'
        return True

    def advance_integrated_production(self, session):
        """Resolve each newly captured planet before normal tactical production."""
        while session.integrated_queue:
            planet_id = session.integrated_queue.pop(0)
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == planet_id)
            if not self.planet_resources(planet):
                continue
            session.integrated_current = planet_id
            session.production_sites = [(planet_id, 9999)]
            session.production_limit = 9999
            session.production_choices.clear()
            session.production_planets.clear()
            session.trade_goods_to_spend = 0
            session.self_assembly_planet = None
            session.aida_production_discount = False
            session.amalgamation_choices.clear()
            session.stage = 'production'
            session.production_checkpoint = SessionCheckpoint.capture(self, session, 'production')
            return True
        session.integrated_current = None
        return self.prepare_production(session)

    def start_strategy_production(self, player, target, dock):
        if self.session:
            raise MovementError('Finish the current action first.')
        session = Session(player, target, {}, Snapshot.capture(self.board, player, self.players,
                                                               self.action_cards, self.exploration),
                          strategic_production=True)
        planet = next((p for p in target.planets if p.planet_id == dock.location.planet_id), None)
        value = str(unit_profile(dock).get('productionValue', '+2'))
        if dock.location.region == Region.SPACE and dock.kind == 'spacedock' and player.faction == 'saar':
            limit = int(value)
        elif planet is not None:
            limit = self.planet_resources(planet) + int(value[1:]) if value.startswith('+') else int(value)
        else:
            raise MovementError('Choose a production unit on a controlled planet.')
        session.production_sites = [(planet.planet_id if planet else None, limit)]
        session.production_limit = limit
        session.stage = 'production'
        session.production_checkpoint = SessionCheckpoint.capture(self, session, 'production')
        self.session = session
        return session

    def production_total(self, session):
        total = sum(session.production_choices.values())
        if (session.player.faction == 'cabal' and
                leader_available(session.player, 'cabalcommander')):
            total -= min(2, session.production_choices.get('fighter', 0) +
                         session.production_choices.get('infantry', 0))
        return total

    def production_cost(self, session):
        raw_cost = ceil(sum(self.unit_cost(kind, session.player) *
                            (count - session.amalgamation_choices.get(kind, 0))
                            for kind, count in session.production_choices.items()))
        if (session.player.faction == 'nomad' and
                leader_available(session.player, 'nomadcommander')):
            raw_cost = max(0, raw_cost - self.unit_cost('flagship', session.player) *
                           min(1, session.production_choices.get('flagship', 0)))
        if session.integrated_current:
            return raw_cost
        sarween = int('st' in session.player.technologies and bool(session.production_choices))
        aida = (sum(is_unit_upgrade(technology_catalog()[alias])
                    for alias in session.player.technologies if alias in technology_catalog())
                if session.aida_production_discount else 0)
        return max(0, raw_cost - sarween - getattr(session, 'war_machine_discount', 0) - aida)

    def toggle_ai_development_discount(self):
        """Choose whether to exhaust AI Development Algorithm for this production."""
        session = self.session
        if not session or session.stage != 'production' or session.integrated_current:
            raise MovementError('AI Development Algorithm needs unit production.')
        if ('aida' not in session.player.technologies or
                'aida' in session.player.exhausted_technologies or
                not any(is_unit_upgrade(technology_catalog()[alias])
                        for alias in session.player.technologies if alias in technology_catalog())):
            raise MovementError('AI Development Algorithm cannot reduce this production cost.')
        session.aida_production_discount = not session.aida_production_discount
        return session.aida_production_discount

    def self_assembly_planets(self):
        """Legal destinations for the free Self-Assembly Routines mech."""
        session = self.session
        if (not session or session.stage != 'production' or session.integrated_current or
                'sar' not in session.player.technologies or
                'sar' in session.player.exhausted_technologies):
            return ()
        deployed = sum(unit.owner == session.player.faction and unit.kind == 'mech'
                       for tile in self.board.values() for unit in tile.units)
        if deployed + session.production_choices.get('mech', 0) >= 4:
            return ()
        return tuple(planet.planet_id for planet in session.target.planets
                     if session.target.planet_owners.get(planet.planet_id) == session.player.faction
                     and (self.exploration is None or not (
                         self.exploration.planet_is_blocked(planet.planet_id) or
                         planet.planet_id in self.exploration.destroyed_planets)))

    def choose_self_assembly_planet(self, planet_id=None):
        """Opt into the post-PRODUCTION mech, or decline with ``None``."""
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Self-Assembly Routines is only available during production.')
        if planet_id is not None and planet_id not in self.self_assembly_planets():
            raise MovementError('Choose a controlled planet with a mech in your reinforcements.')
        session.self_assembly_planet = planet_id
        return planet_id

    def adjust_amalgamation(self, kind, delta):
        """Spend a captured unit of the same type instead of its production cost."""
        session = self.session
        if not session or session.stage != 'production' or session.player.faction != 'cabal':
            raise MovementError('Amalgamation requires Cabal production.')
        if kind not in self.PRODUCIBLE_KINDS or delta not in (-1, 1):
            raise MovementError('Select one produced unit for Amalgamation.')
        current = session.amalgamation_choices.get(kind, 0)
        maximum = min(session.production_choices.get(kind, 0),
                      session.player.captured_units.get(kind, 0))
        updated = max(0, min(maximum, current + delta))
        if updated:
            session.amalgamation_choices[kind] = updated
        else:
            session.amalgamation_choices.pop(kind, None)
        return updated

    def production_payment(self, session):
        card_resources = sum(self.planet_resources(card.planet) for card in session.player.planets
                             if card.planet.planet_id in session.production_planets)
        return card_resources + session.trade_goods_to_spend

    def adjust_production(self, kind, delta):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        if kind not in self.PRODUCIBLE_KINDS:
            raise MovementError('Structures cannot be produced yet')
        if kind == 'mech' and delta > 0:
            deployed = sum(unit.owner == session.player.faction and unit.kind == 'mech'
                           for tile in self.board.values() for unit in tile.units)
            if deployed + session.production_choices.get('mech', 0) >= 4:
                raise MovementError('No mechs remain in your reinforcements.')
        if getattr(session, 'sling_relay', False) and not UNIT_TYPES[kind]['ship']:
            raise MovementError('Sling Relay may produce only a ship.')
        if not production_allowed(session.player, kind):
            raise MovementError('Research War Sun before producing it.')
        if (delta > 0 and UNIT_TYPES[kind]['ship'] and
                self.ships_blocked_by_opponents(session)):
            raise MovementError('Cannot produce ships while another player has ships in this system.')
        current = session.production_choices.get(kind, 0)
        if delta > 0:
            remaining = session.production_limit - self.production_total(session)
            if (kind in ('infantry', 'fighter') and session.player.faction == 'cabal' and
                    leader_available(session.player, 'cabalcommander')):
                remaining += max(0, 2 - session.production_choices.get('fighter', 0) -
                                 session.production_choices.get('infantry', 0))
            addition = min(2, remaining) if kind in ('infantry', 'fighter') else 1
            if session.player.faction == 'arborec' and kind == 'infantry':
                letani_capacity = sum(unit.owner == 'arborec' and unit.kind == 'infantry' and
                                      unit.location.region == Region.PLANET and
                                      unit_profile(unit).get('productionValue')
                                      for unit in session.target.units)
                addition = min(addition, max(0, letani_capacity - current))
            if addition > remaining:
                return
            if addition <= 0:
                return
            session.production_choices[kind] = current + addition
        elif current:
            updated = current - 1
            if updated:
                session.production_choices[kind] = updated
            else:
                session.production_choices.pop(kind, None)
            if session.amalgamation_choices.get(kind, 0) > updated:
                session.amalgamation_choices[kind] = updated
            if not session.amalgamation_choices.get(kind, 0):
                session.amalgamation_choices.pop(kind, None)

    def change_production_trade_goods(self, delta):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        session.trade_goods_to_spend = max(0, min(session.player.trade_goods,
                                                   session.trade_goods_to_spend + delta))

    def toggle_production_planet(self, planet_id):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        card = next((card for card in session.player.planets
                     if card.planet.planet_id == planet_id), None)
        if not card:
            raise MovementError('This planet cannot pay for production')
        if planet_id in session.production_planets:
            session.production_planets.remove(planet_id)
            card.exhausted = False
        elif not card.exhausted:
            session.production_planets.add(planet_id)
            card.exhausted = True

    def produce(self):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        if not session.production_choices:
            raise MovementError('Choose at least one unit or skip production')
        if self.production_total(session) > session.production_limit:
            raise MovementError('Production limit exceeded')
        if (self.ships_blocked_by_opponents(session) and
                any(count and UNIT_TYPES[kind]['ship']
                    for kind, count in session.production_choices.items())):
            raise MovementError('Cannot produce ships while another player has ships in this system.')
        if session.integrated_current:
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == session.integrated_current)
            if self.production_cost(session) > self.planet_resources(planet):
                raise MovementError('Integrated Economy exceeds this planet’s resource value.')
        if self.production_payment(session) < self.production_cost(session):
            raise MovementError('Not enough exhausted planet resources and trade goods')
        if any(count > min(session.production_choices.get(kind, 0),
                           session.player.captured_units.get(kind, 0))
               for kind, count in session.amalgamation_choices.items()):
            raise MovementError('A captured unit is unavailable for Amalgamation.')
        if session.self_assembly_planet and session.self_assembly_planet not in self.self_assembly_planets():
            raise MovementError('No Self-Assembly Routines mech can be placed on that planet.')
        for card in session.player.planets:
            if card.planet.planet_id in session.production_planets:
                card.exhausted = True
        session.player.trade_goods -= session.trade_goods_to_spend
        for kind, count in session.amalgamation_choices.items():
            session.player.captured_units[kind] -= count
            if not session.player.captured_units[kind]:
                del session.player.captured_units[kind]
        if session.aida_production_discount:
            session.player.exhausted_technologies.add('aida')
        used_ids = {unit.unit_id for tile in self.board.values() for unit in tile.units}
        next_id = 1
        for kind, count in session.production_choices.items():
            for _ in range(count):
                while f'{session.player.faction}-built-{next_id}' in used_ids:
                    next_id += 1
                unit_id = f'{session.player.faction}-built-{next_id}'
                used_ids.add(unit_id)
                next_id += 1
                if kind == 'spacedock' and session.player.faction == 'saar':
                    location = UnitLocation(Region.SPACE)
                elif kind in ('infantry', 'mech', 'pds', 'spacedock'):
                    site_planet = session.production_sites[0][0]
                    owned_planets = [planet.planet_id for planet in session.target.planets
                                     if session.target.planet_owners.get(planet.planet_id) == session.player.faction
                                     and (self.exploration is None or not (
                                         self.exploration.planet_is_blocked(planet.planet_id) or
                                         planet.planet_id in self.exploration.destroyed_planets))]
                    planet_id = site_planet if site_planet in owned_planets else next(iter(owned_planets), None)
                    if planet_id is not None:
                        location = UnitLocation(Region.PLANET, planet_id=planet_id)
                    elif kind in self.GROUND_FORCES and session.player.faction == 'saar':
                        factory = next((unit for unit in session.target.units
                                        if unit.owner == 'saar' and unit.kind == 'spacedock' and
                                        unit.location.region == Region.SPACE and
                                        unit_profile(unit).get('productionValue') is not None), None)
                        if factory is None:
                            raise MovementError('Ground forces produced in space need a Saar Floating Factory.')
                        # Ground forces in a system's space area count against capacity.
                        # Keep them attached to the mobile factory so movement and
                        # invasion can carry them through the existing cargo flow.
                        location = UnitLocation(Region.TRANSPORT, carrier_id=factory.unit_id)
                    else:
                        raise MovementError('Ground units and structures need a controlled planet in this system.')
                else:
                    location = UnitLocation(Region.SPACE)
                session.target.units.append(Unit(unit_id, kind, session.player.faction,
                                                 session.player.color_code, location,
                                                 profile_id=(upgrade_profile(session.player, kind) or {}).get('id')))
                if kind == 'warsun' and session.player.faction == 'muaat':
                    from leaders import notify_leader_event
                    notify_leader_event(session.player, 'produced_war_sun')
        if session.self_assembly_planet:
            session.target.units.append(Unit(f'{session.player.faction}-self-assembly-{uuid4().hex}',
                                             'mech', session.player.faction,
                                             session.player.color_code,
                                             UnitLocation(Region.PLANET, session.self_assembly_planet)))
            session.player.exhausted_technologies.add('sar')
        if (session.player.faction == 'titans' and not session.integrated_current and
                not getattr(session, 'sling_relay', False) and
                leader_available(session.player, 'titanscommander')):
            session.player.trade_goods += 1
        session.stage = 'complete'
        next_stage = 'integrated_continue' if session.integrated_current else 'complete'
        self._check_capacity_and_fleet(
            session, [(session.target.position, session.player.faction)], next_stage)

    def skip_production(self):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        for card in session.player.planets:
            if card.planet.planet_id in session.production_planets:
                card.exhausted = False
        session.stage = 'complete'
        if session.integrated_current:
            self.advance_integrated_production(session)
        else:
            self.finish()

    def landing_forces(self, session):
        return [unit for unit in session.target.units if unit.owner == session.player.faction and
                unit.kind in self.GROUND_FORCES and unit.location.region in
                (Region.TRANSPORT, Region.SPACE, Region.SPACE_AREA)]

    def cycle_landing(self, unit_id):
        session = self.session
        if not session or session.stage != 'invasion' or unit_id not in session.landings:
            raise MovementError('This ground force cannot be landed')
        planets = [planet for planet in session.target.planets
                   if self.exploration is None or not (
                       self.exploration.planet_is_blocked(planet.planet_id) or
                       planet.planet_id in self.exploration.destroyed_planets)]
        choices = [None, *(planet.planet_id for planet in planets)]
        current = session.landings[unit_id]
        session.landings[unit_id] = choices[(choices.index(current) + 1) % len(choices)]

    def establish_control(self):
        session = self.session
        if not session or session.stage != 'invasion':
            raise MovementError('Invasion is not active')
        session.invasion_checkpoint = SessionCheckpoint.capture(self, session, 'invasion')
        target, player = session.target, session.player
        mecatol_landings = [unit for unit in self.landing_forces(session)
                            if session.landings.get(unit.unit_id) == 'mr']
        if target.custodians_present and mecatol_landings:
            if self.pay_custodians(session):
                target.custodians_present = False
            else:
                for unit in mecatol_landings:
                    session.landings[unit.unit_id] = None
                session.custodian_payment_failed = True
                session.custodian_payment_log.append(
                    'Ground forces assigned to Mecatol Rex remained aboard their ships.')
        landed_planets = []
        for unit in self.landing_forces(session):
            planet_id = session.landings.get(unit.unit_id)
            if planet_id is None:
                continue
            unit.location = UnitLocation(Region.PLANET, planet_id=planet_id)
            if planet_id not in landed_planets:
                landed_planets.append(planet_id)
        session.landed_planets = landed_planets
        session.captured_planets = []
        session.ground_planets = []
        session.ground_planet_index = 0
        self.resolve_planetary_cannon_defense(session)
        for planet_id in landed_planets:
            attackers = [unit for unit in target.units if unit.owner == player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                         unit.kind in self.GROUND_FORCES]
            if not attackers:
                continue
            defenders = [unit for unit in target.units if unit.owner != player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                         self.ground_combatant(unit)]
            if not defenders:
                self.capture_planet(session, planet_id)
            else:
                session.ground_planets.append(planet_id)
        if not landed_planets:
            session.outcome = 'No ground forces landed. No planets changed control.'
            if session.custodian_payment_log:
                session.outcome += ' ' + ' '.join(session.custodian_payment_log)
            self.prepare_production(session)
        elif session.ground_planets:
            self.start_ground_combat(session, session.ground_planets[0])
        else:
            self.finish_invasion(session)

    def resolve_planetary_cannon_defense(self, session):
        session.defense_log.clear()
        session.defense_checked = True
        faction = session.player.faction
        if 'l4' in session.player.technologies:
            session.defense_log.append('L4 Disruptors prevent space cannon defense during this invasion.')
            return
        plasma_used = set()
        nes_cancel = {}
        argent = self.faction_player('argent')
        argent_pds = [(int(unit_profile(unit)['spaceCannonHitsOn']), unit.unit_id)
                      for planet_id in session.landed_planets
                      for unit in session.target.units
                      if unit.owner == 'argent' and unit.kind == 'pds' and
                      unit.location.region == Region.PLANET and
                      unit.location.planet_id == planet_id and
                      unit.unit_id not in session.disabled_pds and
                      unit_profile(unit).get('spaceCannonHitsOn')]
        argent_cannon = (min(argent_pds)[1] if argent_pds and argent and
                         leader_available(argent, 'argentcommander') else None)
        for planet_id in session.landed_planets:
            cannons = [unit for unit in session.target.units if unit.owner != faction and unit.kind == 'pds' and
                       unit.unit_id not in session.disabled_pds and
                       unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                       unit_profile(unit).get('spaceCannonHitsOn')]
            if not cannons:
                continue
            for cannon in cannons:
                profile = unit_profile(cannon)
                shooter = self.faction_player(cannon.owner)
                plasma_bonus = bool(shooter and 'ps' in shooter.technologies and
                                    cannon.owner not in plasma_used)
                extra = int(plasma_bonus)
                if plasma_bonus:
                    plasma_used.add(cannon.owner)
                argent_bonus = cannon.unit_id == argent_cannon
                extra += int(argent_bonus)
                dice = [self.roll_d10(session)
                        for _ in range(int(profile.get('spaceCannonDieCount') or 1) + extra)]
                penalty = -1 if 'amd' in session.player.technologies else 0
                hits = sum(value + penalty >= int(profile['spaceCannonHitsOn']) for value in dice)
                results = ', '.join(format_die_result(
                    value, penalty, value + penalty >= int(profile['spaceCannonHitsOn']))
                    for value in dice)
                session.defense_log.append(
                    f'{cannon.owner.upper()} PDS on {planet_id} '
                    f'({profile["spaceCannonHitsOn"]}+): {results} → {hits} hit(s)' +
                    (' · Plasma Scoring +1 die' if plasma_bonus else '') +
                    (' · Argent commander +1 die' if argent_bonus else ''))
                for _ in range(hits):
                    if nes_cancel.get(planet_id, 0):
                        nes_cancel[planet_id] -= 1
                        session.defense_log.append('Non-Euclidean Shielding canceled 1 additional hit.')
                        continue
                    attackers = [unit for unit in session.target.units if unit.owner == faction and
                                 unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                                 unit.kind in self.GROUND_FORCES]
                    if not attackers:
                        break
                    target = self.apply_hit(session.target, attackers, session)
                    if target in session.target.units and target.damaged and \
                            self.has_tech(target.owner, 'nes'):
                        nes_cancel[planet_id] = 1
                    session.defense_log.append(
                        f'{target.kind.title()} on {planet_id} {"damaged" if target.damaged else "destroyed"}')

    def capture_planet(self, session, planet_id):
        target, player = session.target, session.player
        previous_owner = target.planet_owners.get(planet_id)
        if previous_owner == player.faction:
            return False
        if planet_id == 'mr' and target.custodians_present:
            if not self.pay_custodians(session):
                session.custodian_payment_failed = True
                return False
            target.custodians_present = False
        target.planet_owners[planet_id] = player.faction
        planet = next(planet for planet in target.planets if planet.planet_id == planet_id)
        if (self.exploration is not None and
                self.exploration.planet_is_legendary(planet)):
            if previous_owner is None:
                player.exhausted_legendary_abilities.discard(planet_id)
            elif previous_owner != player.faction:
                old_owner = self.faction_player(previous_owner)
                ability_was_exhausted = bool(
                    old_owner and planet_id in old_owner.exhausted_legendary_abilities)
                if old_owner:
                    old_owner.exhausted_legendary_abilities.discard(planet_id)
                if ability_was_exhausted:
                    player.exhausted_legendary_abilities.add(planet_id)
                else:
                    player.exhausted_legendary_abilities.discard(planet_id)
        if (previous_owner and self.exploration is not None and
                (planet.faction_homeworld or self.exploration.planet_is_legendary(planet))):
            self.exploration.relics.transfer_shard(previous_owner, player.faction)
        for unit in list(target.units):
            if unit.owner != player.faction and unit.location.region == Region.PLANET and \
                    unit.location.planet_id == planet_id and unit.kind in ('pds', 'spacedock'):
                target.units.remove(unit)
                if player.faction == 'l1z1x':
                    target.units.append(Unit(f'l1z1x-assimilated-{uuid4().hex}', unit.kind,
                                             player.faction, player.color_code,
                                             UnitLocation(Region.PLANET, planet_id),
                                             profile_id=(upgrade_profile(player, unit.kind) or {}).get('id')))
        for other in self.players:
            other.planets[:] = [card for card in other.planets if card.planet.planet_id != planet_id]
        player.planets.append(PlanetCard(planet, exhausted=True))
        if player.faction == 'saar':
            player.trade_goods += 1
            self.resolve_pillage(player)
        if planet_id not in session.captured_planets:
            session.captured_planets.append(planet_id)
        if previous_owner is None and self.exploration is not None:
            self.exploration_queue.append((player, target, planet_id))
            self.resolve_exploration_queue()
        from leaders import refresh_leader_unlocks
        for participant in self.players:
            refresh_leader_unlocks(participant, self.board, self.players)
        return True

    def resolve_exploration_queue(self):
        """Reveal newly claimed neutral planets in capture order."""
        if self.exploration is None:
            return
        while (self.exploration_queue and self.exploration.pending is None and
               self.exploration.freelancer_offer is None and
               self.exploration.terragenesis_pending is None and
               not self.exploration.secret_objectives.pending_returns):
            player, tile, planet_id = self.exploration_queue.pop(0)
            if planet_id is None:
                outcome = self.exploration.explore_frontier(
                    player, tile, auto_resolve=self.exploration_is_bot(player))
            elif player.faction == 'naaz':
                from pok_factions import naaz_explore_planet
                outcome = naaz_explore_planet(
                    player, self.exploration, tile, planet_id,
                    auto_resolve=self.exploration_is_bot(player))
            else:
                outcome = self.exploration.explore_planet(
                    player, tile, planet_id, auto_resolve=self.exploration_is_bot(player))
            if outcome.card is not None:
                self.exploration_outcomes.append((player.faction, outcome))
            if outcome.resolved and planet_id is not None:
                self.after_planet_explored(player, tile, planet_id)

    def after_planet_explored(self, player, tile, planet_id):
        if 'pfa' in player.technologies:
            card = next((card for card in player.planets
                         if card.planet.planet_id == planet_id), None)
            if card is not None:
                card.exhausted = False
        if (player.faction == 'titans' and self.exploration is not None and
                planet_id not in tile.sleeper_tokens):
            from pok_factions import SLEEPER_TOKEN_SUPPLY, titans_place_sleeper
            from exploration import PendingTerragenesis
            sleeper_count = sum(len(system.sleeper_tokens) for system in self.board.values())
            if sleeper_count < SLEEPER_TOKEN_SUPPLY:
                titans_place_sleeper(player, self.board, tile, planet_id)
            else:
                self.exploration.terragenesis_pending = PendingTerragenesis(
                    player.faction, tile.position, planet_id)

    def terragenesis_sources(self):
        pending = self.exploration.terragenesis_pending
        if pending is None:
            return ()
        locations = {planet.planet_id: (tile, planet)
                     for tile in self.board.values() for planet in tile.planets}
        return tuple((tile, locations[planet_id][1])
                     for tile in self.board.values() for planet_id in sorted(tile.sleeper_tokens)
                     if planet_id in locations)

    def resolve_terragenesis(self, move_from_planet_id=None):
        pending = self.exploration.terragenesis_pending
        if pending is None:
            raise MovementError('Terragenesis is not waiting for a choice.')
        player = self.exploration.players[pending.faction]
        if move_from_planet_id is not None:
            tile = self.board[pending.position]
            from pok_factions import titans_place_sleeper
            titans_place_sleeper(player, self.board, tile, pending.planet_id,
                                 move_from_planet_id=move_from_planet_id)
        self.exploration.terragenesis_pending = None
        self.resolve_exploration_queue()

    def resolve_exploration_choice(self, choice):
        if self.exploration is None or self.exploration.pending is None:
            raise MovementError('No exploration choice is pending.')
        faction = self.exploration.pending.faction
        planet_id = self.exploration.pending.planet_id
        tile = self.board[self.exploration.pending.position]
        outcome = self.exploration.resolve_pending(choice)
        self.exploration_outcomes.append((faction, outcome))
        if outcome.resolved and planet_id is not None:
            self.after_planet_explored(self.exploration.players[faction], tile, planet_id)
        self.resolve_exploration_queue()
        return outcome

    def pay_custodians(self, session):
        """Pay influence to remove the Mecatol Rex Custodians token."""
        player = session.player
        if player.faction == 'winnu':
            session.custodian_payment_log.append('Winnu removed the Custodians token for free (Blood Ties).')
            return True
        ready = []
        for card in player.planets:
            if card.exhausted:
                continue
            tile = next((tile for tile in self.board.values()
                         if any(planet.planet_id == card.planet.planet_id for planet in tile.planets)), None)
            if tile and tile.planet_owners.get(card.planet.planet_id) == player.faction:
                ready.append(card)
        payment = None
        for size in range(1, len(ready) + 1):
            eligible = [choice for choice in combinations(ready, size)
                        if sum(self.planet_influence(card.planet) for card in choice) >= 6]
            if eligible:
                payment = min(eligible, key=lambda choice: (
                    sum(self.planet_influence(card.planet) for card in choice) - 6,
                    sum(self.planet_resources(card.planet) for card in choice),
                    tuple(card.planet.planet_id for card in choice)))
                break
        if payment is None:
            session.custodian_payment_log.append(
                f'{player.faction.upper()} needs 6 ready influence to remove the Custodians token.')
            return False
        for card in payment:
            card.exhausted = True
        session.custodian_payment_log.append(
            f'{player.faction.upper()} spent 6 influence to remove the Custodians token.')
        return True

    def finish_ground_battle(self, session, attackers_alive, defenders_alive):
        planet_id = session.combat_planet_id
        if attackers_alive and not defenders_alive:
            self.capture_planet(session, planet_id)
            winner = session.player.faction
        elif defenders_alive and not attackers_alive:
            winner = next((faction for faction in session.combat_factions
                           if faction != session.player.faction and
                           self.combat_units(session, faction)), None)
        else:
            winner = None
        if winner == 'mahact':
            from pok_factions import mahact_gain_fleet_token
            mahact = self.faction_player('mahact')
            for faction in session.combat_factions:
                defeated = self.faction_player(faction)
                if faction != winner and defeated is not None:
                    mahact_gain_fleet_token(mahact, defeated, self.board)
        if winner and self.has_tech(winner, 'dxa'):
            player = self.faction_player(winner)
            deployed = sum(unit.owner == winner and unit.kind == 'infantry'
                           for tile in self.board.values() for unit in tile.units)
            if deployed < 12:
                session.target.units.append(Unit(f'{winner}-dacxive-{uuid4().hex}', 'infantry',
                                                 winner, player.color_code,
                                                 UnitLocation(Region.PLANET, planet_id),
                                                 profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
        session.ground_planet_index += 1
        while session.ground_planet_index < len(session.ground_planets):
            next_planet = session.ground_planets[session.ground_planet_index]
            defenders = [unit for unit in session.target.units if unit.owner != session.player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == next_planet and
                         self.ground_combatant(unit)]
            attackers = [unit for unit in session.target.units if unit.owner == session.player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == next_planet and
                         unit.kind in self.GROUND_FORCES]
            if defenders and attackers:
                self.start_ground_combat(session, next_planet)
                return
            if attackers and not defenders:
                self.capture_planet(session, next_planet)
            session.ground_planet_index += 1
        session.stage = 'invasion'
        self.finish_invasion(session)

    def finish_invasion(self, session):
        if session.player.faction == 'winnu' and 'mr' in session.captured_planets:
            planet = next((planet for planet in session.target.planets if planet.planet_id == 'mr'), None)
            if planet:
                owned = [unit for tile in self.board.values() for unit in tile.units
                         if unit.owner == 'winnu']
                pds_supply = 6 - sum(unit.kind == 'pds' for unit in owned)
                dock_supply = 3 - sum(unit.kind == 'spacedock' for unit in owned)
                if pds_supply > 0 and not any(
                        unit.owner == 'winnu' and unit.kind == 'pds' and
                        unit.location.planet_id == 'mr' for unit in session.target.units):
                    session.target.units.append(Unit(f'winnu-reclamation-pds-{uuid4().hex}', 'pds',
                                                     'winnu', session.player.color_code,
                                                     UnitLocation(Region.PLANET, 'mr'),
                                                     profile_id=(upgrade_profile(session.player, 'pds') or {}).get('id')))
                if dock_supply > 0 and not any(
                        unit.owner == 'winnu' and unit.kind == 'spacedock' and
                        unit.location.planet_id == 'mr' for unit in session.target.units):
                    session.target.units.append(Unit(f'winnu-reclamation-dock-{uuid4().hex}', 'spacedock',
                                                     'winnu', session.player.color_code,
                                                     UnitLocation(Region.PLANET, 'mr'),
                                                     profile_id=(upgrade_profile(session.player, 'spacedock') or {}).get('id')))
        planets = {planet.planet_id: planet for planet in session.target.planets}
        landed_names = [planets[planet_id].name for planet_id in session.landed_planets]
        captured_names = [planets[planet_id].name for planet_id in session.captured_planets]
        session.outcome = ('Landed forces on ' + ', '.join(landed_names) + '. ' +
                           ('Captured: ' + ', '.join(captured_names) + '.' if captured_names else
                            'Control did not change.'))
        if session.custodian_payment_log:
            session.outcome += ' ' + ' '.join(session.custodian_payment_log)
        if 'ie' in session.player.technologies and session.captured_planets:
            session.integrated_queue = list(session.captured_planets)
            self.advance_integrated_production(session)
        else:
            self.prepare_production(session)

    def finish(self):
        if self.session and self.session.stage == 'complete':
            session = self.session
            if (not session.strategic_production and self.exploration is not None and
                    'det' in session.player.technologies and
                    'frontier' in self.exploration.space_tokens.get(session.target.position, ()) and
                    any(unit.owner == session.player.faction and
                        unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']
                        for unit in session.target.units)):
                self.exploration_queue.append((session.player, session.target, None))
                self.resolve_exploration_queue()
            from leaders import refresh_leader_unlocks
            for participant in self.players:
                refresh_leader_unlocks(participant, self.board, self.players)
            if session.strategic_production:
                self.session = None
                return True
            checkpoint = None
            if session.rolled_any_dice:
                checkpoint = (session.production_checkpoint or session.invasion_checkpoint or
                              session.bombardment_checkpoint or session.movement_checkpoint)
            if checkpoint:
                self.history.append(UndoEntry(checkpoint.snapshot, session, checkpoint))
            else:
                self.history.append(UndoEntry(session.snapshot))
            self.session = None
            return True
        return False

    def legendary_planet_options(self, player):
        """Ready legendary cards and legal end-of-turn destinations for their abilities."""
        owned = {planet.planet_id for tile in self.board.values() for planet in tile.planets
                 if tile.planet_owners.get(planet.planet_id) == player.faction and
                 (self.exploration is None or not (
                     self.exploration.planet_is_blocked(planet.planet_id) or
                     planet.planet_id in self.exploration.destroyed_planets))}
        ready = {card.planet.planet_id for card in player.planets
                 if card.planet.planet_id in owned and
                 card.planet.planet_id not in player.exhausted_legendary_abilities}
        options = {}
        infantry_out = sum(unit.owner == player.faction and unit.kind == 'infantry'
                           for tile in self.board.values() for unit in tile.units)
        infantry_out += sum(player.infantry_on_cards.values())
        mech_out = sum(unit.owner == player.faction and unit.kind == 'mech'
                       for tile in self.board.values() for unit in tile.units)
        if 'primor' in ready and infantry_out < 12:
            options['primor'] = {'infantry': tuple(sorted(owned)),
                                 'maximum': min(2, 12 - infantry_out)}
        if 'hopesend' in ready:
            choices = {}
            if mech_out < 4:
                choices['mech'] = tuple(sorted(owned))
            if self.action_cards and self.action_cards.can_draw(player):
                choices['card'] = True
            if choices:
                options['hopesend'] = choices
        return options

    def use_legendary_planet(self, player, source_planet_id, *, destination_planet_id=None,
                             choice='infantry', count=2):
        """Exhaust Primor or Hope's End at the caller's end-of-turn timing window."""
        if self.session is not None or not any(player is participant for participant in self.players):
            raise MovementError('Resolve the current action before using a legendary planet.')
        options = self.legendary_planet_options(player)
        abilities = options.get(source_planet_id, {})
        if choice == 'infantry' and source_planet_id == 'primor':
            if (destination_planet_id not in abilities.get('infantry', ()) or
                    not 1 <= count <= abilities.get('maximum', 0)):
                raise MovementError('Primor must place one or two infantry on a controlled planet.')
            kind, quantity = 'infantry', count
        elif choice == 'mech' and source_planet_id == 'hopesend':
            if destination_planet_id not in abilities.get('mech', ()):
                raise MovementError("Hope's End needs a controlled planet and a mech in reinforcements.")
            kind, quantity = 'mech', 1
        elif choice == 'card' and source_planet_id == 'hopesend':
            if not abilities.get('card') or destination_planet_id is not None:
                raise MovementError("Hope's End cannot draw an action card now.")
            kind, quantity = 'card', 1
        else:
            raise MovementError('This legendary planet ability is unavailable.')
        snapshot = Snapshot.capture(self.board, player, self.players,
                                    self.action_cards, self.exploration)
        if kind == 'card':
            drawn = self.action_cards.draw(player, 1)
            if not drawn:
                raise MovementError('No action card could be drawn.')
        else:
            destination = next(tile for tile in self.board.values()
                               if destination_planet_id in {planet.planet_id for planet in tile.planets})
            for _ in range(quantity):
                destination.units.append(Unit(f'{player.faction}-{source_planet_id}-{kind}-{uuid4().hex}',
                                              kind, player.faction, player.color_code,
                                              UnitLocation(Region.PLANET, destination_planet_id)))
        player.exhausted_legendary_abilities.add(source_planet_id)
        self.history.append(UndoEntry(snapshot, counts_as_action=False))
        return {'source': source_planet_id, 'choice': kind, 'count': quantity,
                'destination': destination_planet_id}

    def cancel(self):
        if self.session:
            self.session.snapshot.restore()
            self.session = None
            self.exploration_queue.clear()
            self.exploration_outcomes.clear()
            return True
        return False

    def undo(self):
        if self.session:
            session = self.session
            if session.rolled_any_dice:
                checkpoint = None
                if session.stage in ('production', 'fleet_overflow'):
                    checkpoint = session.production_checkpoint
                elif session.stage in ('bombardment',):
                    checkpoint = session.bombardment_checkpoint
                elif session.stage in ('invasion', 'ground_combat'):
                    checkpoint = session.invasion_checkpoint
                if checkpoint is None:
                    checkpoint = session.movement_checkpoint
                if checkpoint:
                    checkpoint.restore(session)
                    self.exploration_queue.clear()
                    self.exploration_outcomes.clear()
                    return True
            return self.cancel()
        if self.history:
            entry = self.history.pop()
            entry.snapshot.restore()
            self.exploration_queue.clear()
            self.exploration_outcomes.clear()
            if entry.resumed_session and entry.checkpoint:
                entry.checkpoint.restore(entry.resumed_session)
                self.session = entry.resumed_session
            return True
        return False
