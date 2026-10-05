# Twilight Imperium IV: 2D board

Set up and run on Windows (Python 3.11):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/fetch_assets.py
.\.venv\Scripts\python.exe app.py
```

Controls: left-click selects a system; right/middle drag pans; mouse wheel zooms; F fits the whole board. The map follows the official TI4 Learn to Play three-player layout on page 22, with Sol, Hacan and Jol-Nar occupying the three home-system placeholders. Factions can be changed in maps/three_player.json.

Validation:

```powershell
.\.venv\Scripts\python.exe app.py --validate
.\.venv\Scripts\python.exe app.py --smoke-test
```

This first build displays the board and component information. Turns, movement and AI are not implemented yet; starting fleets are displayed. Assets are loaded from assets/source/asyncti4/src/main/resources. The upstream directory also contains homebrew content; the current map uses base-game systems only.


## Tile objects and custom maps

`board.py` defines `Tile` (system ID, position, image path, planets and player), and `Board`, a collection of tile objects with `add`, `remove`, `move`, and `neighbors`. `app.py` creates a separate `TileSprite` for each tile; every sprite has its own texture. Map size and player count are derived from the objects, not fixed in the renderer.

Use any JSON layout with a non-empty `tiles` list:

```powershell
.\.venv\Scripts\python.exe app.py --map maps/my_map.json
```

Each entry specifies `id`, `q`, `r`, and optionally `player` and `color`. Images and component data are loaded by system ID. The official three-player JSON is a starting layout, not a baked board image. Modifying object positions or loading another layout does not require editing the renderer.


## Component catalog

`TILES.by_number` stores base-game tile definitions by integer number. `TILES[18]` describes Mecatol Rex; `TILES.create(18, (0, 0))` creates a placed tile object. Definitions and planets are shared component descriptions, while each placed tile has independent coordinates, player and sprite. The catalog selects `HomeSystemTile`, `PlanetaryTile`, `AnomalyTile`, or `EmptySpaceTile`, all derived from `Tile`.

Each `Planet` has `name`, `planet_type`, `resources`, `influence`, `tech_specialties`, faction homeworld and legendary ability fields. Future ownership/exhaustion state should be stored separately from these component descriptions.


## Units and system detail view

The default map now initializes the Sol, Hacan and Jol-Nar starting fleets from AsyncTI4 faction data. `units.py` defines individual units, space/planet/transport locations, and an automatic layout. `unit_view.py` renders the original colored sprites with shadows, count badges and selection highlights.

Select a system and press **Space**, double-click it, or click **Detail view** to inspect it. Larger figures show up to four units individually; groups of five or more use a count badge. Ships of the same type form nearby groups, while ground units stay on their planets. The sidebar includes the original system tile image and lists fleet counts by type and separate planet cards with resources, influence and garrisons. Click an inventory row to select its units; scroll over the panel to see longer lists. **Escape** returns to the galaxy. Mouse wheel over the map changes zoom, right/middle drag pans the galaxy, **F** fits the whole board.

Units are currently displayed in their starting positions. Movement, transport assignment and combat are not implemented. Each map entry can specify `faction` and `unit_color`; `setup: starting_fleets` enables starting units. Omitting this setup leaves a board without units.


## Asset source

`assets/resources.lock.json` pins the 195 resources needed by this build to AsyncTI4 commit `bd234c306286c00cc379f428558ac54152ebb0fe`. The restore script downloads about 10 MB and verifies each file against its Git blob hash. Downloaded artwork, virtual environments, local IDE settings and generated previews are excluded from Git. Original TI4 artwork remains third-party material; this repository does not grant a license to that artwork.

```powershell
.\.venv\Scripts\python.exe scripts/fetch_assets.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
