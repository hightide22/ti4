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
.\.venv\Scripts\python.exe app.py --technology-smoke-test
.\.venv\Scripts\python.exe scripts/round_draft_smoke.py
```

The board opens in a maximized desktop window with its normal title bar and window controls. Fleet layouts are computed in the background so the board appears without waiting for every starting system to finish arranging its units. Players can activate systems and move ships and carried units. Assets are loaded from assets/source/asyncti4/src/main/resources. The source bundle includes other editions, but gameplay catalogs and maps expose base-game content only.

At the new-game screen, choose the three- or four-player map and the factions in each seat. Check **PLAY AGAINST AI** and click your faction row; the other seats become computer opponents. Their draft, command allocation, strategy actions and secondaries, technology research, movement, invasions, combat hit assignment and production proceed automatically. Their choices use board position, available resources, transport capacity, fleet supply and enemy strength. On the opening turn, a bot with a reachable neutral planet, a transport and infantry expands before playing a strategy card or activating its home dock for production. A contested human planet receives 65% of the opponent-pressure weight and a bot-owned planet 35%; neutral expansion remains valuable. Before attacking, the bot estimates its chance of winning with the ships it can actually move, including fighters, sustain damage, anti-fighter barrage, hostile PDS fire and survival of a transport for an invasion. It also checks ground-combat odds for occupied planets and declines attacks below an 80% estimated win chance. These estimates do not consume game dice.

During an AI decision, its movement, strategy and action-card controls are hidden. Bot action-card names also stay hidden in the player roster until played. A human defending against the AI still sees their own hit-assignment and playable-card controls. The AI action feed explains recent choices. A newly activated system glows and the selected flight route appears while ships move there; the token-to-movement transition is brief, while combat and other outcomes remain visible longer. **PAUSE AI** stops automatic decisions, and the adjacent speed button cycles through 1×, 2×, 4× and 0.5×; 1× is the default viewing pace. After every player passes, all damaged units repair and all remaining commodities become trade goods before command allocation and the next strategy draft. Run `scripts/ai_smoke.py --players 3` or `--players 4` for a two-round GUI smoke test; `scripts/ai_visibility_smoke.py` checks private bot controls and human combat responses.


## Tile objects and custom maps

`board.py` defines `Tile` (system ID, position, image path, planets and player), and `Board`, a collection of tile objects with `add`, `remove`, `move`, and `neighbors`. `app.py` creates a separate `TileSprite` for each tile; every sprite has its own texture. Map size and player count are derived from the objects, not fixed in the renderer.

Use any JSON layout with a non-empty `tiles` list:

```powershell
.\.venv\Scripts\python.exe app.py --map maps/my_map.json
```

Each entry specifies `id`, `q`, `r`, and optionally `player` and `color`. Images and component data are loaded by system ID. The official three-player JSON is a starting layout, not a baked board image. Modifying object positions or loading another layout does not require editing the renderer.


## Component catalog

`TILES.by_number` stores base-game tile definitions by integer number. `TILES[18]` describes Mecatol Rex; `TILES.create(18, (0, 0))` creates a placed tile object. Definitions and planets are shared component descriptions, while each placed tile has independent coordinates, player and sprite. The catalog selects `HomeSystemTile`, `PlanetaryTile`, `AnomalyTile`, or `EmptySpaceTile`, all derived from `Tile`.

Each `Planet` has `name`, `planet_type`, `resources`, `influence`, `tech_specialties` and faction homeworld fields. Ownership is tracked per placed tile so shared component definitions remain unchanged.


## Units and system detail view

The default map now initializes the Sol, Hacan and Jol-Nar starting fleets from AsyncTI4 faction data. `units.py` defines individual units, space/planet/transport locations, and an automatic layout. `unit_view.py` renders the original colored sprites with shadows, count badges and selection highlights.

Select a system and press **Space** to inspect it. Infantry and fighters show three sprites and a total count badge when a group has at least three units; other groups of five or more use a count badge. Ships of the same type form nearby groups, while ground units stay on their planets. The sidebar includes the original system tile image and lists fleet counts by type and separate planet cards with resources, influence and garrisons. Click an inventory row to select its units; scroll over the panel to see longer lists. **Escape** returns to the galaxy. Mouse wheel over the map changes zoom, right/middle drag pans the galaxy, **F** fits the whole board.

Double-click a system to activate it for the current turn owner. The activation spends one tactical command token and places that player's faction-marked triangle at the center of the system. Multiple factions' tokens can share a system. The sidebar lists tokens in the selected system and friendly ships that can reach it, along with eligible infantry and fighters in each source system. Select ships and passengers, then choose **Move**; carried units travel with their selected carrier. Transport capacity is checked while selecting. After movement, the game checks fleet supply and asks the player to choose any excess non-fighter ships to destroy. Landed infantry can fight for planets; capturing one adds an exhausted planet card to the player dashboard and marks the planet with a rectangular faction token. If the activated system has a friendly production unit on a controlled planet, **STEP 5 · PRODUCTION** opens a production panel. Space docks provide their planet's resources plus 2 production, and unit costs come from faction unit profiles. Choose units within the production limit, exhaust ready planet cards for their resources, and spend trade goods to cover the selected cost. Infantry and fighters are built in pairs unless only one production slot remains. Infantry is placed on a production planet, ships in space. Fleet supply is checked again after production. Skipping or resolving production ends the action and records it for **Ctrl+Z**. The selected system panel lists system and planet owners. **Cancel** or **Ctrl+Z** restores the full tactical action. Right-click an activation token to reveal a debug button that removes it and returns it to its owner's tactical reserve. Each map entry can specify `faction` and `unit_color`; `setup: starting_fleets` enables starting units. Omitting this setup leaves a board without units.


## Asset source

`assets/resources.lock.json` pins the resources needed by this build to AsyncTI4 commit `bd234c306286c00cc379f428558ac54152ebb0fe`. The restore script downloads the required files and verifies each against its Git blob hash. Downloaded artwork, virtual environments, local IDE settings and generated previews are excluded from Git. Original TI4 artwork remains third-party material; this repository does not grant a license to that artwork.

```powershell
.\.venv\Scripts\python.exe scripts/fetch_assets.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```


## Player dashboard

The left roster shows every faction, its strategy cards, and the original Speaker token. Hover a player for read-only goods, command pools, reinforcements, planets, faction abilities, technology and action cards. Scroll over a hovered player to read longer details. The bottom dashboard follows the player currently resolving an action or secondary ability. Each player starts with their home planet cards, 3 tactical tokens, 3 fleet tokens, 2 strategic tokens, zero trade goods and zero commodities. Commodity capacity comes from faction data (Sol 4, Hacan 6, Jol-Nar 4).

Planet cards can be exhausted only during a payment. Click a ready planet to select its resources for production or its influence for Leadership; selected cards turn yellow, and clicking again removes that payment. Ready resource and influence totals update immediately. Cards use the dedicated AsyncTI4 `planet_cards` assets for traits, resource/influence values, technology specialties and ready/exhausted states, plus faction homeworld emblems. Hover a card for larger text and details. Use the currency +/- controls to adjust balances; commodities cannot exceed capacity. Allocate new tokens using the pool controls. Redistribution is available during Warfare and round-end allocation; ordinary turns cannot move tokens between pools. State lasts for the current session.

The board and dashboard render live. Smoke-test screenshots are written to the ignored `previews/` directory and are not used by the game.

The player tray is 160 px tall. Planet cards paginate as the collection grows; the system sidebar uses compact inventory rows and a small original tile thumbnail.

Hovering a planet card raises and highlights it and softly outlines its containing system on the galaxy map. The selected-system panel shows a larger original tile image. Ships may rotate slightly when needed to fit a formation into a crowded system.

## Strategy cards

The strategy draft opens as a centered dialog with original base-game card artwork. The Speaker picks first; players pick clockwise, twice in three- and four-player games. Initiative uses each player's lowest-numbered card. After every player passes, all strategy cards return to the draft. Players allocate their new command tokens, then the Speaker starts the next draft. The left roster shows ready card fronts and used card backs. Click the active player's strategy cards to open their play dialog.

A strategic action holds the current turn until the owner finishes the entire primary ability, including payment, construction choices, and command allocation. Every other player, including players who passed, is then offered the secondary in clockwise seating order. Each may use it or skip. The dashboard follows that responder; completing the final secondary returns control to the turn owner with **END TURN** available. Secondary abilities cannot be deferred to a later turn.

## Action cards

The combat and movement shortlist contains 18 base-game action cards. Politics draws two cards for its owner and lets other players spend a Strategy token to draw up to two. Each player also draws one at the end of a round, up to the seven-card hand limit. The **CARDS** button glows whenever any player's hand has a card available in the current timing window; playable scans also glow with **PLAY NOW**. The hand supports movement bonuses, combat rolls and rerolls, hit cancellation, retreat, Space Cannon, bombardment, system movement, unit placement and combat reactions. The card list and timing windows are in [docs/action_cards.md](docs/action_cards.md).

Leadership uses explicit payment: choose the extra token count, click ready planets for influence, and choose trade goods with +/- controls. The dialog shows paid influence versus required influence. Nothing selects payment automatically, and commodities cannot pay. New tokens must be distributed before the next secondary offer. The combined command-sheet, board, and unallocated-token count is capped at 16. Leadership's secondary has no strategy-token cost.

Diplomacy lets the owner choose a controlled system other than Mecatol Rex, places other players' command tokens there and readies every exhausted planet the owner controls in that system. Its secondary can ready up to two exhausted planets anywhere. Construction uses selected structures and planets. Trade lets the owner choose free secondary recipients. Warfare returns a selected board token for allocation and redistribution; its secondary uses exactly one selected Space Dock in the responder's home system, with the existing production payment interface. Politics draws two action cards and changes the Speaker; card 8 remains a placeholder.

## Technologies

Open **TECHNOLOGY** in the system inspector to view the faction's unit sheet and technology cards. The **UNITS** tab shows current unit values; researching a unit upgrade replaces the relevant profile on existing units and future production. The four colored tabs show the available generic and faction technologies using the bundled card scans. Researched cards are marked **OWNED**.

Strategy card 7 opens the same dialog for research. Its owner researches one technology for free and may research a second for 6 resources. Other players may spend a strategy token and 4 resources for one technology; Jol-Nar may use Brilliant to resolve the primary effect instead. Select a card, meet its colored prerequisites with owned technologies or ready specialty planets, and click planet cards below the dialog to pay resources. Trade goods may cover resource cost. The dialog distinguishes resource payment from specialty use.

Researched base-game action technologies expose their targets in the card detail: X-89 Bacterial Weapon, Transit Diodes and Production Biomes. Quantum Datahub Node offers a strategy-card exchange at the end of drafting. Base-game production discounts and unit-profile abilities apply to tactical production, movement and combat. Agenda effects await that game phase.

Strategy and roster artwork and reference data are included in the pinned asset manifest. The GUI scenario verifies central drafting at 1440×900 and 1120×720, manual influence payment, token allocation, immediate secondary offers, free Trade recipients, Warfare production, and player details:

```powershell
.\.venv\Scripts\python.exe scripts/strategy_smoke.py
```

Screenshots are written to the ignored `previews/strategy-*.png` files.
