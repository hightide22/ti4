from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil

from player import PlanetCard
from units import Region, Unit, UnitLocation, UNIT_TYPES, unit_profile, unit_profiles


class MovementError(ValueError):
    pass


def capital_ship(unit):
    return UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter'


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

    @classmethod
    def capture(cls, board, player, players):
        all_players = players or [player]
        cards = [(other, list(other.planets)) for other in all_players]
        return cls(player, dict(player.command_pools),
                   [(tile, list(tile.units), set(tile.command_tokens), dict(tile.planet_owners)) for tile in board.values()],
                   [(unit, unit.location) for tile in board.values() for unit in tile.units],
                   cards,
                   [(card, card.exhausted) for _, player_cards in cards for card in player_cards],
                   [(other, other.trade_goods, other.commodities) for other in all_players])

    def restore(self):
        self.player.command_pools.clear()
        self.player.command_pools.update(self.pools)
        for tile, units, tokens, owners in self.tiles:
            tile.units[:] = units
            tile.command_tokens.clear()
            tile.command_tokens.update(tokens)
            tile.planet_owners.clear()
            tile.planet_owners.update(owners)
        for unit, location in self.locations:
            unit.location = location
        for player, cards in self.planet_cards:
            player.planets[:] = cards
        for card, exhausted in self.card_states:
            card.exhausted = exhausted
        for player, trade_goods, commodities in self.currencies:
            player.trade_goods = trade_goods
            player.commodities = commodities


@dataclass
class Session:
    player: object
    target: object
    sources: dict
    snapshot: Snapshot
    selected: set[str] = field(default_factory=set)
    stage: str = 'movement'
    landings: dict[str, str | None] = field(default_factory=dict)
    cannon_log: list[str] = field(default_factory=list)
    outcome: str = ''
    overflow_required: int = 0
    overflow_selected: set[str] = field(default_factory=set)
    overflow_next_stage: str | None = None
    production_limit: int = 0
    production_sites: list[tuple[str, int]] = field(default_factory=list)
    production_choices: dict[str, int] = field(default_factory=dict)
    production_planets: set[str] = field(default_factory=set)
    trade_goods_to_spend: int = 0

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
        return len(self.passengers(source)) + len(self.carried(source)), sum(u.capacity for u in self.ships(source))

    def toggle(self, unit_id):
        unit = self.choices.get(unit_id)
        if unit is None:
            raise MovementError('This unit cannot move to the activated system')
        source = next(s for s in self.sources.values() if unit in s.ships or unit in s.passengers)
        trial = self.selected ^ {unit_id}
        ships = [u for u in source.ships if u.unit_id in trial]
        cargo = [u for u in source.passengers if u.unit_id in trial]
        used = len(cargo) + len(self.carried(source, ships))
        capacity = sum(u.capacity for u in ships)
        if used > capacity:
            raise MovementError('Not enough capacity in this source system. Select a transport or remove passengers first.')
        self.selected = trial


