#!/usr/bin/env python3
"""Build the Blender add-on release ZIP without requiring PowerShell."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO_ROOT / "light_field_plugin"
DIST_DIR = REPO_ROOT / "dist"


def parse_version() -> str:
    init_file = ADDON_DIR / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    match = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', text)
    if not match:
        raise SystemExit(f"Cannot parse bl_info version from {init_file}")
    return ".".join(match.groups())


def should_include(path: Path) -> bool:
    if "__pycache__" in path.parts:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def build_zip(version: str) -> Path:
    if not (ADDON_DIR / "__init__.py").exists():
        raise SystemExit(f"Cannot find add-on entrypoint: {ADDON_DIR / '__init__.py'}")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"light_field_render-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(ADDON_DIR.rglob("*")):
            if path.is_file() and should_include(path):
                zf.write(path, path.relative_to(REPO_ROOT).as_posix())
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Light Field Render release ZIP.")
    parser.add_argument("--version", default="", help="Release version. Defaults to bl_info version.")
    parser.add_argument("--no-bundle-numpy", action="store_true", help="Accepted for parity with build_release.ps1.")
    args = parser.parse_args()
    version = args.version.strip() or parse_version()
    zip_path = build_zip(version)
    print(f"Created {zip_path}")


if __name__ == "__main__":
    main()
