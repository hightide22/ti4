"""Action-phase abilities printed on researched technology cards."""
from __future__ import annotations

from dataclasses import dataclass, field
from movement import Snapshot, UndoEntry
from units import Region, UNIT_TYPES, Unit, UnitLocation, unit_profile
from unit_supply import can_place_infantry, can_transfer_infantry, unit_supply_remaining
from technology import upgrade_profile


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
        self.mageon_owner: str | None = None

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
                self.board, player, self.turn.players,
                exploration=self.movement.exploration))
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
        destination = UnitLocation(Region.PLANET, planet_id=planet_id)
        if not can_transfer_infantry(
                player, self.board, [(source, unit, target, destination)]):
            raise ValueError('Infantry tokens need a plastic infantry anchor on the destination planet.')
        source.units.remove(unit)
        unit.location = destination
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
                (alias != 'pa' and alias in player.exhausted_technologies):
            return []
        if (self.turn.active_player is not player or self.turn.strategy_selection or
                self.turn.command_allocation or self.turn.strategy_resolution or
                self.movement.session):
            return []
        if alias == 'pa':
            exploration = self.movement.exploration
            return [(card.planet.planet_id, card.planet.name) for card in player.planets
                    if (exploration.planet_specialties(card.planet) if exploration else
                        card.planet.tech_specialties) and not card.exhausted and any(
                        tile.planet_owners.get(card.planet.planet_id) == player.faction
                        for tile in self.board.values())]
        if alias == 'bs' and self.turn.action_used:
            exploration = self.movement.exploration
            planets = [(f'planet:{card.planet.planet_id}', f'Ready {card.planet.name}')
                       for card in player.planets if card.exhausted and
                       (exploration.planet_specialties(card.planet) if exploration else
                        card.planet.tech_specialties)]
            techs = [(f'tech:{key}', f'Ready {key}') for key in sorted(
                player.exhausted_technologies & player.technologies - {'bs'})]
            return planets + techs
        if alias == 'pi' and self.turn.action_used:
            current = tuple(player.command_pools[key]
                            for key in ('tactical', 'fleet', 'strategic'))
            total = sum(current)
            min_fleet = max((sum(unit.owner == player.faction and
                                 unit.location.region == Region.SPACE and
                                 UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter'
                                 for unit in tile.units) for tile in self.board.values()), default=0)
            allocations = ((tactical, fleet, total - tactical - fleet)
                           for tactical in range(total + 1)
                           for fleet in range(total - tactical + 1))
            alternatives = sorted((allocation for allocation in allocations
                                   if allocation != current and allocation[1] >= min_fleet),
                                  key=lambda allocation: (sum(abs(new - old) for new, old
                                                              in zip(allocation, current)), allocation))
            return [(f'{tactical}:{fleet}:{strategic}',
                     f'Tactic {tactical} · Fleet {fleet} · Strategy {strategic}')
                    for tactical, fleet, strategic in alternatives]
        if not self.turn.can_take_action:
            return []
        if alias == 'vtx':
            if player.faction != 'cabal':
                return []
            from pok_factions import cabal_can_capture_from
            dock_systems = {neighbor.position
                            for dock_tile in self.board.values()
                            if any(unit.owner == player.faction and unit.kind == 'spacedock'
                                   for unit in dock_tile.units)
                            for neighbor in self.movement.neighbors(dock_tile, player.faction)}
            options = []
            for tile in self.board.values():
                if tile.position not in dock_systems:
                    continue
                for unit in tile.units:
                    if unit.owner == player.faction or unit.kind in ('pds', 'spacedock'):
                        continue
                    profile = unit_profile(unit)
                    if profile.get('cost') is None or not cabal_can_capture_from(
                            player, unit.owner, self.board):
                        continue
                    owner = next((candidate for candidate in self.turn.players
                                  if candidate.faction == unit.owner), None)
                    if owner is None:
                        continue
                    remaining = unit_supply_remaining(owner, self.board, unit.kind)
                    captured = player.captured_unit_sources.get((unit.kind, owner.faction), 0)
                    if remaining is not None and remaining - captured <= 0:
                        continue
                    options.append((unit.unit_id,
                                    f'{owner.faction.upper()} · Tile {tile.system_id} · '
                                    f'{unit.kind.title()}'))
            return sorted(options, key=lambda value: (value[1], value[0]))
        if alias == 'lgf':
            if player.faction != 'winnu':
                return []
            mecatol = next((tile for tile in self.board.values()
                            if any(planet.planet_id == 'mr' for planet in tile.planets)), None)
            if mecatol and mecatol.planet_owners.get('mr') != player.faction:
                return [('no-effect', 'Exhaust · no effect')]
            if (mecatol and mecatol.planet_owners.get('mr') == player.faction and
                    can_place_infantry(player, self.board, mecatol,
                                       UnitLocation(Region.PLANET, planet_id='mr'))):
                return [('mr', 'Mecatol Rex · place 1 infantry')]
            return []
        if alias == 'mi':
            from action_cards import ActionCardController
            if self.mageon_owner is None:
                return [(f'owner:{other.faction}', f'{other.name} · view action cards')
                        for other in self.turn.players
                        if other is not player and other.action_cards]
            owner = next((other for other in self.turn.players
                          if other.faction == self.mageon_owner and other is not player), None)
            if owner is None or not owner.action_cards:
                self.mageon_owner = None
                return []
            return [('back', 'Choose another player')] + [
                (f'card:{index}', ActionCardController.name_for(card))
                for index, card in enumerate(owner.action_cards)]
        if alias == 'sr':
            return [(unit.unit_id, f'Tile {tile.system_id} · Space Dock')
                    for tile in self.board.values() for unit in tile.units
                    if unit.owner == player.faction and unit.kind == 'spacedock' and
                    not any(other.owner != player.faction and
                            other.location.region == Region.SPACE and
                            UNIT_TYPES[other.kind]['ship'] for other in tile.units)]
        if alias == 'pm' and player.command_pools['strategic']:
            return [(other.faction, f'{other.name} · give 2 trade goods')
                    for other in self.turn.players if other is not player]
        return []

    def use(self, player, alias, target_id):
        if target_id not in {key for key, _ in self.targets(player, alias)}:
            raise ValueError('This technology or target is unavailable.')
        if alias == 'mi' and target_id.startswith('owner:'):
            self.mageon_owner = target_id.partition(':')[2]
            return False
        if alias == 'mi' and target_id == 'back':
            self.mageon_owner = None
            return False
        snapshot = Snapshot.capture(self.board, player, self.turn.players,
                                    exploration=self.movement.exploration)
        if alias == 'vtx':
            from pok_factions import cabal_capture_unit
            target = next(unit for tile in self.board.values() for unit in tile.units
                          if unit.unit_id == target_id)
            cabal_capture_unit(player, target.kind, target.owner)
            player.exhausted_technologies.add('vtx')
            # The app records undoable actions from their history entry. Marking
            # here too would spend both Fleet Logistics actions for one use.
            self.movement.history.append(UndoEntry(snapshot))
            return
        if alias == 'lgf':
            if target_id == 'no-effect':
                player.exhausted_technologies.add('lgf')
                self.movement.history.append(UndoEntry(snapshot))
                return
            from uuid import uuid4
            tile = next(tile for tile in self.board.values()
                        if any(planet.planet_id == 'mr' for planet in tile.planets))
            tile.units.append(Unit(f'winnu-lazax-gate-{uuid4().hex}', 'infantry',
                                   player.faction, player.color_code,
                                   UnitLocation(Region.PLANET, planet_id='mr'),
                                   profile_id=(upgrade_profile(player, 'infantry') or {}).get('id')))
            player.exhausted_technologies.add('lgf')
            self.movement.history.append(UndoEntry(snapshot))
            return
        if alias == 'mi':
            owner = next(other for other in self.turn.players
                         if other.faction == self.mageon_owner)
            index = int(target_id.partition(':')[2])
            player.action_cards.append(owner.action_cards.pop(int(index)))
            player.exhausted_technologies.add('mi')
            self.mageon_owner = None
            self.movement.history.append(UndoEntry(snapshot))
            return True
        if alias == 'pa':
            card = next(card for card in player.planets if card.planet.planet_id == target_id)
            card.exhausted = True
            player.trade_goods += 1
            self.movement.resolve_pillage(player)
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            return
        if alias == 'bs':
            category, _, key = target_id.partition(':')
            if category == 'planet':
                next(card for card in player.planets if card.planet.planet_id == key).exhausted = False
            else:
                player.exhausted_technologies.remove(key)
            player.exhausted_technologies.add('bs')
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            return
        if alias == 'pi':
            tactical, fleet, strategic = (int(value) for value in target_id.split(':'))
            player.command_pools.update(tactical=tactical, fleet=fleet,
                                        strategic=strategic)
            player.exhausted_technologies.add('pi')
            self.movement.history.append(UndoEntry(snapshot, counts_as_action=False))
            return
        if alias == 'sr':
            tile, dock = next((tile, unit) for tile in self.board.values()
                              for unit in tile.units if unit.unit_id == target_id)
            session = self.movement.start_strategy_production(player, tile, dock)
            session.sling_relay = True
            session.production_sites = [(dock.location.planet_id, 1)]
            session.production_limit = 1
            player.exhausted_technologies.add('sr')
            self.turn.mark_action_completed()
            return
        if alias == 'pm':
            other = next(other for other in self.turn.players
                         if other.faction == target_id)
            player.command_pools['strategic'] -= 1
            player.trade_goods += 4
            other.trade_goods += 2
            self.movement.resolve_pillage(player)
            self.movement.resolve_pillage(other)
        player.exhausted_technologies.add(alias)
        self.movement.history.append(UndoEntry(snapshot))
