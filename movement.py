from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from player import PlanetCard
from units import Region, UnitLocation, UNIT_TYPES


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

    @classmethod
    def capture(cls, board, player, players):
        return cls(player, dict(player.command_pools),
                   [(tile, list(tile.units), set(tile.command_tokens), dict(tile.planet_owners)) for tile in board.values()],
                   [(unit, unit.location) for tile in board.values() for unit in tile.units],
                   [(other, list(other.planets)) for other in players])

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
        capital_count = sum(capital_ship(u) and u.owner == session.player.faction for u in session.target.units)
        capital_count += sum(len(session.ships(s)) for s in session.sources.values())
        if capital_count > session.player.command_pools['fleet']:
            raise MovementError('The selected fleet exceeds your fleet reserve limit')
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
        session.stage = 'invasion'
        session.cannon_log = []
        session.landings = ({unit.unit_id: None for unit in self.landing_forces(session)}
                            if session.target.planets else {})
        return len(transfers)

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
        session.stage = 'complete'

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
