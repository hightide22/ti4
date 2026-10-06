from __future__ import annotations

import json
import math
from collections import defaultdict
from functools import lru_cache

from PIL import Image
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from board import RESOURCES, Tile


class Region(str, Enum):
    SPACE = "space"
    PLANET = "planet"
    TRANSPORT = "transport"


@dataclass(frozen=True)
class UnitLocation:
    region: Region
    planet_id: str | None = None
    carrier_id: str | None = None

    def __post_init__(self):
        if self.region == Region.PLANET and not self.planet_id:
            raise ValueError("Ground units need a planet")
        if self.region == Region.TRANSPORT and not self.carrier_id:
            raise ValueError("Transported units need a carrier")
        if self.region != Region.PLANET and self.planet_id is not None:
            raise ValueError("Only ground units can have a planet location")
        if self.region != Region.TRANSPORT and self.carrier_id is not None:
            raise ValueError("Only transported units can have a carrier location")


@dataclass(eq=False)
class Unit:
    unit_id: str
    kind: str
    owner: str
    color_code: str
    location: UnitLocation
    damaged: bool = False
    profile_id: str | None = None

    @property
    def move_value(self):
        return unit_profile(self).get('moveValue', 0)

    @property
    def capacity(self):
        return unit_profile(self).get('capacityValue', 0)

    @property
    def image_path(self) -> Path:
        return RESOURCES / "units" / f"{self.color_code}_{UNIT_TYPES[self.kind]['sprite']}.png"

    def __post_init__(self):
        if self.kind not in UNIT_TYPES:
            raise ValueError(f"Unknown unit: {self.kind}")
        ship = UNIT_TYPES[self.kind]["ship"]
        if ship and self.location.region != Region.SPACE and not (self.kind == 'fighter' and self.location.region == Region.TRANSPORT):
            raise ValueError("Ships must be in space")
        if not ship and self.location.region == Region.SPACE:
            raise ValueError("Ground units must be on a planet or a transport")
        if self.kind in {"pds", "spacedock"} and self.location.region != Region.PLANET:
            raise ValueError("Structures must be on a planet")
        if not self.image_path.is_file():
            raise FileNotFoundError(self.image_path)


UNIT_TYPES = {
    "carrier": {"sprite": "cv", "name": "Carrier", "ship": True, "size": 54.05},
    "cruiser": {"sprite": "ca", "name": "Cruiser", "ship": True, "size": 55.2},
    "destroyer": {"sprite": "dd", "name": "Destroyer", "ship": True, "size": 49.45},
    "dreadnought": {"sprite": "dn", "name": "Dreadnought", "ship": True, "size": 73.6},
    "fighter": {"sprite": "ff", "name": "Fighter", "ship": True, "size": 35.65},
    "flagship": {"sprite": "fs", "name": "Flagship", "ship": True, "size": 80.5},
    "warsun": {"sprite": "ws", "name": "War sun", "ship": True, "size": 77.05},
    "infantry": {"sprite": "gf", "name": "Infantry", "ship": False, "size": 37},
    "mech": {"sprite": "mf", "name": "Mech", "ship": False, "size": 42},
    "pds": {"sprite": "pd", "name": "PDS", "ship": False, "size": 38},
    "spacedock": {"sprite": "sd", "name": "Space dock", "ship": False, "size": 42},
}
STARTING_CODES = {"cv": "carrier", "cr": "cruiser", "dd": "destroyer", "dn": "dreadnought", "ff": "fighter", "inf": "infantry", "pds": "pds", "sd": "spacedock"}


@lru_cache(maxsize=1)
def unit_profiles():
    definitions = {}
    for filename in ('baseUnits.json', 'pok.json'):
        for data in json.loads((RESOURCES / 'data/units' / filename).read_text(encoding='utf-8')):
            if filename == 'baseUnits.json' or data.get('source') == 'base' or data.get('faction'):
                definitions[data['id']] = data
    factions = {f['alias']: f for f in json.loads((RESOURCES / 'data/factions/base.json').read_text(encoding='utf-8'))}
    return definitions, factions


def unit_profile(unit):
    definitions, factions = unit_profiles()
    if unit.profile_id:
        return definitions[unit.profile_id]
    for profile_id in factions.get(unit.owner, {}).get('units', []):
        profile = definitions.get(profile_id)
        if profile and profile['baseType'] == unit.kind:
            return profile
    return definitions[unit.kind]


def starting_units(tile: Tile, faction: dict, color_code: str) -> list[Unit]:
    units = []
    for item in faction["startingFleet"].split(","):
        parts = item.strip().split()
        count = int(parts.pop(0)) if parts[0].isdigit() else 1
        kind = STARTING_CODES[parts.pop(0)]
        if UNIT_TYPES[kind]["ship"]:
            location = UnitLocation(Region.SPACE)
        else:
            prefix = parts[0]
            matches = [p for p in tile.planets if p.name.lower().startswith(prefix)]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous starting planet: {prefix}")
            location = UnitLocation(Region.PLANET, matches[0].planet_id)
        for _ in range(count):
            units.append(Unit(f"{faction['alias']}-{len(units) + 1}", kind, faction["alias"], color_code, location))
    return units


