"""Action-phase abilities printed on researched technology cards."""
from __future__ import annotations

from dataclasses import dataclass, field
from movement import Snapshot, UndoEntry
from units import Region, UNIT_TYPES, UnitLocation, unit_profile


@dataclass
class TransitResolution:
    player: object
    snapshot: Snapshot
    selected_unit: str | None = None
    moved_ids: set[str] = field(default_factory=set)


class TechnologyActions:
    def __init__(self, board, movement, turn):
        self.board = board
        self.movement = movement
        self.turn = turn
        self.transit: TransitResolution | None = None

    def transit_units(self, player):
        if self.transit and self.transit.player is not player:
            return []
        if not self.transit and not self._ready(player, 'td'):
            return []
        moved = self.transit.moved_ids if self.transit else set()
        return [(unit.unit_id, f'Tile {tile.system_id} · {unit.kind.title()}')
                for tile in self.board.values() for unit in tile.units
                if unit.owner == player.faction and unit.kind == 'infantry'
                and unit.unit_id not in moved]

    def transit_planets(self, player):
        return [(card.planet.planet_id, card.planet.name) for card in player.planets]

    def _ready(self, player, alias):
        return bool(alias in player.technologies and alias not in player.exhausted_technologies and
                    self.turn.active_player is player and not self.turn.strategy_selection and
                    not self.turn.command_allocation and not self.turn.strategy_resolution and
                    not self.movement.session and self.turn.can_take_action)

    def select_transit_unit(self, player, unit_id):
        if unit_id not in {key for key, _ in self.transit_units(player)}:
            raise ValueError('This ground force cannot use Transit Diodes.')
        if self.transit is None:
            self.transit = TransitResolution(player, Snapshot.capture(
                self.board, player, self.turn.players))
        if len(self.transit.moved_ids) >= 4:
            raise ValueError('Transit Diodes can move at most 4 ground forces.')
        self.transit.selected_unit = unit_id

    def place_transit_unit(self, player, planet_id):
        state = self.transit
        if not state or state.player is not player or not state.selected_unit or \
                planet_id not in {key for key, _ in self.transit_planets(player)}:
            raise ValueError('Choose a controlled destination planet.')
        source, unit = next((tile, unit) for tile in self.board.values()
                            for unit in tile.units if unit.unit_id == state.selected_unit)
        target = next(tile for tile in self.board.values()
                      if any(planet.planet_id == planet_id for planet in tile.planets))
        source.units.remove(unit)
        unit.location = UnitLocation(Region.PLANET, planet_id=planet_id)
        target.units.append(unit)
        state.moved_ids.add(unit.unit_id)
        state.selected_unit = None

    def finish_transit(self, player):
        state = self.transit
        if not state or state.player is not player or not state.moved_ids:
            raise ValueError('Move at least one ground force.')
        player.exhausted_technologies.add('td')
        self.movement.history.append(UndoEntry(state.snapshot))
        self.transit = None

    def cancel_transit(self):
        if self.transit:
            self.transit.snapshot.restore()
            self.transit = None

    def targets(self, player, alias):
        if self.transit or alias not in player.technologies or \
                alias in player.exhausted_technologies:
            return []
        if (self.turn.active_player is not player or self.turn.strategy_selection or
                self.turn.command_allocation or self.turn.strategy_resolution or
                self.movement.session):
            return []
        if not self.turn.can_take_action:
            return []
        if alias == 'x89_base':
            return [(planet.planet_id, f'Tile {tile.system_id} · {planet.name}')
                    for tile in self.board.values() if any(
                        unit.owner == player.faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] and unit_profile(unit).get('bombardHitsOn')
                        for unit in tile.units)
                    for planet in tile.planets if any(
                        unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                        unit.location.planet_id == planet.planet_id for unit in tile.units)]
        if alias == 'pm' and player.command_pools['strategic']:
            return [(other.faction, f'{other.name} · give 2 trade goods')
                    for other in self.turn.players if other is not player]
        return []

    def use(self, player, alias, target_id):
        if target_id not in {key for key, _ in self.targets(player, alias)}:
            raise ValueError('This technology or target is unavailable.')
        snapshot = Snapshot.capture(self.board, player, self.turn.players)
        if alias == 'x89_base':
            tile = next(tile for tile in self.board.values()
                        if any(planet.planet_id == target_id for planet in tile.planets))
            for unit in list(tile.units):
                if (unit.kind == 'infantry' and unit.location.region == Region.PLANET and
                        unit.location.planet_id == target_id):
                    self.movement.destroy_unit(tile, unit)
        elif alias == 'pm':
            other = next(other for other in self.turn.players
                         if other.faction == target_id)
            player.command_pools['strategic'] -= 1
            player.trade_goods += 4
            other.trade_goods += 2
        player.exhausted_technologies.add(alias)
        self.movement.history.append(UndoEntry(snapshot))
