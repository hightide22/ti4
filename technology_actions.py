"""Action-phase abilities printed on researched technology cards."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from movement import Snapshot, UndoEntry
from technology import technology_catalog
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
        self.end_turn_player = None
        self.predictive_active = False

    def begin_end_turn(self, player):
        bio_stims_target = any(card.exhausted and card.planet.tech_specialties
                               for card in player.planets) or bool(player.exhausted_technologies - {'bs'})
        ready = (('bs' in player.technologies and 'bs' not in player.exhausted_technologies
                  and bio_stims_target) or
                 ('pi' in player.technologies and 'pi' not in player.exhausted_technologies
                  and any(player.command_pools.values())))
        if ready:
            self.end_turn_player = player
        return ready

    def finish_end_turn(self):
        self.end_turn_player = None
        self.predictive_active = False

    def transit_units(self, player):
        if self.transit and self.transit.player is not player:
            return []
        if not self.transit and not self._ready(player, 'td'):
            return []
        moved = self.transit.moved_ids if self.transit else set()
        return [(unit.unit_id, f'Tile {tile.system_id} · {unit.kind.title()}')
                for tile in self.board.values() for unit in tile.units
                if unit.owner == player.faction and unit.kind in ('infantry', 'mech')
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
        if alias in ('bs', 'pi') and self.end_turn_player is player:
            if alias == 'bs' and alias not in player.exhausted_technologies:
                planets = [(f'planet:{card.planet.planet_id}', f'Ready {card.planet.name}')
                           for card in player.planets
                           if card.exhausted and card.planet.tech_specialties]
                technologies = [(f'tech:{other}',
                                 f'Ready {technology_catalog().get(other, {}).get("name", other)}')
                                for other in sorted(player.exhausted_technologies)
                                if other != 'bs' and other in player.technologies]
                return planets + technologies
            if alias == 'pi' and (alias not in player.exhausted_technologies or
                                  self.predictive_active):
                minimum_fleet = max((self.movement.fleet_ship_count(
                    SimpleNamespace(target=tile, player=player)) for tile in self.board.values()),
                    default=0)
                can_reduce_fleet = (player.command_pools['fleet'] - 1 +
                                    (2 if player.faction == 'letnev' else 0) >= minimum_fleet)
                return [(f'{source}:{target}', f'{source.title()} → {target.title()}')
                        for source in ('tactical', 'fleet', 'strategic')
                        for target in ('tactical', 'fleet', 'strategic')
                        if source != target and player.command_pools[source] > 0 and
                        (source != 'fleet' or can_reduce_fleet)]
        if self.transit or self.end_turn_player or alias not in player.technologies or \
                alias in player.exhausted_technologies:
            return []
        if (self.turn.active_player is not player or self.turn.strategy_selection or
                self.turn.command_allocation or self.turn.strategy_resolution or
                self.movement.session):
            return []
        if alias != 'pa' and not self.turn.can_take_action:
            return []
        if alias == 'pa':
            return [(card.planet.planet_id, f'{card.planet.name} · gain 1 trade good')
                    for card in player.planets if card.planet.tech_specialties and not card.exhausted]
        if alias == 'sr':
            return [(unit.unit_id, f'Tile {tile.system_id} · {unit.location.planet_id}')
                    for tile in self.board.values() for unit in tile.units
                    if unit.owner == player.faction and unit.kind == 'spacedock']
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
        if alias == 'bs':
            if target_id.startswith('planet:'):
                card = next(card for card in player.planets
                            if card.planet.planet_id == target_id.removeprefix('planet:'))
                card.exhausted = False
            else:
                player.exhausted_technologies.remove(target_id.removeprefix('tech:'))
            player.exhausted_technologies.add('bs')
            return
        if alias == 'pi':
            source, target = target_id.split(':')
            player.transfer_command(source, target)
            player.exhausted_technologies.add('pi')
            self.predictive_active = True
            return
        if alias == 'pa':
            card = next(card for card in player.planets
                        if card.planet.planet_id == target_id)
            card.exhausted = True
            player.trade_goods += 1
            return
        if alias == 'sr':
            tile, dock = next((tile, unit) for tile in self.board.values()
                              for unit in tile.units if unit.unit_id == target_id)
            self.movement.start_sling_relay(player, tile, dock)
            return
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