def setup_starting_fleets(board, config: dict):
    if config.get("setup") != "starting_fleets":
        return
    factions = {f["alias"]: f for f in json.loads((RESOURCES / "data/factions/base.json").read_text(encoding="utf-8"))}
    for entry in config["tiles"]:
        if entry.get("faction"):
            tile = board[(entry["q"], entry["r"])]
            faction = factions[entry["faction"]]
            if int(faction["homeSystem"]) != tile.number:
                raise ValueError("Faction does not match its home system")
            tile.units = starting_units(tile, faction, entry["unit_color"])


@dataclass(frozen=True)
class UnitPlacement:
    units: tuple[Unit, ...]
    x: float
    y: float
    size: float
    angle: float = 0
    badge_count: int | None = None

    @property
    def kind(self):
        return self.units[0].kind


def hex_clearance(x, y):
    dx, dy = abs(x - 172.5), abs(y - 149.5)
    return min(149.5 - dy, (math.sqrt(3) * 172.5 - math.sqrt(3) * dx - dy) / 2)


def outline_edge_support(outline):
    return (min(y for _, y in outline), max(y for _, y in outline),
            tuple(max(sx * math.sqrt(3) * dx + sy * dy for dx, dy in outline)
                  for sx, sy in ((-1, -1), (-1, 1), (1, -1), (1, 1))))


def outline_hex_clearance(x, y, support):
    min_y, max_y, diagonals = support
    clearance = min(y + min_y, 299 - y - max_y)
    for (sx, sy), extent in zip(((-1, -1), (-1, 1), (1, -1), (1, 1)), diagonals):
        clearance = min(clearance, (math.sqrt(3) * 172.5 -
                                    sx * math.sqrt(3) * (x - 172.5) -
                                    sy * (y - 149.5) - extent) / 2)
    return clearance


GROUP_THRESHOLD = 5


def convex_hull(points):
    points = sorted(set(points))
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return tuple(lower[:-1] + upper[:-1])


@lru_cache(maxsize=128)
def silhouette(image_path, size):
    with Image.open(image_path) as image:
        alpha = image.convert("RGBA").getchannel("A")
        width, height = image.size
        scale = size / max(width, height)
        points = [((x - width / 2) * scale, (y - height / 2) * scale) for x in range(width) for y in range(height) if alpha.getpixel((x, y)) > 140]
        return convex_hull(points)


@lru_cache(maxsize=512)
def rotated_silhouette(image_path, size, angle):
    theta = math.radians(angle)
    cosine, sine = math.cos(theta), math.sin(theta)
    return tuple((x * cosine - y * sine, x * sine + y * cosine)
                 for x, y in silhouette(image_path, size))


def placed_outline(placement):
    return tuple((placement.x + x, placement.y + y) for x, y in
                 rotated_silhouette(placement.units[0].image_path, placement.size, placement.angle))


def polygons_overlap(a, b):
    for polygon in (a, b):
        for i, p in enumerate(polygon):
            q = polygon[(i + 1) % len(polygon)]
            nx, ny = -(q[1] - p[1]), q[0] - p[0]
            pa = [x * nx + y * ny for x, y in a]
            pb = [x * nx + y * ny for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return False
    return True


def circle_overlap(polygon, center, radius):
    cx, cy = center
    signs = []
    for i, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(i + 1) % len(polygon)]
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((cx - ax) * dx + (cy - ay) * dy) / (dx * dx + dy * dy or 1)))
        if math.hypot(cx - ax - t * dx, cy - ay - t * dy) < radius:
            return True
        signs.append(dx * (cy - ay) - dy * (cx - ax))
    return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)


def protected_labels(tile):
    return [((px - 50, ly - 12), (px + 50, ly - 12), (px + 50, ly + 12), (px - 50, ly + 12)) for planet in tile.planets for px, py in [planet.center] for ly in (py - 65, py + 65)]


