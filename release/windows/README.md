# Windows build

Keep the `TI4` folder together and double-click `TI4/TI4.exe` to start the game. No Python installation is required.

To rebuild on Windows with Python 3.11, install the build dependencies and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\fetch_assets.py
.\.venv\Scripts\python.exe scripts\build_windows.py
```
