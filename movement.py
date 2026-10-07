from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil
import random

from player import PlanetCard
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profile, unit_profiles


class MovementError(ValueError):
    pass


def capital_ship(unit):
    return UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter'


def cargo_cost(unit):
    value = unit_profile(unit).get('capacityUsed')
    return int(value) if value is not None else 1


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
    action_hands: list
    action_state: object | None = None

    @classmethod
    def capture(cls, board, player, players, action_controller=None):
        all_players = players or [player]
        cards = [(other, list(other.planets)) for other in all_players]
        return cls(player, dict(player.command_pools),
                   [(tile, list(tile.units), set(tile.command_tokens), dict(tile.planet_owners)) for tile in board.values()],
                   [(unit, unit.location, unit.damaged) for tile in board.values() for unit in tile.units],
                   cards,
                   [(card, card.exhausted) for _, player_cards in cards for card in player_cards],
                   [(other, other.trade_goods, other.commodities) for other in all_players],
                   [(other, list(other.action_cards)) for other in all_players],
                   (action_controller, action_controller.snapshot()) if action_controller else None)

    def restore(self):
        self.player.command_pools.clear()
        self.player.command_pools.update(self.pools)
        for tile, units, tokens, owners in self.tiles:
            tile.units[:] = units
            tile.command_tokens.clear()
            tile.command_tokens.update(tokens)
            tile.planet_owners.clear()
            tile.planet_owners.update(owners)
        for unit, location, damaged in self.locations:
            unit.location = location
            unit.damaged = damaged
        for player, cards in self.planet_cards:
            player.planets[:] = cards
        for card, exhausted in self.card_states:
            card.exhausted = exhausted
        for player, trade_goods, commodities in self.currencies:
            player.trade_goods = trade_goods
            player.commodities = commodities
        for player, hand in self.action_hands:
            player.action_cards[:] = hand
        if self.action_state:
            controller, state = self.action_state
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
                                    controller.action_cards),
                   stage, dict(session.landings), dict(session.bombard_targets))

    def restore(self, session):
        self.snapshot.restore()
        session.stage = self.stage
        session.landings = dict(self.landings)
        session.outcome = ''
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
            session.overflow_required = 0
            session.overflow_selected.clear()
            session.overflow_next_stage = None


@dataclass
class UndoEntry:
    snapshot: Snapshot
    resumed_session: object | None = None
    checkpoint: SessionCheckpoint | None = None


@dataclass
class Session:
    player: object
    target: object
    sources: dict
    snapshot: Snapshot
    strategic_production: bool = False
    selected: set[str] = field(default_factory=set)
    stage: str = 'movement'
    landings: dict[str, str | None] = field(default_factory=dict)
    cannon_log: list[str] = field(default_factory=list)
    cannon_checked: bool = False
    afb_rolls: dict[str, list[dict]] = field(default_factory=dict)
    afb_log: list[str] = field(default_factory=list)
    afb_resolved: bool = False
    retreat_announced: str | None = None
    retreat_log: str = ''
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
    production_limit: int = 0
    production_sites: list[tuple[str, int]] = field(default_factory=list)
    production_choices: dict[str, int] = field(default_factory=dict)
    production_planets: set[str] = field(default_factory=set)
    trade_goods_to_spend: int = 0
    combat_round: int = 0
    combat_factions: tuple[str, ...] = ()
    combat_rolls: dict[str, list[dict]] = field(default_factory=dict)
    combat_hits: dict[str, int] = field(default_factory=dict)
    combat_assignments: dict[str, list[str]] = field(default_factory=dict)
    combat_needs_resolution: bool = False
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

    @property
    def choices(self):
        return {u.unit_id: u for source in self.sources.values() for u in source.ships + source.passengers}

    def ships(self, source):
        return [u for u in source.ships if u.unit_id in self.selected]

    def passengers(self, source):
        return [u for u in source.passengers if u.unit_id in self.selected]

    def carried(self, source, ships=None):
        ids = {u.unit_id for u in (self.ships(source) if ships is None else ships)}
        return [u for u in source.tile.units if u.location.region == Region.TRANSPORT and u.location.carrier_id in ids]

    def cargo_values(self, source):
        cargo = self.passengers(source) + self.carried(source)
        return sum(cargo_cost(unit) for unit in cargo), sum(u.capacity for u in self.ships(source))

    def toggle(self, unit_id):
        unit = self.choices.get(unit_id)
        if unit is None:
            raise MovementError('This unit cannot move to the activated system')
        source = next(s for s in self.sources.values() if unit in s.ships or unit in s.passengers)
        trial = self.selected ^ {unit_id}
        ships = [u for u in source.ships if u.unit_id in trial]
        cargo = [u for u in source.passengers if u.unit_id in trial]
        if unit in source.passengers and unit_id not in self.selected and not ships:
            raise MovementError('Select a ship before loading ground forces or fighters')
        used = sum(cargo_cost(unit) for unit in cargo + self.carried(source, ships))
        capacity = sum(u.capacity for u in ships)
        if used > capacity:
            raise MovementError('Not enough capacity in this source system. Select a transport or remove passengers first.')
        self.selected = trial


