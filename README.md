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

The board displays the galaxy, component information and starting fleets. Players can activate systems and move ships and carried units. Assets are loaded from assets/source/asyncti4/src/main/resources. The upstream directory also contains homebrew content; the current map uses base-game systems only.


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

Select a system and press **Space** or click **Detail view** to inspect it. Larger figures show up to four units individually; groups of five or more use a count badge. Ships of the same type form nearby groups, while ground units stay on their planets. The sidebar includes the original system tile image and lists fleet counts by type and separate planet cards with resources, influence and garrisons. Click an inventory row to select its units; scroll over the panel to see longer lists. **Escape** returns to the galaxy. Mouse wheel over the map changes zoom, right/middle drag pans the galaxy, **F** fits the whole board.

Double-click a system to activate it for the player selected in the bottom dashboard. The activation spends one tactical command token and places that player's faction-marked triangle at the center of the system. Multiple factions' tokens can share a system. The sidebar lists the tokens in the selected system and friendly ships that can reach it, along with eligible infantry, mechs and fighters in each source system. Select ships and passengers, then choose **Move**; carried units travel with their selected carrier. Transport capacity is checked while selecting. **Cancel** or **Ctrl+Z** restores the activation and its tactical token. After a move, **Ctrl+Z** also restores the prior board state. Right-click a board token to reveal a debug button that removes it and returns it to its owner's tactical reserve. Each map entry can specify `faction` and `unit_color`; `setup: starting_fleets` enables starting units. Omitting this setup leaves a board without units.


## Asset source

`assets/resources.lock.json` pins the 225 resources needed by this build to AsyncTI4 commit `bd234c306286c00cc379f428558ac54152ebb0fe`. The restore script downloads about 10 MB and verifies each file against its Git blob hash. Downloaded artwork, virtual environments, local IDE settings and generated previews are excluded from Git. Original TI4 artwork remains third-party material; this repository does not grant a license to that artwork.

```powershell
.\.venv\Scripts\python.exe scripts/fetch_assets.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```


## Player dashboard

The bottom panel switches between the three faction dashboards. Each player starts with their home planet cards, 3 tactical tokens, 3 fleet tokens, 2 strategic tokens, zero trade goods and zero commodities. Commodity capacity comes from faction data (Sol 4, Hacan 6, Jol-Nar 4).

Click a planet card to switch between ready and exhausted sides. Ready resource and influence totals update immediately. Cards use the dedicated AsyncTI4 `planet_cards` assets for traits, resource/influence values, technology specialties and ready/exhausted states, plus faction homeworld emblems. Hover a card for larger text and details. Use the currency +/- controls to adjust balances; commodities cannot exceed capacity. Click a command pool, then another pool, to move one token. These are prototype state controls; turn timing, payments and other rules are not enforced yet, and state lasts for the current session.

The board and dashboard render live. Smoke-test screenshots are written to the ignored `previews/` directory and are not used by the game.

The player tray is 160 px tall. Planet cards paginate as the collection grows; the system sidebar uses compact inventory rows and a small original tile thumbnail.

Hovering a planet card raises and highlights it and softly outlines its containing system on the galaxy map. The selected-system panel shows a larger original tile image. Ships may rotate slightly when needed to fit a formation into a crowded system.
