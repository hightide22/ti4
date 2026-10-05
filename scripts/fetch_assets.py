"""Restore pinned AsyncTI4 resources required by the current base-game build."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "assets/source/asyncti4/src/main/resources"


def blob_hash(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def restore(entry, base_url, check):
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid resource path")
    target = DESTINATION / relative
    if target.is_file() and blob_hash(target.read_bytes()) == entry["sha"]:
        return
    if check:
        raise ValueError(f"Missing or modified resource: {relative}")
    with urlopen(base_url + quote(entry["path"], safe="/"), timeout=60) as response:
        data = response.read()
    if len(data) != entry["size"] or blob_hash(data) != entry["sha"]:
        raise ValueError(f"Resource verification failed: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    temporary.write_bytes(data)
    temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify local files without downloading")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "assets/resources.lock.json").read_text(encoding="utf-8"))
    repository = manifest["repository"].removeprefix("https://github.com/")
    base_url = f"https://raw.githubusercontent.com/{repository}/{manifest['commit']}/src/main/resources/"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda entry: restore(entry, base_url, args.check), manifest["files"]))
    print(f"PASS: {len(manifest['files'])} pinned resources verified")


if __name__ == "__main__":
    main()
