from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil
import random
from uuid import uuid4

from player import PlanetCard
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profile, unit_profiles
from technology import production_allowed, upgrade_profile


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
    technology_states: list
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
                   [(other, set(other.exhausted_technologies), dict(other.infantry_on_cards))
                    for other in all_players],
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
        for player, exhausted, infantry in self.technology_states:
            player.exhausted_technologies.clear()
            player.exhausted_technologies.update(exhausted)
            player.infantry_on_cards.clear()
            player.infantry_on_cards.update(infantry)
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
        session.capacity_required = 0
        session.capacity_selected.clear()
        session.capacity_position = None
        session.capacity_faction = None
        session.capacity_queue.clear()
        session.capacity_next_stage = None
        session.capacity_affected.clear()
        session.fleet_queue.clear()
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
    space_cannon_events: list[dict] = field(default_factory=list)
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
    production_limit: int = 0
    production_sites: list[tuple[str, int]] = field(default_factory=list)
    production_choices: dict[str, int] = field(default_factory=dict)
    production_planets: set[str] = field(default_factory=set)
    integrated_queue: list[str] = field(default_factory=list)
    integrated_current: str | None = None
    trade_goods_to_spend: int = 0
    combat_round: int = 0
    combat_factions: tuple[str, ...] = ()
    combat_rolls: dict[str, list[dict]] = field(default_factory=dict)
    combat_hits: dict[str, int] = field(default_factory=dict)
    combat_assignments: dict[str, list[str]] = field(default_factory=dict)
    munitions_ready: set[str] = field(default_factory=set)
    munitions_available: set[str] = field(default_factory=set)
    munitions_spent_round: set[tuple[str, int]] = field(default_factory=set)
    reroll_selected: dict[str, set[int]] = field(default_factory=dict)
    gravity_bonus_ids: set[str] = field(default_factory=set)
    spatial_conduit_active: bool = False
    combat_needs_resolution: bool = False
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
        if sum(unit_id in self.gravity_bonus_ids for unit_id in trial) > 1:
            raise MovementError('Gravity Drive can boost only one ship in this action.')
        self.selected = trial