class MovementController:
    def __init__(self, board, players=()):
        self.board = board
        self.players = list(players)
        self.session = None
        self.history = []

    def neighbors(self, tile):
        neighbors = list(self.board.neighbors(tile.position))
        if tile.wormholes:
            neighbors.extend(other for other in self.board.values()
                             if other is not tile and set(tile.wormholes) & set(other.wormholes)
                             and other not in neighbors)
        return neighbors

    def route(self, origin, target, unit, player):
        if player.faction in origin.command_tokens or origin is target or unit.move_value <= 0:
            return None
        budget = min(unit.move_value, 1) if 'nebula' in origin.anomalies else unit.move_value
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
                    path = self.route(origin, target, unit, player)
                    if path:
                        ships.append(unit)
                        routes[unit.unit_id] = path
            if ships:
                passengers = [u for u in origin.units if u.owner == player.faction and
                              ((u.kind == 'fighter' and u.location.region == Region.SPACE) or
                               (u.kind in ('infantry', 'mech') and u.location.region == Region.PLANET))]
                sources[origin.position] = Source(origin, ships, passengers, routes)
        snapshot = Snapshot.capture(self.board, player, self.players or [player])
        player.command_pools['tactical'] -= 1
        target.command_tokens.add(player.faction)
        self.session = Session(player, target, sources, snapshot)
        return self.session

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
            if len(passengers) + len(carried) > sum(u.capacity for u in ships):
                raise MovementError('Transport capacity exceeded')
            remaining = {ship.unit_id: ship.capacity - sum(u.location.carrier_id == ship.unit_id for u in carried) for ship in ships}
            transfers.extend((source.tile, ship, UnitLocation(Region.SPACE)) for ship in ships)
            transfers.extend((source.tile, u, u.location) for u in carried)
            for unit in passengers:
                carrier_id = next((key for key, slots in remaining.items() if slots > 0), None)
                if carrier_id is None:
                    raise MovementError('Transport capacity exceeded')
                remaining[carrier_id] -= 1
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
        self._check_fleet_limit(session, 'invasion')
        return len(transfers)

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
        elif next_stage == 'invasion':
            session.stage = 'invasion'
        else:
            session.stage = 'complete'
            self.finish()

    def toggle_overflow_ship(self, unit_id):
        session = self.session
        if not session or session.stage != 'fleet_overflow':
            raise MovementError('Fleet supply is not being checked')
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
            raise MovementError('Fleet supply is not being checked')
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
        if next_stage == 'invasion':
            carried_ids = {unit.unit_id for unit in self.landing_forces(session)}
            session.landings = {unit_id: planet_id for unit_id, planet_id in session.landings.items()
                                if unit_id in carried_ids}
            session.stage = 'invasion'
        else:
            session.stage = 'complete'
            self.finish()

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
            self._check_fleet_limit(session, 'complete')
            return False
        session.production_sites = sites
        session.production_limit = sum(value for _, value in sites)
        session.production_choices.clear()
        session.production_planets.clear()
        session.trade_goods_to_spend = 0
        session.stage = 'production'
        return True

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
        self._check_fleet_limit(session, 'complete')

    def skip_production(self):
        session = self.session
        if not session or session.stage != 'production':
            raise MovementError('Production is not active')
        for card in session.player.planets:
            if card.planet.planet_id in session.production_planets:
                card.exhausted = False
        session.stage = 'complete'
        self._check_fleet_limit(session, 'complete')

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
        target, player = session.target, session.player
        landed = []
        for unit in self.landing_forces(session):
            planet_id = session.landings.get(unit.unit_id)
            if planet_id is None:
                continue
            unit.location = UnitLocation(Region.PLANET, planet_id=planet_id)
            landed.append((unit, planet_id))
        captured = []
        for _, planet_id in landed:
            if target.planet_owners.get(planet_id) == player.faction:
                continue
            if any(u.location.region == Region.PLANET and u.location.planet_id == planet_id and
                   u.owner != player.faction and u.kind in ('infantry', 'mech') for u in target.units):
                continue
            target.planet_owners[planet_id] = player.faction
            for unit in list(target.units):
                if unit.owner != player.faction and unit.location.region == Region.PLANET and \
                        unit.location.planet_id == planet_id and unit.kind in ('pds', 'spacedock'):
                    target.units.remove(unit)
            for other in self.players:
                other.planets[:] = [card for card in other.planets if card.planet.planet_id != planet_id]
            planet = next(planet for planet in target.planets if planet.planet_id == planet_id)
            player.planets.append(PlanetCard(planet, exhausted=True))
            captured.append(planet.name)
        if not landed:
            session.outcome = 'No ground forces landed. No planets changed control.'
        else:
            session.outcome = ('Landed forces on ' + ', '.join(next(p.name for p in target.planets if p.planet_id == pid)
                               for _, pid in landed) + '. ' +
                               ('Captured: ' + ', '.join(captured) + '.' if captured else 'Control did not change.'))
        self.prepare_production(session)

    def finish(self):
        if self.session and self.session.stage == 'complete':
            self.history.append(self.session.snapshot)
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
        if self.cancel():
            return True
        if self.history:
            self.history.pop().restore()
            return True
        return False
