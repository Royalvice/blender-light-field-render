#!/usr/bin/env python3
"""Generate a quick visual QA sample for the standard AM film screen."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_PATH = REPO_ROOT / "light_field_plugin" / "core" / "delivery.py"


def load_delivery_module():
    spec = importlib.util.spec_from_file_location("delivery_under_am_sample", DELIVERY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


delivery = load_delivery_module()


def clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def sample_row(y: int, width: int, height: int) -> bytes:
    row = bytearray(width * 3)
    band_h = max(1, height // 4)
    for x in range(width):
        if y < band_h:
            blocks = 16
            tone = int((x * blocks) / max(1, width))
            value = clamp_byte(tone * 255 / max(1, blocks - 1))
        elif y < band_h * 2:
            value = clamp_byte(x * 255 / max(1, width - 1))
        elif y < band_h * 3:
            vertical = clamp_byte((y - band_h * 2) * 255 / max(1, band_h - 1))
            horizontal = clamp_byte(x * 255 / max(1, width - 1))
            value = clamp_byte(0.65 * horizontal + 0.35 * vertical)
        else:
            stripe = 255
            local_y = y - band_h * 3
            if x % 64 in {0, 1} or local_y % 64 in {0, 1}:
                stripe = 0
            elif x % 32 == 0 or local_y % 32 == 0:
                stripe = 80
            value = stripe
        offset = x * 3
        row[offset] = value
        row[offset + 1] = value
        row[offset + 2] = value
    return bytes(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a standard AM halftone QA sample.")
    parser.add_argument("--output-dir", type=Path, default=Path("standard_am_sample"))
    parser.add_argument("--width", type=int, default=2048)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--ppi", type=int, default=4000)
    parser.add_argument("--lpi", type=float, default=200.0)
    parser.add_argument("--angle", type=float, default=45.0)
    parser.add_argument("--dot-shape", default="ROUND", choices=["ROUND", "DIAMOND", "ELLIPSE"])
    parser.add_argument("--gamma", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = args.output_dir / "standard_am_source.png"
    film_path = args.output_dir / "standard_am_1bit.tif"

    settings = delivery.HalftoneSettings(
        method="AM",
        lpi=args.lpi,
        angle_degrees=args.angle,
        dot_shape=args.dot_shape,
        gamma=args.gamma,
    )
    halftoner = delivery.StreamingHalftoner(args.width, settings, args.ppi)

    rows = [sample_row(y, args.width, args.height) for y in range(args.height)]
    delivery.write_rgb_png(str(preview_path), args.width, args.height, rows)
    with delivery.OneBitTiffWriter(str(film_path), args.width, args.height, args.ppi) as writer:
        for y, row in enumerate(rows):
            writer.write_black_row(halftoner.process_rgb_row(y, row))

    manifest = {
        "width": args.width,
        "height": args.height,
        "ppi": args.ppi,
        "halftone": {
            **delivery.standard_am_halftone_manifest(settings, args.ppi),
            "method": settings.method,
            "gamma": settings.gamma,
        },
        "files": {
            "source_png": preview_path.name,
            "film_1bit_tiff": film_path.name,
        },
    }
    (args.output_dir / "standard_am_sample_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {preview_path}")
    print(f"Wrote {film_path}")


if __name__ == "__main__":
    main()