class MovementController:
    PRODUCIBLE_KINDS = {'infantry', 'fighter', 'destroyer', 'cruiser', 'carrier',
                        'dreadnought', 'flagship', 'warsun'}

    def __init__(self, board, players=()):
        self.board = board
        self.players = list(players)
        self.session = None
        self.history: list[UndoEntry] = []
        self.action_cards = None

    def faction_player(self, faction):
        return next((player for player in self.players if player.faction == faction), None)

    def has_tech(self, faction, alias):
        player = self.faction_player(faction)
        return bool(player and alias in player.technologies)

    def destroy_unit(self, tile, unit, session=None, source_factions=()):
        """Destroy a unit and resolve technology reactions to its destruction."""
        player = self.faction_player(unit.owner)
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
        tile.units.remove(unit)
        if UNIT_TYPES[unit.kind]['ship']:
            for cargo in list(tile.units):
                if cargo.location.region == Region.TRANSPORT and cargo.location.carrier_id == unit.unit_id:
                    self.destroy_unit(tile, cargo, session, source_factions)

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

    def route(self, origin, target, unit, player, bonus=0, move_bonus=0, ignore_enemy=False):
        if player.faction in origin.command_tokens or origin is target or unit.move_value <= 0:
            return None
        budget = (min(unit.move_value, 1) if 'nebula' in origin.anomalies else unit.move_value) + bonus + move_bonus
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
                if (enemy and 'lwd' not in player.technologies and not ignore_enemy) or \
                        'nebula' in neighbor.anomalies:
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
        gravity_bonus_ids = set()
        for origin in self.board.values():
            routes = {}
            ships = []
            for unit in origin.units:
                independent_fighter = unit.kind == 'fighter' and 'ff2' in player.technologies and \
                    unit.location.region == Region.SPACE
                if unit.owner == player.faction and (capital_ship(unit) or independent_fighter):
                    path = (target.position,) if origin is target else self.route(origin, target, unit, player)
                    if not path and 'gd' in player.technologies:
                        path = self.route(origin, target, unit, player, bonus=1)
                        if path:
                            gravity_bonus_ids.add(unit.unit_id)
                    if path:
                        ships.append(unit)
                        routes[unit.unit_id] = path
            if ships:
                passengers = [u for u in origin.units if u.owner == player.faction and
                              ((u.kind == 'fighter' and u.location.region == Region.SPACE and u not in ships) or
                               (u.kind == 'infantry' and u.location.region == Region.PLANET))]
                sources[origin.position] = Source(origin, ships, passengers, routes)
        snapshot = Snapshot.capture(self.board, player, self.players or [player], self.action_cards)
        player.command_pools['tactical'] -= 1
        target.command_tokens.add(player.faction)
        self.session = Session(player, target, sources, snapshot)
        self.session.gravity_bonus_ids = gravity_bonus_ids
        for opponent in self.players:
            if opponent is not player and 'ers' in opponent.technologies and any(
                    unit.owner == opponent.faction and unit.location.region == Region.SPACE and
                    UNIT_TYPES[unit.kind]['ship'] for unit in target.units):
                opponent.trade_goods += 4
        return self.session

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
                        not UNIT_TYPES[unit.kind]['ship'] or not unit.move_value:
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
                                     ((u.kind == 'fighter' and u.location.region == Region.SPACE and
                                       u not in source.ships) or
                                      (u.kind == 'infantry' and u.location.region == Region.PLANET))]

    def apply_flank_speed(self, session):
        """Rebuild eligible movement routes after the activation-window bonus."""
        session.movement_bonus = 1
        sources = {}
        gravity_bonus_ids = set()
        for origin in self.board.values():
            routes, ships = {}, []
            for unit in origin.units:
                independent_fighter = (unit.kind == 'fighter' and
                                       'ff2' in session.player.technologies and
                                       unit.location.region == Region.SPACE)
                if unit.owner != session.player.faction or not (capital_ship(unit) or independent_fighter):
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
                passengers = [unit for unit in origin.units if unit.owner == session.player.faction and
                              ((unit.kind == 'fighter' and unit.location.region == Region.SPACE and unit not in ships) or
                               (unit.kind == 'infantry' and unit.location.region == Region.PLANET))]
                sources[origin.position] = Source(origin, ships, passengers, routes)
        eligible = {unit.unit_id for source in sources.values() for unit in source.ships + source.passengers}
        session.selected.intersection_update(eligible)
        session.sources = sources
        session.gravity_bonus_ids = gravity_bonus_ids

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
            independent_fighter = (unit.kind == 'fighter' and 'ff2' in player.technologies and
                                   unit.location.region == Region.SPACE)
            if unit.owner != player.faction or not (capital_ship(unit) or independent_fighter):
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
        source.passengers = [unit for unit in origin.units if unit.owner == player.faction and
                             ((unit.kind == 'fighter' and unit.location.region == Region.SPACE and
                               unit not in source.ships) or
                              (unit.kind == 'infantry' and unit.location.region == Region.PLANET))]
        session.gravity_bonus_ids.update(gravity_bonus_ids)
        session.selected.intersection_update(session.choices)

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
        snapshot = Snapshot.capture(self.board, player, self.players)
        player.command_pools['strategic'] -= 1
        for _ in range(2):
            target.units.append(Unit(f'{player.faction}-orbital-{uuid4().hex}', 'infantry',
                                     player.faction, player.color_code,
                                     UnitLocation(Region.PLANET, planet_id=planet_id),
                                     profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
        self.history.append(UndoEntry(snapshot))
        return target

    @staticmethod
    def fleet_supply(player):
        return player.command_pools['fleet'] + (2 if player.faction == 'letnev' else 0)

    @staticmethod
    def combat_threshold(faction, profile):
        return int(profile.get('combatHitsOn') or 10) + (1 if faction == 'jolnar' else 0)

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
            value = self.roll_d10(session)
            roll['value'] = value
            roll['hit'] = value >= self.combat_threshold(faction, unit_profile(
                next(unit for unit in session.target.units if unit.unit_id == roll['unit_id'])))
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
        ships_entering_target = {ship.unit_id for source in session.sources.values()
                                 if source.tile is not session.target
                                 for ship in session.ships(source)}
        for origin, unit, location in transfers:
            origin.units.remove(unit)
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
        self._check_capacity_and_fleet(session, affected, next_stage)
        return len(transfers)

    @staticmethod
    def hostile_space_factions(tile, faction):
        return tuple(sorted({unit.owner for unit in tile.units
                             if unit.owner != faction and unit.location.region == Region.SPACE and
                             UNIT_TYPES[unit.kind]['ship']}))

    def _capacity_units(self, tile, faction):
        return [unit for unit in tile.units if unit.owner == faction and
                ((unit.kind == 'fighter' and unit.location.region in (Region.SPACE, Region.TRANSPORT)) or
                 (unit.kind == 'infantry' and unit.location.region == Region.TRANSPORT))]

    def _capacity_value(self, tile, faction):
        return sum(unit.capacity for unit in tile.units if unit.owner == faction and
                   unit.location.region == Region.SPACE and capital_ship(unit))

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
        ground_forces = sum(cargo_cost(unit) for unit in units if unit.kind == 'infantry')
        base_fighters = [unit for unit in units if unit.kind == 'fighter' and
                         not self._fighter_ii(unit)]
        fighter_ii_in_space = sorted((unit for unit in units if unit.kind == 'fighter' and
                                      unit.location.region == Region.SPACE and self._fighter_ii(unit)),
                                     key=lambda unit: unit.unit_id)
        fighter_ii_transported = [unit for unit in units if unit.kind == 'fighter' and
                                  unit.location.region == Region.TRANSPORT and self._fighter_ii(unit)]
        dock_slots = 3 * sum(unit.owner == faction and unit.kind == 'spacedock'
                             for unit in tile.units)
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
        if next_stage in ('invasion', 'space_combat'):
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
        active_ships = [unit for unit in session.target.units if unit.owner == active_faction and
                        unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        if not active_ships:
            return True
        shooters = []
        for source in (session.target, *self.neighbors(session.target)):
            for unit in source.units:
                profile = unit_profile(unit)
                if (unit.kind != 'pds' or unit.owner == active_faction or
                        unit.unit_id in session.disabled_pds or not profile.get('spaceCannonHitsOn')):
                    continue
                if source is not session.target and not profile.get('deepSpaceCannon'):
                    continue
                shooters.append((source, unit, profile))
        if not shooters:
            return self._finish_space_cannon_rolls(session, active_ships)
        plasma_used = set()
        for source, cannon, profile in shooters:
            shooter = self.faction_player(cannon.owner)
            extra = int(bool(shooter and 'ps' in shooter.technologies and
                             cannon.owner not in plasma_used))
            if extra:
                plasma_used.add(cannon.owner)
            dice = [self.roll_d10(session) for _ in range(int(profile.get('spaceCannonDieCount') or 1) + extra)]
            modifier = -1 if 'amd' in session.player.technologies else 0
            hits = sum(value + modifier >= int(profile['spaceCannonHitsOn']) for value in dice)
            graviton = bool(shooter and 'gls' in shooter.technologies and
                            'gls' not in shooter.exhausted_technologies and
                            any(unit in session.target.units and unit.kind == 'fighter'
                                for unit in active_ships) and
                            any(unit in session.target.units and unit.kind != 'fighter'
                                for unit in active_ships))
            if graviton:
                shooter.exhausted_technologies.add('gls')
            session.cannon_log.append(
                f'{cannon.owner.upper()} PDS in tile {source.system_id}: {dice} → {hits} hit(s)')
            for _ in range(hits):
                candidates = [unit for unit in active_ships if unit in session.target.units]
                if not candidates:
                    break
                if graviton:
                    nonfighters = [unit for unit in candidates if unit.kind != 'fighter']
                    if nonfighters:
                        candidates = nonfighters
                session.space_cannon_events.append({'owner': cannon.owner,
                                                    'candidates': [unit.unit_id for unit in candidates],
                                                    'graviton': graviton})
        return self._finish_space_cannon_rolls(session, active_ships)

    def _finish_space_cannon_rolls(self, session, active_ships):
        if session.space_cannon_events and self.action_cards:
            session.stage = 'space_cannon_response'
            if any(self.action_cards.can_play(player.faction, 'maneuvering_jets', session)
                   for player in self.players):
                return False
        return self.resolve_space_cannon_hits(session)

    def resolve_space_cannon_hits(self, session):
        while session.space_cannon_events:
            event = session.space_cannon_events.pop(0)
            candidates = [unit for unit in session.target.units
                          if unit.unit_id in event['candidates']]
            if not candidates:
                continue
            if session.space_cannon_nes_cancel:
                session.space_cannon_nes_cancel -= 1
                session.cannon_log.append('Non-Euclidean Shielding canceled 1 additional hit.')
                continue
            if event.get('graviton'):
                nonfighters = [unit for unit in candidates if unit.kind != 'fighter']
                if nonfighters:
                    candidates = nonfighters
            target = self.apply_hit(session.target, candidates, session)
            if target in session.target.units and target.damaged and event['owner'] != target.owner:
                session.pending_direct_hits.setdefault(event['owner'], []).append(target.unit_id)
            if target in session.target.units and target.damaged and self.has_tech(target.owner, 'nes'):
                session.space_cannon_nes_cancel = 1
            session.cannon_log.append(
                f'{target.kind.title()} {"damaged" if target.damaged else "destroyed"}')
            if target in session.target.units and target.damaged and event['owner'] != target.owner:
                session.space_cannon_direct_hit = (event['owner'], target.unit_id)
                previous_stage = session.stage
                session.stage = 'space_cannon_direct_hit'
                if (self.action_cards and
                        self.action_cards.can_play(event['owner'], 'direct_hit', session)):
                    return False
                session.stage = previous_stage
                session.space_cannon_direct_hit = None
                session.pending_direct_hits[event['owner']].remove(target.unit_id)
        return True

    def apply_hit(self, tile, candidates, session=None):
        target = sorted(candidates, key=lambda unit: (
            not (unit_profile(unit).get('sustainDamage') and not unit.damaged), unit.unit_id))[0]
        if unit_profile(target).get('sustainDamage') and not target.damaged:
            target.damaged = True
        else:
            self.destroy_unit(tile, target, session)
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
            if not hostile_fleet and (has_friendly or
                                      (self.has_tech(faction, 'det') and
                                       not any(unit.owner != faction for unit in tile.units))):
                options.append(tile)
        return options

    def announce_retreat(self):
        session = self.session
        if not session or session.stage != 'space_combat' or session.combat_needs_resolution:
            raise MovementError('Retreat can only be announced before combat rolls')
        faction = session.player.faction
        if session.retreat_blocked_round == session.combat_round + 1:
            raise MovementError('Your retreat was intercepted for this combat round.')
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
                          unit.kind == 'infantry'}
        bombers = [unit for unit in session.target.units if unit.owner == faction and
                   unit.location.region == Region.SPACE and
                   unit_profile(unit).get('bombardHitsOn')]
        war_sun_present = any(unit.kind == 'warsun' and unit.owner == faction and
                              unit.location.region == Region.SPACE for unit in session.target.units)
        hostile_shield = any(unit.owner != faction and unit.unit_id not in session.disabled_pds and
                             unit.kind == 'pds' and
                             unit_profile(unit).get('planetaryShield') for unit in session.target.units) and \
            not war_sun_present
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
                          unit.location.planet_id == planet.planet_id and unit.kind == 'infantry'
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
        nes_cancel = {}
        for unit_id, planet_id in session.bombard_targets.items():
            if planet_id is None:
                continue
            ship = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
            if ship is None:
                continue
            profile = unit_profile(ship)
            rolls = []
            extra = int(plasma_available)
            plasma_available = False
            for _ in range(int(profile.get('bombardDieCount') or 1) + extra):
                value = self.roll_d10(session)
                planet_owner = session.target.planet_owners.get(planet_id)
                if planet_owner in session.bunker_factions:
                    value -= 4
                rolls.append({'unit_id': unit_id, 'kind': ship.kind, 'planet_id': planet_id,
                              'value': value, 'hit': value >= int(profile['bombardHitsOn'])})
            session.bombard_rolls.extend(rolls)
            hits = sum(roll['hit'] for roll in rolls)
            destroyed = 0
            for _ in range(hits):
                if nes_cancel.get(planet_id, 0):
                    nes_cancel[planet_id] -= 1
                    continue
                defenders = [unit for unit in session.target.units if unit.owner != faction and
                             unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                             unit.kind == 'infantry']
                if not defenders:
                    break
                target = self.apply_hit(session.target, defenders, session)
                if target in session.target.units and target.damaged and \
                        self.has_tech(target.owner, 'nes'):
                    nes_cancel[planet_id] = 1
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
        session.space_combat_resolved = False
        session.skilled_retreat = False
        session.combat_planet_id = None
        session.combat_factions = (session.player.faction,) + self.hostile_space_factions(
            session.target, session.player.faction)
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
            session.space_combat_resolved = True
            self._check_capacity_and_fleet(
                session, [(session.target.position, faction) for faction in session.combat_factions],
                'after_combat')

    def start_ground_combat(self, session, planet_id):
        session.combat_type = 'ground'
        session.combat_planet_id = planet_id
        defenders = tuple(sorted({unit.owner for unit in session.target.units
                                  if unit.owner != session.player.faction and
                                  unit.location.region == Region.PLANET and
                                  unit.location.planet_id == planet_id and
                                  unit.kind == 'infantry'}))
        session.combat_factions = (session.player.faction,) + defenders
        session.combat_round = 0
        session.combat_rolls.clear()
        session.combat_hits.clear()
        session.combat_assignments.clear()
        session.combat_needs_resolution = False
        session.magen_suppressed = None
        for faction in session.combat_factions:
            player = self.faction_player(faction)
            if player and 'md_base' in player.technologies and \
                    'md_base' not in player.exhausted_technologies and not any(
                        unit.owner != faction and unit.kind == 'warsun' and
                        unit.location.region == Region.SPACE for unit in session.target.units) and any(
                        unit.owner == faction and unit.location.planet_id == planet_id and
                        unit_profile(unit).get('planetaryShield') for unit in session.target.units):
                player.exhausted_technologies.add('md_base')
                session.magen_suppressed = next((enemy for enemy in session.combat_factions
                                                 if enemy != faction), None)
                break
        session.combat_rolls.clear()
        session.combat_modifiers.clear()
        session.fighter_combat_modifiers.clear()
        session.stage = 'ground_combat'

    def combat_units(self, session, faction):
        if session.combat_type == 'ground':
            return [unit for unit in session.target.units if unit.owner == faction and
                    unit.location.region == Region.PLANET and
                    unit.location.planet_id == session.combat_planet_id and
                    unit.kind == 'infantry']
        return [unit for unit in session.target.units if unit.owner == faction and
                unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]

    def combat_hit_capacity(self, session, faction):
        total = 0
        for unit in self.combat_units(session, faction):
            sustain = bool(unit_profile(unit).get('sustainDamage'))
            bonus = 2 if sustain and not unit.damaged and self.has_tech(faction, 'nes') else \
                    1 if sustain and not unit.damaged else 0
            total += 1 + bonus
        return total

    def combat_assignment_target(self, session, faction, kind):
        assigned = session.combat_assignments.setdefault(faction, [])
        counts = {unit_id: assigned.count(unit_id) for unit_id in set(assigned)}
        for unit in sorted(self.combat_units(session, faction), key=lambda item: item.unit_id):
            if unit.kind != kind:
                continue
            bonus = 2 if unit_profile(unit).get('sustainDamage') and not unit.damaged and \
                self.has_tech(faction, 'nes') else 1 if unit_profile(unit).get('sustainDamage') and \
                not unit.damaged else 0
            max_hits = 1 + bonus
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
        if (self.has_tech(faction, 'nes') and unit_profile(unit).get('sustainDamage') and
                not unit.damaged and assignments.count(unit.unit_id) == 1 and len(assignments) < required):
            assignments.append(unit.unit_id)

    def combat_assignments_complete(self, session):
        return all(len(session.combat_assignments.get(faction, [])) >=
                   min(session.combat_hits.get(faction, 0), self.combat_hit_capacity(session, faction))
                   for faction in session.combat_factions)

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
            sustained_this_round = set()
            absorbed_extra = set()
            for faction in session.combat_factions:
                for unit_id in session.combat_assignments.get(faction, []):
                    unit = next((unit for unit in session.target.units if unit.unit_id == unit_id), None)
                    if unit is None:
                        continue
                    if unit_id in absorbed_extra:
                        absorbed_extra.remove(unit_id)
                        continue
                    if unit_profile(unit).get('sustainDamage') and not unit.damaged:
                        unit.damaged = True
                        sustained_this_round.add(unit_id)
                        for source in session.hit_sources.get(faction, ()):
                            session.pending_direct_hits.setdefault(source, []).append(unit_id)
                        if self.has_tech(faction, 'nes'):
                            absorbed_extra.add(unit_id)
                        continue
                    self.destroy_unit(session.target, unit, session,
                                      session.hit_sources.get(faction, ()))
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
                for _ in range(int(profile.get('combatDieCount') or 1)):
                    value = self.roll_d10(session)
                    modifier = session.combat_modifiers.get(faction, 0)
                    if unit.kind == 'fighter':
                        modifier += session.fighter_combat_modifiers.get(faction, 0)
                    result = value + modifier
                    rolls.append({'unit_id': unit.unit_id, 'kind': unit.kind, 'value': result,
                                  'natural': value, 'modifier': modifier,
                                  'hit': result >= self.combat_threshold(faction, profile),
                                  'rerolled': False})
            session.combat_rolls[faction] = rolls
            outgoing_hits[faction] = sum(result['hit'] for result in rolls)
        session.munitions_available = set(session.munitions_ready)
        session.munitions_ready.clear()
        session.combat_hits = {faction: sum(hits for shooter, hits in outgoing_hits.items()
                                             if shooter != faction)
                               for faction in session.combat_factions}
        session.hit_sources = {faction: tuple(shooter for shooter, hits in outgoing_hits.items()
                                              if shooter != faction and hits)
                               for faction in session.combat_factions}
        session.combat_needs_resolution = True

    def _finish_combat_round(self, session):
        session.pending_direct_hits.clear()
        session.pending_courageous.clear()
        own_alive = bool(self.combat_units(session, session.player.faction))
        enemies_alive = any(self.combat_units(session, faction)
                            for faction in session.combat_factions if faction != session.player.faction)
        if session.combat_type == 'ground' and (not own_alive or not enemies_alive):
            self.finish_ground_battle(session, own_alive, enemies_alive)
        elif not own_alive or not enemies_alive:
            winner = next((faction for faction in session.combat_factions
                           if self.combat_units(session, faction)), None)
            session.combat_winner = winner
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
        session.stage = 'production'
        return True

    def advance_integrated_production(self, session):
        """Resolve each newly captured planet before normal tactical production."""
        while session.integrated_queue:
            planet_id = session.integrated_queue.pop(0)
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == planet_id)
            if not planet.resources:
                continue
            session.integrated_current = planet_id
            session.production_sites = [(planet_id, 9999)]
            session.production_limit = 9999
            session.production_choices.clear()
            session.production_planets.clear()
            session.trade_goods_to_spend = 0
            session.stage = 'production'
            session.production_checkpoint = SessionCheckpoint.capture(self, session, 'production')
            return True
        session.integrated_current = None
        return self.prepare_production(session)

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
        raw_cost = ceil(sum(self.unit_cost(kind, session.player) * count
                            for kind, count in session.production_choices.items()))
        if session.integrated_current:
            return raw_cost
        sarween = int('st' in session.player.technologies and bool(session.production_choices))
        return max(0, raw_cost - sarween)

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
        if not production_allowed(session.player, kind):
            raise MovementError('Research War Sun before producing it.')
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
        if session.integrated_current:
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == session.integrated_current)
            if self.production_cost(session) > planet.resources:
                raise MovementError('Integrated Economy exceeds this planet’s resource value.')
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
                if kind in ('infantry', 'pds', 'spacedock'):
                    planet_id = session.production_sites[0][0]
                    location = UnitLocation(Region.PLANET, planet_id=planet_id)
                else:
                    location = UnitLocation(Region.SPACE)
                session.target.units.append(Unit(unit_id, kind, session.player.faction,
                                                 session.player.color_code, location,
                                                 profile_id=(upgrade_profile(session.player, kind) or {}).get('id')))
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
                unit.kind == 'infantry' and unit.location.region == Region.TRANSPORT]

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
                         unit.kind == 'infantry']
            if not attackers:
                continue
            defenders = [unit for unit in target.units if unit.owner != player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                         unit.kind == 'infantry']
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
        if 'l4' in session.player.technologies:
            session.defense_log.append('L4 Disruptors prevent space cannon defense during this invasion.')
            return
        plasma_used = set()
        nes_cancel = {}
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
                extra = int(bool(shooter and 'ps' in shooter.technologies and
                                 cannon.owner not in plasma_used))
                if extra:
                    plasma_used.add(cannon.owner)
                dice = [self.roll_d10(session)
                        for _ in range(int(profile.get('spaceCannonDieCount') or 1) + extra)]
                penalty = -1 if 'amd' in session.player.technologies else 0
                hits = sum(value + penalty >= int(profile['spaceCannonHitsOn']) for value in dice)
                session.defense_log.append(
                    f'{cannon.owner.upper()} PDS on {planet_id}: {dice} → {hits} hit(s)')
                for _ in range(hits):
                    if nes_cancel.get(planet_id, 0):
                        nes_cancel[planet_id] -= 1
                        session.defense_log.append('Non-Euclidean Shielding canceled 1 additional hit.')
                        continue
                    attackers = [unit for unit in session.target.units if unit.owner == faction and
                                 unit.location.region == Region.PLANET and unit.location.planet_id == planet_id and
                                 unit.kind == 'infantry']
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
            winner = session.player.faction
        elif defenders_alive and not attackers_alive:
            winner = next((faction for faction in session.combat_factions
                           if faction != session.player.faction and
                           self.combat_units(session, faction)), None)
        else:
            winner = None
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
                         unit.kind == 'infantry']
            attackers = [unit for unit in session.target.units if unit.owner == session.player.faction and
                         unit.location.region == Region.PLANET and unit.location.planet_id == next_planet and
                         unit.kind == 'infantry']
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
        if 'ie' in session.player.technologies and session.captured_planets:
            session.integrated_queue = list(session.captured_planets)
            self.advance_integrated_production(session)
        else:
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
