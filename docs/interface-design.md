# Command interface

This branch contains the compact Command concept with a permanently visible system workbench and the game's original palette. Run `python app.py` from the project environment. The Windows bundle is deliberately unchanged.

- The map stays central. The top bar separates the current player and turn action from map controls.
- The left roster shows active/passed players, strategy cards and the speaker; hover for faction details.
- The bottom resource tray keeps planets, payment, currencies and command pools in one place. Paid planets have a visible label and gold border.
- System information uses a scrollable inventory instead of repeating a large map tile. Unit names and counts have separate columns.
- Tactical actions show five main phases. **Show all step details** expands the full sequence. The action buttons remain visible while the source/unit list scrolls.
- Production has larger quantity controls, separate cost labels, and capacity/payment progress bars.
- Strategy cards have large selection areas and explicit availability labels. Strategy and combat dialogs can be moved using their headers.
- Combat has a fixed summary for each side, wrapped dice, independent column scrolling and a separate action footer. Click a unit group to assign a hit; scroll over that side to see more units.

The alternative Atlas branch lets players collapse the system inspector to give the galaxy more room. Both concepts use the game's original colors. Switch branches while the game is closed.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe app.py --smoke-test
.\.venv\Scripts\python.exe scripts\strategy_smoke.py
.\.venv\Scripts\python.exe scripts\interface_smoke.py
```

The GUI checks cover real click targets, ordered strategy responses, compact layouts, source-list scrolling, movement-route hover, dense combat dice and hit assignment after scrolling. Preview images are saved under `previews/`, which is ignored by Git.
