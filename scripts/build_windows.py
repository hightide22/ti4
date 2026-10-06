"""Build a self-contained Windows folder bundle for the board game."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets/resources.lock.json"
RESOURCE_SOURCE = ROOT / "assets/source/asyncti4/src/main/resources"
DIST = ROOT / "release/windows"
RESOURCE_DESTINATION = "assets/source/asyncti4/src/main/resources"


def stage_resources(destination: Path) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    count = 0
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        source = RESOURCE_SOURCE / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing pinned resource: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return count


def fix_arcade_version_file() -> None:
    arcade_dir = DIST / "TI4/_internal/arcade"
    version = arcade_dir / "VERSION"
    nested_version = version / "VERSION"
    if not nested_version.is_file():
        if version.is_file():
            return
        raise FileNotFoundError(f"Missing bundled Arcade version file: {nested_version}")
    temporary = arcade_dir / ".VERSION.tmp"
    nested_version.replace(temporary)
    version.rmdir()
    temporary.replace(version)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build this bundle on Windows so PyInstaller creates a Windows executable.")
    if not MANIFEST.is_file():
        raise SystemExit(f"Missing resource manifest: {MANIFEST}")
    subprocess.run([sys.executable, str(ROOT / "scripts/fetch_assets.py"), "--check"],
                   cwd=ROOT, check=True)
    if not (ROOT / "maps/three_player.json").is_file():
        raise SystemExit("Missing default map: maps/three_player.json")
    DIST.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ti4-build-") as temporary:
        work = Path(temporary)
        staged = work / "runtime-resources"
        count = stage_resources(staged)
        command = [
            sys.executable,
            "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name", "TI4",
            "--contents-directory", "_internal",
            "--distpath", str(DIST),
            "--workpath", str(work / "work"),
            "--specpath", str(work / "spec"),
            "--collect-all", "pyglet",
            "--add-data", f"{staged}:{RESOURCE_DESTINATION}",
            "--add-data", f"{ROOT / 'maps'}:maps",
            str(ROOT / "app.py"),
        ]
        print(f"Bundling {count} pinned game resources...", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)

    fix_arcade_version_file()
    executable = DIST / "TI4/TI4.exe"
    if not executable.is_file():
        raise SystemExit(f"Build did not produce the expected executable: {executable}")
    print(f"PASS: {executable}")


if __name__ == "__main__":
    main()
