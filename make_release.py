#!/usr/bin/env python3
"""Zip a shippable copy of this repo.

Packages everything except the stuff nobody should ship: the virtualenv,
node_modules, Python/pytest caches, runtime output, and .env (which holds a
real API key). web/dist is included explicitly even though it is gitignored,
because a judge running this from the zip should not have to `npm run build`
first.

    python make_release.py             # writes recoverpay-release.zip
    python make_release.py --out x.zip # custom output path
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXCLUDE_DIRS = {
    ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "out", ".git", ".mypy_cache", ".ruff_cache",
}
EXCLUDE_FILES = {".env"}
EXCLUDE_SUFFIXES = {".pyc", ".zip"}


def should_skip_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS


def should_skip_file(path: Path) -> bool:
    if path.name in EXCLUDE_FILES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def iter_files():
    stack = [ROOT]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if not should_skip_dir(entry.name):
                    stack.append(entry)
            elif not should_skip_file(entry):
                yield entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="recoverpay-release.zip")
    args = ap.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in iter_files():
            arcname = path.relative_to(ROOT)
            zf.write(path, arcname)
            count += 1

    size_kb = out_path.stat().st_size / 1024
    print(f"wrote {out_path} ({count} files, {size_kb:.0f} KB)")
    if size_kb > 5 * 1024:
        print("WARNING: over the 5 MB target - check what got included.")


if __name__ == "__main__":
    main()
