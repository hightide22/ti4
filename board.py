from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from copy import deepcopy
from typing import ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from units import Unit
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "assets/source/asyncti4/src/main/resources"
DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
Position = tuple[int, int]


@dataclass(frozen=True)
class Planet:
    planet_id: str
    name: str
    planet_type: str | None
    resources: int
    influence: int
    tech_specialties: tuple[str, ...] = ()
    faction_homeworld: str | None = None
    center: tuple[float, float] = (172.5, 149.5)
    radius: float = 60.0

    @classmethod
    def from_data(cls, data: dict) -> Planet:
        return cls(data["id"], data["name"], data.get("planetType"), data.get("resources", 0), data.get("influence", 0), tuple(data.get("techSpecialties") or ()), data.get("factionHomeworld"), (data.get("positionInTile", {}).get("x", 172.5), data.get("positionInTile", {}).get("y", 149.5)), float(data.get("radius") or 60))


@dataclass(frozen=True)
class TileDefinition:
    number: int
    system_id: str
    name: str
    image_path: Path
    planets: tuple[Planet, ...]
    wormholes: tuple[str, ...]
    anomalies: tuple[str, ...]
    is_home_system: bool
    source: str
    system: dict = field(repr=False)


@dataclass(eq=False)
class Tile:
    definition: TileDefinition
    position: Position
    player: str | None = None
    color: tuple[int, int, int] = (100, 207, 224)
    kind: ClassVar[str] = "system"
    units: list[Unit] = field(default_factory=list)
    command_tokens: set[str] = field(default_factory=set)
    planet_owners: dict[str, str] = field(default_factory=dict)

    @property
    def number(self) -> int:
        return self.definition.number

    @property
    def system_id(self) -> str:
        return self.definition.system_id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def image_path(self) -> Path:
        return self.definition.image_path

    @property
    def planets(self) -> tuple[Planet, ...]:
        return self.definition.planets

    @property
    def wormholes(self) -> tuple[str, ...]:
        return self.definition.wormholes

    @property
    def anomalies(self) -> tuple[str, ...]:
        return self.definition.anomalies


class PlanetaryTile(Tile):
    kind = "planetary"


class HomeSystemTile(PlanetaryTile):
    kind = "home_system"


class AnomalyTile(Tile):
    kind = "anomaly"


class EmptySpaceTile(Tile):
    kind = "empty_space"


class TileCatalog:
    def __init__(self, by_number: dict[int, TileDefinition]):
        self.by_number = by_number

    def __getitem__(self, number: int | str) -> TileDefinition:
        return self.by_number[int(number)]

    @classmethod
    def from_resources(cls, resources: Path = RESOURCES, sources: set[str] | None = None) -> TileCatalog:
        by_number = {}
        anomaly_flags = {"isAsteroidField": "asteroid_field", "isNebula": "nebula", "isSupernova": "supernova", "isGravityRift": "gravity_rift"}
        for path in sorted((resources / "systems").glob("*.json")):
            system = json.loads(path.read_text(encoding="utf-8"))
            system_id = str(system.get("id", ""))
            if not system_id.isdecimal() or (sources is not None and system.get("source") not in sources):
                continue
            number = int(system_id)
            if number in by_number:
                raise ValueError(f"Duplicate tile number: {number}")
            image_path = resources / "tiles" / system["imagePath"]
            if not image_path.is_file():
                raise FileNotFoundError(image_path)
            planets = tuple(Planet.from_data(json.loads((resources / "planets" / f"{planet_id}.json").read_text(encoding="utf-8"))) for planet_id in system.get("planets", []))
            by_number[number] = TileDefinition(number, system_id, system["name"], image_path, planets, tuple(system.get("wormholes") or ()), tuple(name for flag, name in anomaly_flags.items() if system.get(flag)), system.get("tileBack") == "green", system.get("source", "unknown"), deepcopy(system))
        return cls(by_number)

    def create(self, number: int | str, position: Position, player: str | None = None, color=(100, 207, 224)) -> Tile:
        definition = self[number]
        if definition.is_home_system:
            tile_class = HomeSystemTile
        elif definition.anomalies:
            tile_class = AnomalyTile
        elif definition.planets:
            tile_class = PlanetaryTile
        else:
            tile_class = EmptySpaceTile
        return tile_class(definition, position, player, tuple(color))

    def from_entry(self, entry: dict) -> Tile:
        return self.create(entry["id"], (entry["q"], entry["r"]), entry.get("player"), entry.get("color", (100, 207, 224)))


TILES = TileCatalog.from_resources(sources={"base"})