def layout_units(tile: Tile, detailed=False) -> list[UnitPlacement]:
    groups = defaultdict(list)
    for unit in tile.units:
        if unit.location.region != Region.TRANSPORT:
            groups[(unit.location.region, unit.location.planet_id, unit.owner, unit.kind)].append(unit)
    ground = defaultdict(list)
    fleet = []
    for (region, planet_id, owner, kind), members in groups.items():
        size = UNIT_TYPES[kind]["size"]
        if kind in ('infantry', 'fighter', 'mech') and len(members) >= 3:
            visible = [(tuple([unit]), None) for unit in members[:2]] + [(tuple(members[2:]), len(members))]
        elif len(members) < GROUP_THRESHOLD:
            visible = [(tuple([unit]), None) for unit in members]
        else:
            visible = [(tuple(members), len(members))]
        if region == Region.PLANET:
            ground[planet_id].extend((part, min(size, 42), badge_count) for part, badge_count in visible)
        else:
            fleet.extend((part, size, badge_count) for part, badge_count in visible)
    placements = []
    for planet_id, entries in ground.items():
        planet = next(p for p in tile.planets if p.planet_id == planet_id)
        cx, cy = planet.center
        if len(entries) > 9:
            consolidated = defaultdict(list)
            for members, _, _ in entries:
                consolidated[(members[0].owner, members[0].kind)].extend(members)
            entries = [(tuple(members), min(UNIT_TYPES[members[0].kind]["size"], 42), len(members) if members[0].kind in ('infantry', 'fighter', 'mech') and len(members) >= 3 else None) for members in consolidated.values()]
        count = len(entries)
        columns = min(3, count)
        rows = math.ceil(count / columns)
        for index, (members, size, badge_count) in enumerate(entries):
            row, col = divmod(index, columns)
            row_count = min(columns, count - row * columns)
            placements.append(UnitPlacement(members, cx + (col - (row_count - 1) / 2) * 36, cy + (row - (rows - 1) / 2) * 36 - 2, size, badge_count=badge_count))
    fleet.sort(key=lambda item: (-item[1], item[0][0].owner))
    occupied = []
    occupied_bounds = []
    same_kind = {}
    labels = protected_labels(tile)
    for members, size, badge_count in fleet:
        key = members[0].owner, members[0].kind
        candidates = []
        # Rotate only when a repeated ship cannot stay near its formation.
        for angle in (0, -15, 15, -30, 30, -45, 45, -60, 60, 90):
            if angle and key not in same_kind and candidates:
                break
            if angle and candidates:
                best = max(candidates, key=lambda candidate: candidate[0])
                if math.dist(best[1:3], same_kind[key]) <= size * 1.5:
                    break
            outline = rotated_silhouette(members[0].image_path, size, angle)
            bound_radius = max(math.hypot(dx, dy) for dx, dy in outline)
            edge_support = outline_edge_support(outline)
            for x in range(20, 326, 6):
                for y in range(18, 283, 6):
                    # The silhouette's bounding radius avoids exact polygon
                    # collision checks when shapes are clearly far apart.
                    polygon = tuple((x + dx, y + dy) for dx, dy in outline)
                    edge_clearance = outline_hex_clearance(x, y, edge_support)
                    if edge_clearance < 3:
                        continue
                    if any(math.dist((x, y), planet.center) < planet.radius + bound_radius + 2 and
                           circle_overlap(polygon, planet.center, planet.radius + 2)
                           for planet in tile.planets):
                        continue
                    if any(math.hypot(max(label[0][0] - x, 0, x - label[2][0]),
                                      max(label[0][1] - y, 0, y - label[2][1])) < bound_radius and
                           polygons_overlap(polygon, label) for label in labels):
                        continue
                    if math.dist((x, y), (104, 274)) < bound_radius + 29 and circle_overlap(polygon, (104, 274), 29):
                        continue
                    expanded = tuple((x + dx * 1.12, y + dy * 1.12) for dx, dy in outline)
                    expanded_radius = bound_radius * 1.12
                    if any(math.dist((x, y), (ox, oy)) < expanded_radius + other_radius and
                           polygons_overlap(expanded, other)
                           for other, (ox, oy, other_radius) in zip(occupied, occupied_bounds)):
                        continue
                    planet_clearance = min((math.hypot(x - p.center[0], y - p.center[1]) - p.radius for p in tile.planets), default=100)
                    score = min(edge_clearance, planet_clearance)
                    if key in same_kind:
                        score -= math.dist((x, y), same_kind[key]) * .7
                    candidates.append((score - abs(angle) * .015, x, y, polygon, angle))
        if not candidates:
            raise ValueError(f"No free display position for {members[0].kind} in tile {tile.number}")
        _, x, y, polygon, angle = max(candidates, key=lambda candidate: candidate[0])
        occupied.append(polygon)
        occupied_bounds.append((x, y, max(math.hypot(px - x, py - y) for px, py in polygon)))
        same_kind.setdefault(key, (x, y))
        placements.append(UnitPlacement(members, x, y, size, angle, badge_count))
    return placements


def system_inventory(tile):
    fleet, cargo = defaultdict(list), defaultdict(list)
    planets = {planet.planet_id: defaultdict(list) for planet in tile.planets}
    for unit in tile.units:
        if unit.location.region == Region.SPACE:
            fleet[unit.kind].append(unit)
        elif unit.location.region == Region.PLANET:
            planets[unit.location.planet_id][unit.kind].append(unit)
        else:
            cargo[unit.kind].append(unit)
    return {"fleet": dict(fleet), "planets": {p: dict(g) for p, g in planets.items()}, "cargo": dict(cargo)}