class MovementController:
    PRODUCIBLE_KINDS = {'infantry', 'fighter', 'destroyer', 'cruiser', 'carrier',
                        'dreadnought', 'mech', 'flagship', 'warsun'}

    def __init__(self, board, players=()):
        self.board = board
        self.players = list(players)
        self.session = None
        self.history: list[UndoEntry] = []
        self.action_cards = None

    def neighbors(self, tile):
        neighbors = list(self.board.neighbors(tile.position))
        if tile.wormholes:
            neighbors.extend(other for other in self.board.values()
                             if other is not tile and set(tile.wormholes) & set(other.wormholes)
                             and other not in neighbors)
        return neighbors

    @staticmethod
    def roll_d10(session):
        session.rolled_any_dice = True
        return random.randint(1, 10)

    def route(self, origin, target, unit, player, move_bonus=0):
        if player.faction in origin.command_tokens or origin is target or unit.move_value <= 0:
            return None
        budget = (min(unit.move_value, 1) if 'nebula' in origin.anomalies else unit.move_value) + move_bonus
        queue = deque([(origin, (origin.position,))])
        seen = {origin.position}
        while queue:
            tile, path = queue.popleft()
            if len(path) - 1 >= budget:
                continue
            for neighbor in self.neighbors(tile):
                if neighbor.position in seen:
                    continue
                if 'supernova' in neighbor.anomalies or 'gravity_rift' in neighbor.anomalies:
                    continue
                if 'asteroid_field' in neighbor.anomalies and 'amd' not in player.technologies:
                    continue
                next_path = path + (neighbor.position,)
                if neighbor is target:
                    return next_path
                enemy = any(u.owner != player.faction and UNIT_TYPES[u.kind]['ship']
                            and u.location.region == Region.SPACE for u in neighbor.units)
                if enemy or 'nebula' in neighbor.anomalies:
                    continue
                seen.add(neighbor.position)
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
        for origin in self.board.values():
            routes = {}
            ships = []
            for unit in origin.units:
                if unit.owner == player.faction and capital_ship(unit):
                    path = (target.position,) if origin is target else self.route(origin, target, unit, player)
                    if path:
                        ships.append(unit)
                        routes[unit.unit_id] = path
            if ships:
                passengers = [u for u in origin.units if u.owner == player.faction and
                              ((u.kind == 'fighter' and u.location.region == Region.SPACE) or
                               (u.kind in ('infantry', 'mech') and u.location.region == Region.PLANET))]
                sources[origin.position] = Source(origin, ships, passengers, routes)
        snapshot = Snapshot.capture(self.board, player, self.players or [player], self.action_cards)
        player.command_pools['tactical'] -= 1
        target.command_tokens.add(player.faction)
        self.session = Session(player, target, sources, snapshot)
        return self.session

    def apply_flank_speed(self, session):
        """Rebuild eligible movement routes after the activation-window bonus."""
        session.movement_bonus = 1
        sources = {}
        for origin in self.board.values():
            routes, ships = {}, []
            for unit in origin.units:
                if unit.owner != session.player.faction or not capital_ship(unit):
                    continue
                path = ((session.target.position,) if origin is session.target else
                        self.route(origin, session.target, unit, session.player,
                                   session.movement_bonus))
                if path:
                    ships.append(unit)
                    routes[unit.unit_id] = path
            if ships:
                passengers = [unit for unit in origin.units if unit.owner == session.player.faction and
                              ((unit.kind == 'fighter' and unit.location.region == Region.SPACE) or
                               (unit.kind in ('infantry', 'mech') and unit.location.region == Region.PLANET))]
                sources[origin.position] = Source(origin, ships, passengers, routes)
        eligible = {unit.unit_id for source in sources.values() for unit in source.ships + source.passengers}
        session.selected.intersection_update(eligible)
        session.sources = sources

    def add_command_token(self, player, position):
        tile = self.board[position]
        if player.faction in tile.command_tokens:
            raise MovementError('This system already has your command token')
        if player.command_pools['tactical'] <= 0:
            raise MovementError('No command tokens in the tactical reserve')
        player.command_pools['tactical'] -= 1
        tile.command_tokens.add(player.faction)

    def confirm(self):
        session = self.session
        if not session:
            raise MovementError('No active movement')
        if session.stage != 'movement':
            raise MovementError('Ship movement has already been completed')
        transfers = []
        for source in session.sources.values():
            ships = session.ships(source)
            passengers = session.passengers(source)
            carried = session.carried(source)
            if sum(cargo_cost(unit) for unit in passengers + carried) > sum(u.capacity for u in ships):
                raise MovementError('Transport capacity exceeded')
            remaining = {ship.unit_id: ship.capacity - sum(cargo_cost(unit) for unit in carried
                                                              if unit.location.carrier_id == ship.unit_id)
                         for ship in ships}
            transfers.extend((source.tile, ship, UnitLocation(Region.SPACE)) for ship in ships)
            transfers.extend((source.tile, u, u.location) for u in carried)
            for unit in passengers:
                required = cargo_cost(unit)
                carrier_id = next((key for key, slots in remaining.items() if slots >= required), None)
                if carrier_id is None:
                    raise MovementError('Transport capacity exceeded')
                remaining[carrier_id] -= required
                location = UnitLocation(Region.SPACE) if unit.kind == 'fighter' else UnitLocation(Region.TRANSPORT, carrier_id=carrier_id)
                transfers.append((source.tile, unit, location))
        # Apply only after the entire selection has passed validation.
        for origin, unit, location in transfers:
            origin.units.remove(unit)
            unit.location = location
            session.target.units.append(unit)
        session.cannon_log = []
        session.landings = ({unit.unit_id: None for unit in self.landing_forces(session)}
                            if session.target.planets else {})
        next_stage = ('space_combat' if self.hostile_space_factions(session.target, session.player.faction)
                      else 'invasion')
        moved_capital_ships = any(capital_ship(unit) for source in session.sources.values()
                                  for unit in session.ships(source))
        if moved_capital_ships:
            self._check_fleet_limit(session, next_stage)
        else:
            self.continue_after_movement(session, next_stage)
        return len(transfers)

    @staticmethod
    def hostile_space_factions(tile, faction):
        return tuple(sorted({unit.owner for unit in tile.units
                             if unit.owner != faction and unit.location.region == Region.SPACE and
                             UNIT_TYPES[unit.kind]['ship']}))

    def fleet_ships(self, session):
        return [unit for unit in session.target.units if unit.owner == session.player.faction and
                unit.location.region == Region.SPACE and capital_ship(unit)]

    def _check_fleet_limit(self, session, next_stage):
        excess = max(0, len(self.fleet_ships(session)) - session.player.command_pools['fleet'])
        if excess:
            session.stage = 'fleet_overflow'
            session.overflow_required = excess
            session.overflow_selected.clear()
            session.overflow_next_stage = next_stage
        elif next_stage in ('invasion', 'space_combat'):
            self.continue_after_movement(session, next_stage)
        else:
            session.stage = 'complete'
            self.finish()

    def continue_after_movement(self, session, next_stage):
        self.resolve_space_cannon(session)
        if next_stage == 'space_combat' and self.hostile_space_factions(
                session.target, session.player.faction) and self.combat_units(session, session.player.faction):
            self.start_combat(session)
        else:
            if next_stage == 'space_combat':
                session.space_combat_resolved = True
            self.prepare_invasion(session)

    def resolve_space_cannon(self, session):
        if session.cannon_checked:
            return
        session.cannon_checked = True
        active_faction = session.player.faction
        active_ships = [unit for unit in session.target.units if unit.owner == active_faction and
                        unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        if not active_ships:
            return
        shooters = []
        for source in (session.target, *self.neighbors(session.target)):
            for unit in source.units:
                profile = unit_profile(unit)
                if unit.kind != 'pds' or unit.owner == active_faction or not profile.get('spaceCannonHitsOn'):
                    continue
                if source is not session.target and not profile.get('deepSpaceCannon'):
                    continue
                shooters.append((source, unit, profile))
        if not shooters:
            return
        for source, cannon, profile in shooters:
            dice = [self.roll_d10(session) for _ in range(int(profile.get('spaceCannonDieCount') or 1))]
            hits = sum(value >= int(profile['spaceCannonHitsOn']) for value in dice)
            session.cannon_log.append(
                f'{cannon.owner.upper()} PDS in tile {source.system_id}: {dice} → {hits} hit(s)')
            for _ in range(hits):
                candidates = [unit for unit in active_ships if unit in session.target.units]
                if not candidates:
                    break
                target = self.apply_hit(session.target, candidates)
                session.cannon_log.append(
                    f'{target.kind.title()} {"damaged" if target.damaged else "destroyed"}')

    @staticmethod
    def apply_hit(tile, candidates):
        target = sorted(candidates, key=lambda unit: (
            not (unit_profile(unit).get('sustainDamage') and not unit.damaged), unit.unit_id))[0]
        if unit_profile(target).get('sustainDamage') and not target.damaged:
            target.damaged = True
        else:
            tile.units.remove(target)
            if UNIT_TYPES[target.kind]['ship']:
                for cargo in list(tile.units):
                    if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id == target.unit_id:
                        tile.units.remove(cargo)
        return target

    def resolve_anti_fighter_barrage(self, session):
        session.afb_rolls.clear()
        session.afb_log.clear()
        factions = session.combat_factions
        for faction in factions:
            enemies = [unit for enemy in factions if enemy != faction
                       for unit in self.combat_units(session, enemy) if unit.kind == 'fighter']
            if not enemies:
                continue
            rolls = []
            for ship in self.combat_units(session, faction):
                profile = unit_profile(ship)
                if not profile.get('afbHitsOn'):
                    continue
                for _ in range(int(profile.get('afbDieCount') or 1)):
                    value = self.roll_d10(session)
                    rolls.append({'unit_id': ship.unit_id, 'kind': ship.kind, 'value': value,
                                  'hit': value >= int(profile['afbHitsOn'])})
            session.afb_rolls[faction] = rolls
            hits = sum(roll['hit'] for roll in rolls)
            destroyed = []
            for enemy in enemies:
                if not hits:
                    break
                if enemy in session.target.units:
                    session.target.units.remove(enemy)
                    destroyed.append(enemy.owner.upper())
                    hits -= 1
            session.afb_log.append(
                f'{faction.upper()} anti-fighter barrage: {sum(r["hit"] for r in rolls)} hit(s); '
                f'{len(destroyed)} fighter(s) destroyed')
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
            if not hostile_fleet and has_friendly:
                options.append(tile)
        return options

    def announce_retreat(self):
        session = self.session
        if not session or session.stage != 'space_combat' or session.combat_needs_resolution:
            raise MovementError('Retreat can only be announced before combat rolls')
        faction = session.player.faction
        if session.retreat_announced:
            raise MovementError('A retreat has already been announced')
        if not self.retreat_options(session, faction):
            raise MovementError('No adjacent system with your ships is available for retreat')
        session.retreat_announced = faction

    def resolve_retreat(self, position):
        session = self.session
        if not session or session.stage != 'retreat_selection' or not session.retreat_announced:
            raise MovementError('No retreat destination is being selected')
        destination = self.board[position]
        if destination not in self.retreat_options(session, session.retreat_announced):
            raise MovementError('This system is not a valid retreat destination')
        ships = [unit for unit in session.target.units if unit.owner == session.retreat_announced and
                 unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        ship_ids = {unit.unit_id for unit in ships}
        retreating = ships + [unit for unit in session.target.units if unit.location.region == Region.TRANSPORT and
                              unit.location.carrier_id in ship_ids]
        for unit in retreating:
            session.target.units.remove(unit)
            destination.units.append(unit)
        retreating_player = next((player for player in self.players
                                  if player.faction == session.retreat_announced), session.player)
        if (session.skilled_retreat and session.retreat_announced not in destination.command_tokens and
                self.action_cards and self.action_cards.has_reinforcement(retreating_player)):
            destination.command_tokens.add(session.retreat_announced)
        elif not session.skilled_retreat and session.retreat_announced not in destination.command_tokens:
            destination.command_tokens.add(session.retreat_announced)
        session.retreat_log = f'{session.retreat_announced.upper()} retreated to tile {destination.system_id}.'
        session.space_combat_resolved = True
        self.prepare_invasion(session)

    def has_planetary_shield(self, tile):
        return any(unit.kind == 'pds' and unit_profile(unit).get('planetaryShield')
                   for unit in tile.units)

    def prepare_invasion(self, session):
        faction = session.player.faction
        hostile_ground = {unit.location.planet_id for unit in session.target.units
                          if unit.owner != faction and unit.location.region == Region.PLANET and
                          unit.kind in ('infantry', 'mech')}
        bombers = [unit for unit in session.target.units if unit.owner == faction and
                   unit.location.region == Region.SPACE and
                   unit_profile(unit).get('bombardHitsOn')]
        hostile_shield = any(unit.owner != faction and unit.kind == 'pds' and
                             unit_profile(unit).get('planetaryShield') for unit in session.target.units)
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
                          unit.location.planet_id == planet.planet_id and unit.kind in ('infantry', 'mech')
                          for unit in session.target.units)]
        choices = [None, *options]
        current = session.bombard_targets[unit_id]
        session.bombard_targets[unit_id] = choices[(choices.index(current) + 1) % len(choices)]

    def resolve_bombardment(self):
        session = self.session
        if not session or session.stage != 'bombardment':
            raise MovementError('Bombardment is not active')
        faction = session.player.faction
        for unit_id, planet_id in session.bombard_targets.items():
            if planet_id is None:
                continue
            ship = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
            if ship is None:
                continue
            profile = unit_profile(ship)
            rolls = []
            for _ in range(int(profile.get('bombardDieCount') or 1)):
                value = self.roll_d10(session)
                rolls.append({'unit_id': unit_id, 'kind': ship.kind, 'planet_id': planet_id,
                              'value': value, 'hit': value >= int(profile['bombardHitsOn'])})
            session.bombard_rolls.extend(rolls)
            hits = sum(roll['hit'] for roll in rolls)
            destroyed = 0
            for _ in range(hits):
                defenders = [unit for unit in session.target.units if unit.owner != faction and
                             unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                             unit.kind in ('infantry', 'mech')]
                if not defenders:
                    break
                self.apply_hit(session.target, defenders)
                destroyed += 1
            planet = next(planet for planet in session.target.planets if planet.planet_id == planet_id)
            session.bombard_log.append(
                f'{ship.kind.title()} bombarded {planet.name}: {rolls} → {destroyed} hit(s).')
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

    def resolve_fleet_overflow(self):
        session = self.session
        if not session or session.stage != 'fleet_overflow':
            raise MovementError('Fleet limit is not being checked')
        if len(session.overflow_selected) != session.overflow_required:
            raise MovementError(f'Select exactly {session.overflow_required} ships to destroy')
        destroyed = set(session.overflow_selected)
        for unit in list(session.target.units):
            if unit.unit_id in destroyed or (
                    unit.location.region == Region.TRANSPORT and unit.location.carrier_id in destroyed):
                session.target.units.remove(unit)
        next_stage = session.overflow_next_stage
        session.overflow_required = 0
        session.overflow_selected.clear()
        session.overflow_next_stage = None
        if next_stage in ('invasion', 'space_combat'):
            self.continue_after_movement(session, next_stage)
        else:
            session.stage = 'complete'
            self.finish()

    def start_combat(self, session):
        session.combat_type = 'space'
        session.space_combat_resolved = False
        session.skilled_retreat = False
        session.combat_planet_id = None
        session.combat_factions = (session.player.faction,) + self.hostile_space_factions(
            session.target, session.player.faction)
        self.resolve_anti_fighter_barrage(session)
        session.combat_factions = tuple(faction for faction in session.combat_factions
                                        if self.combat_units(session, faction))
        session.combat_round = 0
        session.combat_rolls.clear()
        session.combat_hits.clear()
        session.combat_assignments.clear()
        session.combat_needs_resolution = False
        session.combat_modifiers.clear()
        session.fighter_combat_modifiers.clear()
        session.played_action_windows.clear()
        if len(session.combat_factions) > 1:
            session.stage = 'space_combat'
        else:
            session.space_combat_resolved = True
            self.prepare_invasion(session)

    def start_ground_combat(self, session, planet_id):
        session.combat_type = 'ground'
        session.combat_planet_id = planet_id
        defenders = tuple(sorted({unit.owner for unit in session.target.units
                                  if unit.owner != session.player.faction and
                                  unit.location.region == Region.PLANET and
                                  unit.location.planet_id == planet_id and
                                  unit.kind in ('infantry', 'mech')}))
        session.combat_factions = (session.player.faction,) + defenders
        session.combat_round = 0
        session.combat_rolls.clear()
        session.combat_hits.clear()
        session.combat_assignments.clear()
        session.combat_needs_resolution = False
        session.combat_rolls.clear()
        session.combat_modifiers.clear()
        session.fighter_combat_modifiers.clear()
        session.stage = 'ground_combat'

    def combat_units(self, session, faction):
        if session.combat_type == 'ground':
            return [unit for unit in session.target.units if unit.owner == faction and
                    unit.location.region == Region.PLANET and
                    unit.location.planet_id == session.combat_planet_id and
                    unit.kind in ('infantry', 'mech')]
        return [unit for unit in session.target.units if unit.owner == faction and
                unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]

    def combat_hit_capacity(self, session, faction):
        total = 0
        for unit in self.combat_units(session, faction):
            sustain = bool(unit_profile(unit).get('sustainDamage'))
            total += 2 if sustain and not unit.damaged else 1
        return total

    def combat_assignment_target(self, session, faction, kind):
        assigned = session.combat_assignments.setdefault(faction, [])
        counts = {unit_id: assigned.count(unit_id) for unit_id in set(assigned)}
        for unit in sorted(self.combat_units(session, faction), key=lambda item: item.unit_id):
            if unit.kind != kind:
                continue
            max_hits = 2 if unit_profile(unit).get('sustainDamage') and not unit.damaged else 1
            if counts.get(unit.unit_id, 0) < max_hits:
                return unit
        return None

    def assign_combat_hit(self, faction, kind):
        session = self.session
        if not session or session.stage not in ('space_combat', 'ground_combat') or not session.combat_needs_resolution:
            raise MovementError('Roll combat dice before assigning hits')
        hits = session.combat_hits.get(faction, 0)
        assignments = session.combat_assignments.setdefault(faction, [])
        required = min(hits, self.combat_hit_capacity(session, faction))
        if len(assignments) >= required:
            raise MovementError('All hits against this fleet have been assigned')
        unit = self.combat_assignment_target(session, faction, kind)
        if unit is None:
            raise MovementError('No eligible unit of this type can take another hit')
        assignments.append(unit.unit_id)

    def combat_assignments_complete(self, session):
        return all(len(session.combat_assignments.get(faction, [])) >=
                   min(session.combat_hits.get(faction, 0), self.combat_hit_capacity(session, faction))
                   for faction in session.combat_factions)

    def advance_combat(self):
        session = self.session
        if not session or session.stage not in ('space_combat', 'ground_combat', 'combat_end'):
            raise MovementError('Combat is not active')
        if session.stage == 'combat_end':
            self._finish_combat_round(session)
            return
        if session.combat_needs_resolution:
            if not self.combat_assignments_complete(session):
                raise MovementError('Assign all available hits before continuing')
            for faction in session.combat_factions:
                for unit_id in session.combat_assignments.get(faction, []):
                    unit = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
                    if unit is None:
                        continue
                    if unit_profile(unit).get('sustainDamage') and not unit.damaged:
                        unit.damaged = True
                        continue
                    session.target.units.remove(unit)
                    for cargo in list(session.target.units):
                        if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id == unit.unit_id:
                            session.target.units.remove(cargo)
            session.combat_needs_resolution = False
            session.stage = 'combat_end'
            if self.action_cards and any(
                    self.action_cards.can_play(faction, 'emergency_repairs', session)
                    for faction in session.combat_factions):
                return
            self._finish_combat_round(session)
            return

        session.combat_round += 1
        session.combat_rolls = {}
        session.combat_hits = {}
        session.combat_assignments = {faction: [] for faction in session.combat_factions}
        outgoing_hits = {}
        for faction in session.combat_factions:
            rolls = []
            for unit in self.combat_units(session, faction):
                profile = unit_profile(unit)
                for _ in range(int(profile.get('combatDieCount') or 1)):
                    value = self.roll_d10(session)
                    modifier = session.combat_modifiers.get(faction, 0)
                    if unit.kind == 'fighter':
                        modifier += session.fighter_combat_modifiers.get(faction, 0)
                    result = value + modifier
                    rolls.append({'unit_id': unit.unit_id, 'kind': unit.kind, 'value': result,
                                  'natural': value, 'modifier': modifier,
                                  'hit': result >= int(profile.get('combatHitsOn') or 10)})
            session.combat_rolls[faction] = rolls
            outgoing_hits[faction] = sum(result['hit'] for result in rolls)
        session.combat_hits = {faction: sum(hits for shooter, hits in outgoing_hits.items()
                                             if shooter != faction)
                               for faction in session.combat_factions}
        session.combat_needs_resolution = True

    def _finish_combat_round(self, session):
        own_alive = bool(self.combat_units(session, session.player.faction))
        enemies_alive = any(self.combat_units(session, faction)
                            for faction in session.combat_factions if faction != session.player.faction)
        if session.combat_type == 'ground' and (not own_alive or not enemies_alive):
            self.finish_ground_battle(session, own_alive, enemies_alive)
        elif not own_alive or not enemies_alive:
            session.space_combat_resolved = True
            self.prepare_invasion(session)
        elif session.combat_type == 'space' and session.retreat_announced:
            session.stage = 'retreat_selection'
        else:
            session.stage = 'space_combat' if session.combat_type == 'space' else 'ground_combat'
            session.combat_rolls.clear()
            session.combat_hits.clear()
            session.combat_assignments = {faction: [] for faction in session.combat_factions}
            session.combat_modifiers.clear()
            session.fighter_combat_modifiers.clear()

    def production_sites(self, session):
        sites = []
        for unit in session.target.units:
            if unit.owner != session.player.faction or unit.location.region != Region.PLANET:
                continue
            planet_id = unit.location.planet_id
            planet = next((planet for planet in session.target.planets if planet.planet_id == planet_id), None)
            if not planet or session.target.planet_owners.get(planet_id) != session.player.faction:
                continue
            value = unit_profile(unit).get('productionValue')
            if value is None:
                continue
            if unit.kind == 'spacedock':
                value_text = str(value)
                production = planet.resources + int(value_text[1:]) if value_text.startswith('+') else int(value)
            else:
                production = int(value)
            if production > 0:
                sites.append((planet_id, production))
        return sites

    def unit_cost(self, kind, player):
        costs = {'infantry': .5, 'fighter': .5, 'destroyer': 1, 'cruiser': 2,
                 'carrier': 3, 'dreadnought': 4, 'mech': 2, 'pds': 2,
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
        session.stage = 'production'
        return True

    def start_strategy_production(self, player, target, dock):
        if self.session:
            raise MovementError('Finish the current action first.')
        session = Session(player, target, {}, Snapshot.capture(self.board, player, self.players,
                                                               self.action_cards),
                          strategic_production=True)
        planet = next(p for p in target.planets if p.planet_id == dock.location.planet_id)
        value = str(unit_profile(dock).get('productionValue', '+2'))
        limit = planet.resources + int(value[1:]) if value.startswith('+') else int(value)
        session.production_sites = [(planet.planet_id, limit)]
        session.production_limit = limit
        session.stage = 'production'
        session.production_checkpoint = SessionCheckpoint.capture(self, session, 'production')
        self.session = session
        return session

    def production_total(self, session):
        return sum(session.production_choices.values())

    def production_cost(self, session):
        return sum(ceil(self.unit_cost(kind, session.player) * count)
                   for kind, count in session.production_choices.items())

    def production_payment(self, session):
        card_resources = sum(card.planet.resources for card in session.player.planets
                             if card.planet.planet_id in session.production_planets)
        return card_resources + session.trade_goods_to_spend

    def adjust_production(self, kind, delta):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        if kind not in self.PRODUCIBLE_KINDS:
            raise MovementError('Structures cannot be produced yet')
        current = session.production_choices.get(kind, 0)
        if delta > 0:
            remaining = session.production_limit - self.production_total(session)
            addition = min(2, remaining) if kind in ('infantry', 'fighter') else 1
            if addition > remaining:
                return
            session.production_choices[kind] = current + addition
        elif current:
            updated = current - 1
            if updated:
                session.production_choices[kind] = updated
            else:
                session.production_choices.pop(kind, None)

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
        if self.production_payment(session) < self.production_cost(session):
            raise MovementError('Not enough exhausted planet resources and trade goods')
        for card in session.player.planets:
            if card.planet.planet_id in session.production_planets:
                card.exhausted = True
        session.player.trade_goods -= session.trade_goods_to_spend
        used_ids = {unit.unit_id for tile in self.board.values() for unit in tile.units}
        next_id = 1
        for kind, count in session.production_choices.items():
            for _ in range(count):
                while f'{session.player.faction}-built-{next_id}' in used_ids:
                    next_id += 1
                unit_id = f'{session.player.faction}-built-{next_id}'
                used_ids.add(unit_id)
                next_id += 1
                if kind in ('infantry', 'mech', 'pds', 'spacedock'):
                    planet_id = session.production_sites[0][0]
                    location = UnitLocation(Region.PLANET, planet_id=planet_id)
                else:
                    location = UnitLocation(Region.SPACE)
                session.target.units.append(Unit(unit_id, kind, session.player.faction,
                                                 session.player.color_code, location))
        session.stage = 'complete'
        if any(UNIT_TYPES[kind]['ship'] and kind != 'fighter' and count
               for kind, count in session.production_choices.items()):
            self._check_fleet_limit(session, 'complete')
        else:
            self.finish()

    def skip_production(self):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        for card in session.player.planets:
            if card.planet.planet_id in session.production_planets:
                card.exhausted = False
        session.stage = 'complete'
        self.finish()

    def landing_forces(self, session):
        return [unit for unit in session.target.units if unit.owner == session.player.faction and
                unit.kind in ('infantry', 'mech') and unit.location.region == Region.TRANSPORT]

    def cycle_landing(self, unit_id):
        session = self.session
        if not session or session.stage != 'invasion' or unit_id not in session.landings:
            raise MovementError('This ground force cannot be landed')
        planets = list(session.target.planets)
        choices = [None, *(planet.planet_id for planet in planets)]
        current = session.landings[unit_id]
        session.landings[unit_id] = choices[(choices.index(current) + 1) % len(choices)]

    def establish_control(self):
        session = self.session
        if not session or session.stage != 'invasion':
            raise MovementError('Invasion is not active')
        session.invasion_checkpoint = SessionCheckpoint.capture(self, session, 'invasion')
        target, player = session.target, session.player
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
                         unit.kind in ('infantry', 'mech')]
            if not attackers:
                continue
            defenders = [unit for unit in target.units if unit.owner != player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                         unit.kind in ('infantry', 'mech')]
            if not defenders:
                self.capture_planet(session, planet_id)
            else:
                session.ground_planets.append(planet_id)
        if not landed_planets:
            session.outcome = 'No ground forces landed. No planets changed control.'
            self.prepare_production(session)
        elif session.ground_planets:
            self.start_ground_combat(session, session.ground_planets[0])
        else:
            self.finish_invasion(session)

    def resolve_planetary_cannon_defense(self, session):
        session.defense_log.clear()
        session.defense_checked = True
        faction = session.player.faction
        for planet_id in session.landed_planets:
            cannons = [unit for unit in session.target.units if unit.owner != faction and unit.kind == 'pds' and
                       unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                       unit_profile(unit).get('spaceCannonHitsOn')]
            if not cannons:
                continue
            for cannon in cannons:
                profile = unit_profile(cannon)
                dice = [self.roll_d10(session)
                        for _ in range(int(profile.get('spaceCannonDieCount') or 1))]
                hits = sum(value >= int(profile['spaceCannonHitsOn']) for value in dice)
                session.defense_log.append(
                    f'{cannon.owner.upper()} PDS on {planet_id}: {dice} → {hits} hit(s)')
                for _ in range(hits):
                    attackers = [unit for unit in session.target.units if unit.owner == faction and
                                 unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                                 unit.kind in ('infantry', 'mech')]
                    if not attackers:
                        break
                    target = self.apply_hit(session.target, attackers)
                    session.defense_log.append(
                        f'{target.kind.title()} on {planet_id} {"damaged" if target.damaged else "destroyed"}')

    def capture_planet(self, session, planet_id):
        target, player = session.target, session.player
        if target.planet_owners.get(planet_id) == player.faction:
            return
        target.planet_owners[planet_id] = player.faction
        for unit in list(target.units):
            if unit.owner != player.faction and unit.location.region == Region.PLANET and \
                    unit.location.planet_id == planet_id and unit.kind in ('pds', 'spacedock'):
                target.units.remove(unit)
        for other in self.players:
            other.planets[:] = [card for card in other.planets if card.planet.planet_id != planet_id]
        planet = next(planet for planet in target.planets if planet.planet_id == planet_id)
        player.planets.append(PlanetCard(planet, exhausted=True))
        if planet_id not in session.captured_planets:
            session.captured_planets.append(planet_id)

    def finish_ground_battle(self, session, attackers_alive, defenders_alive):
        planet_id = session.combat_planet_id
        if attackers_alive and not defenders_alive:
            self.capture_planet(session, planet_id)
        session.ground_planet_index += 1
        while session.ground_planet_index < len(session.ground_planets):
            next_planet = session.ground_planets[session.ground_planet_index]
            defenders = [unit for unit in session.target.units if unit.owner != session.player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == next_planet and
                         unit.kind in ('infantry', 'mech')]
            attackers = [unit for unit in session.target.units if unit.owner == session.player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == next_planet and
                         unit.kind in ('infantry', 'mech')]
            if defenders and attackers:
                self.start_ground_combat(session, next_planet)
                return
            if attackers and not defenders:
                self.capture_planet(session, next_planet)
            session.ground_planet_index += 1
        session.stage = 'invasion'
        self.finish_invasion(session)

    def finish_invasion(self, session):
        planets = {planet.planet_id: planet for planet in session.target.planets}
        landed_names = [planets[planet_id].name for planet_id in session.landed_planets]
        captured_names = [planets[planet_id].name for planet_id in session.captured_planets]
        session.outcome = ('Landed forces on ' + ', '.join(landed_names) + '. ' +
                           ('Captured: ' + ', '.join(captured_names) + '.' if captured_names else
                            'Control did not change.'))
        self.prepare_production(session)

    def finish(self):
        if self.session and self.session.stage == 'complete':
            session = self.session
            if session.strategic_production:
                self.session = None
                return True
            checkpoint = None
            if session.rolled_any_dice:
                checkpoint = (session.production_checkpoint or session.invasion_checkpoint or
                              session.bombardment_checkpoint)
            if checkpoint:
                self.history.append(UndoEntry(checkpoint.snapshot, session, checkpoint))
            else:
                self.history.append(UndoEntry(session.snapshot))
            self.session = None
            return True
        return False

    def cancel(self):
        if self.session:
            self.session.snapshot.restore()
            self.session = None
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
                if checkpoint:
                    checkpoint.restore(session)
                    return True
            return self.cancel()
        if self.history:
            entry = self.history.pop()
            entry.snapshot.restore()
            if entry.resumed_session and entry.checkpoint:
                entry.checkpoint.restore(entry.resumed_session)
                self.session = entry.resumed_session
            return True
        return False