class Board(Mapping[Position, Tile]):
    def __init__(self, tiles=()):
        self._tiles: dict[Position, Tile] = {}
        for tile in tiles:
            self.add(tile)

    def __getitem__(self, position: Position) -> Tile:
        return self._tiles[position]

    def __iter__(self) -> Iterator[Position]:
        return iter(self._tiles)

    def __len__(self) -> int:
        return len(self._tiles)

    def add(self, tile: Tile) -> None:
        if tile.position in self._tiles:
            raise ValueError(f"Position already occupied: {tile.position}")
        self._tiles[tile.position] = tile

    def remove(self, position: Position) -> Tile:
        return self._tiles.pop(position)

    def move(self, position: Position, destination: Position) -> None:
        tile = self._tiles[position]
        if position == destination:
            return
        if destination in self._tiles:
            raise ValueError(f"Position already occupied: {destination}")
        del self._tiles[position]
        tile.position = destination
        self._tiles[destination] = tile

    def neighbors(self, position: Position) -> list[Tile]:
        q, r = position
        return [self._tiles[(q + dq, r + dr)] for dq, dr in DIRECTIONS if (q + dq, r + dr) in self._tiles]

    @property
    def home_tiles(self) -> list[Tile]:
        return [tile for tile in self.values() if tile.player is not None]


def faction_unit_color(faction: dict) -> str:
    """Pick the first faction-preferred component color that has the core sprites."""
    from units import UNIT_TYPES

    sprite_names = {unit['sprite'] for unit in UNIT_TYPES.values()}
    for color in faction.get('preferredColours', ()):
        if all((RESOURCES / 'units' / f'{color}_{sprite}.png').is_file()
               for sprite in sprite_names):
            return color
    raise FileNotFoundError(f"No complete unit color set for {faction['alias']}")


def load_board(map_path: Path | str = ROOT / "maps/three_player.json",
               factions: tuple[str, ...] | list[str] | None = None) -> tuple[dict, Board]:
    config = json.loads(Path(map_path).read_text(encoding="utf-8"))
    if factions is not None:
        faction_data = {f['alias']: f for f in json.loads(
            (RESOURCES / 'data/factions/base.json').read_text(encoding='utf-8'))}
        slot_positions = config.get('player_slots')
        if slot_positions:
            by_position = {(entry['q'], entry['r']): entry for entry in config['tiles']}
            slots = [by_position[tuple(position)] for position in slot_positions]
        else:
            slots = [entry for entry in config['tiles'] if entry.get('faction')]
        if len(factions) != len(slots):
            raise ValueError(f"This map requires exactly {len(slots)} factions")
        if len(set(factions)) != len(factions):
            raise ValueError('Each player must choose a different faction')
        colors = {
            'sol': (106, 183, 255), 'jolnar': (185, 148, 248),
            'letnev': (219, 111, 132), 'hacan': (244, 183, 87),
            'arborec': (106, 190, 131), 'ghost': (112, 190, 215),
            'l1z1x': (178, 116, 132), 'muaat': (231, 121, 75),
            'saar': (208, 188, 91),
        }
        for entry, alias in zip(slots, factions):
            faction = faction_data.get(alias)
            if faction is None:
                raise ValueError(f'Unknown faction: {alias}')
            entry['id'] = faction['homeSystem']
            entry['faction'] = alias
            entry['player'] = f"Player {slots.index(entry) + 1} · {faction['factionName']}"
            entry['color'] = colors.get(alias, (100, 207, 224))
            entry['unit_color'] = faction_unit_color(faction)
            if alias == 'ghost':
                # Tile 17 is the Creuss Gate; their planet-bearing home system
                # (tile 51) is placed beside it in the play area.
                entry['ghost_home_label'] = entry.get('player')
                entry['player'] = None
    board = Board(TILES.from_entry(entry) for entry in config["tiles"])
    if not board:
        raise ValueError("Map must contain at least one tile")
    for entry in config['tiles']:
        if entry.get('faction') != 'ghost':
            continue
        gate = board[(entry['q'], entry['r'])]
        candidates = [(gate.position[0] + dq, gate.position[1] + dr)
                      for dq, dr in DIRECTIONS
                      if (gate.position[0] + dq, gate.position[1] + dr) not in board]
        if not candidates:
            # Expand the map edge if the home position is fully surrounded.
            candidates = [(gate.position[0] + 2 * dq, gate.position[1] + 2 * dr)
                          for dq, dr in DIRECTIONS
                          if (gate.position[0] + 2 * dq, gate.position[1] + 2 * dr) not in board]
        if not candidates:
            raise ValueError('Could not place the Creuss home system outside the galaxy')
        # Keep the Creuss home system connected to the map only through the
        # Gate whenever possible. The fixed starter map can have several
        # otherwise-empty positions around the Gate; choosing the most
        # outward-looking one alone can accidentally make tile 51 an ordinary
        # neighbor of a second, passable system.
        def extra_passable_neighbors(position):
            count = 0
            for neighbor in board.neighbors(position):
                if neighbor is gate:
                    continue
                if 'supernova' in neighbor.anomalies or 'gravity_rift' in neighbor.anomalies:
                    continue
                count += 1
            return count

        home_pos = min(
            candidates,
            key=lambda pos: (
                extra_passable_neighbors(pos),
                -(pos[0] * gate.position[0] + pos[1] * gate.position[1]),
            ),
        )
        board.add(TILES.create(51, home_pos, entry.get('ghost_home_label'),
                               entry.get('color', (112, 190, 215))))
        entry['ghost_home_position'] = home_pos
        entry['player'] = None
    from units import setup_starting_fleets
    setup_starting_fleets(board, config)
    return config, board
