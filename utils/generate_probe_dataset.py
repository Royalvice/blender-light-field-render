#!/usr/bin/env python3
"""Generate a 150-view probe image set for reverse-engineering film output.

The generated images are RGB PNG files. They are intended to be submitted as a
single multi-view job to a film/lenticular print vendor. A returned continuous
or 1-bit TIFF can then be analyzed to infer geometric preprocessing, interlace
mapping, color transforms, and halftone/RIP behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import zlib
from pathlib import Path
from typing import Iterable

import numpy as np


DEFAULT_WIDTH = 2160
DEFAULT_HEIGHT = 3651
DEFAULT_COUNT = 150
DEFAULT_OUT = "probe_dataset_v001"
VIEW_COLOR_LEVELS = (16, 60, 104, 148, 192, 236)


REGIONS = [
    {
        "name": "orientation_and_view_id_header",
        "y0": 0,
        "y1": 360,
        "purpose": "corner fiducials, ordering guards, and human-readable/source view ID blocks",
    },
    {
        "name": "binary_gray_code_view_id",
        "y0": 360,
        "y1": 920,
        "purpose": "repeated local Gray-code barcode tiles for model fitting after 1-bit output",
    },
    {
        "name": "continuous_view_color_decode",
        "y0": 920,
        "y1": 1340,
        "purpose": "uniform per-view RGB code; best region for exact view map recovery from continuous TIFF",
    },
    {
        "name": "shared_coordinate_ramps",
        "y0": 1340,
        "y1": 1940,
        "purpose": "identical RGB coordinate ramps to measure crop, scale, rotation, channel order, and gamma",
    },
    {
        "name": "shared_resolution_frequency_chart",
        "y0": 1940,
        "y1": 2300,
        "purpose": "identical line/checker charts to detect resampling, blur, and RIP resolution limits",
    },
    {
        "name": "shared_halftone_tone_scale",
        "y0": 2300,
        "y1": 3050,
        "purpose": "identical flat tone patches and ramps for density curve and AM/FM halftone inference",
    },
    {
        "name": "shared_screen_angle_frequency_chart",
        "y0": 3050,
        "y1": 3440,
        "purpose": "identical angled sinusoidal gratings for screen angle, LPI, and moire analysis",
    },
    {
        "name": "view_dependent_impulse_footer",
        "y0": 3440,
        "y1": 3651,
        "purpose": "sparse per-view impulse lines to expose view ordering and local phase",
    },
]


SEGMENTS = {
    "0": "abcfed",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abfgcd",
}


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png_rgb(path: Path, image: np.ndarray, compress_level: int) -> None:
    """Write a non-interlaced 8-bit RGB PNG using only the Python stdlib."""
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be uint8 RGB")

    height, width, _ = image.shape
    compressor = zlib.compressobj(compress_level)
    compressed_parts = []
    for row in image:
        compressed_parts.append(compressor.compress(b"\x00" + row.tobytes()))
    compressed_parts.append(compressor.flush())

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", b"".join(compressed_parts)),
            _chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(data)


def _build_view_palette() -> tuple[tuple[int, int, int], ...]:
    """Build a deterministic high-distance RGB palette for continuous decoding."""
    candidates = [
        (r, g, b)
        for r in VIEW_COLOR_LEVELS
        for g in VIEW_COLOR_LEVELS
        for b in VIEW_COLOR_LEVELS
    ]
    seeds = [
        (16, 16, 16),
        (236, 236, 236),
        (16, 236, 16),
        (236, 16, 236),
        (16, 16, 236),
        (236, 236, 16),
        (236, 16, 16),
        (16, 236, 236),
    ]
    selected = list(seeds)
    remaining = [candidate for candidate in candidates if candidate not in selected]

    while remaining:
        best_index = 0
        best_distance = -1
        for index, candidate in enumerate(remaining):
            nearest = min(
                (candidate[0] - chosen[0]) ** 2
                + (candidate[1] - chosen[1]) ** 2
                + (candidate[2] - chosen[2]) ** 2
                for chosen in selected
            )
            if nearest > best_distance:
                best_distance = nearest
                best_index = index
        selected.append(remaining.pop(best_index))
    return tuple(selected)


VIEW_PALETTE = _build_view_palette()


def view_color(view_index: int) -> tuple[int, int, int]:
    """Return a deterministic, well-separated RGB code for the source view."""
    return VIEW_PALETTE[view_index]


def gray_code(value: int) -> int:
    return value ^ (value >> 1)


def draw_rect(
    image: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    image[max(y0, 0) : min(y1, image.shape[0]), max(x0, 0) : min(x1, image.shape[1])] = color


def draw_separator(image: np.ndarray, y: int) -> None:
    draw_rect(image, 0, y - 2, image.shape[1], y + 2, (0, 0, 0))
    draw_rect(image, 0, y + 2, image.shape[1], y + 5, (255, 255, 255))


def draw_fiducial(image: np.ndarray, x: int, y: int, size: int, invert: bool = False) -> None:
    colors = [(0, 0, 0), (255, 255, 255)]
    if invert:
        colors.reverse()
    step = size // 6
    for i in range(6):
        draw_rect(image, x + i * step, y + i * step, x + size - i * step, y + size - i * step, colors[i % 2])
    draw_rect(image, x + step * 2, y + step * 2, x + step * 4, y + step * 4, colors[0])


def draw_digit(image: np.ndarray, digit: str, x: int, y: int, scale: int, color: tuple[int, int, int]) -> None:
    thickness = scale
    width = 5 * scale
    height = 9 * scale
    segments = SEGMENTS[digit]

    if "a" in segments:
        draw_rect(image, x + thickness, y, x + width - thickness, y + thickness, color)
    if "b" in segments:
        draw_rect(image, x + width - thickness, y + thickness, x + width, y + height // 2, color)
    if "c" in segments:
        draw_rect(image, x + width - thickness, y + height // 2, x + width, y + height - thickness, color)
    if "d" in segments:
        draw_rect(image, x + thickness, y + height - thickness, x + width - thickness, y + height, color)
    if "e" in segments:
        draw_rect(image, x, y + height // 2, x + thickness, y + height - thickness, color)
    if "f" in segments:
        draw_rect(image, x, y + thickness, x + thickness, y + height // 2, color)
    if "g" in segments:
        draw_rect(
            image,
            x + thickness,
            y + height // 2 - thickness // 2,
            x + width - thickness,
            y + height // 2 + thickness // 2,
            color,
        )


def draw_number(image: np.ndarray, value: int, x: int, y: int, scale: int) -> None:
    text = f"{value:03d}"
    cursor = x
    for digit in text:
        draw_digit(image, digit, cursor, y, scale, (0, 0, 0))
        cursor += 7 * scale


def fill_header(image: np.ndarray, view_index: int, count: int) -> None:
    height, width, _ = image.shape
    draw_rect(image, 0, 0, width, 360, (242, 242, 242))

    for x in range(0, width, 24):
        color = (0, 0, 0) if (x // 24) % 2 == 0 else (255, 255, 255)
        draw_rect(image, x, 0, x + 24, 28, color)
    for y in range(0, 360, 24):
        color = (0, 0, 0) if (y // 24) % 2 == 0 else (255, 255, 255)
        draw_rect(image, 0, y, 28, y + 24, color)

    draw_fiducial(image, 48, 52, 220, invert=False)
    draw_fiducial(image, width - 268, 52, 220, invert=True)
    draw_number(image, view_index, 330, 70, 18)

    color = view_color(view_index)
    draw_rect(image, 830, 70, width - 330, 215, color)
    draw_rect(image, 830, 230, width - 330, 300, (round(view_index * 255 / max(count - 1, 1)),) * 3)

    code = gray_code(view_index)
    cell_w = max(12, (width - 660) // 18)
    x0 = 330
    y0 = 245
    for bit in range(9):
        on = (code >> bit) & 1
        c = (255, 255, 255) if on else (0, 0, 0)
        draw_rect(image, x0 + bit * cell_w, y0, x0 + (bit + 1) * cell_w - 4, y0 + 55, c)
        inv = (0, 0, 0) if on else (255, 255, 255)
        draw_rect(image, x0 + (bit + 9) * cell_w, y0, x0 + (bit + 10) * cell_w - 4, y0 + 55, inv)


def fill_binary_gray_code_region(image: np.ndarray, view_index: int) -> None:
    width = image.shape[1]
    y0, y1 = 360, 920
    draw_rect(image, 0, y0, width, y1, (127, 127, 127))
    code = gray_code(view_index)
    cell_w = 20
    cell_h = 26
    tile_w = cell_w * 18
    tile_h = cell_h * 2

    for tile_y in range(y0 + 14, y1 - tile_h, tile_h + 12):
        for tile_x in range(16, width - tile_w, tile_w + 28):
            draw_rect(image, tile_x - 2, tile_y - 2, tile_x + tile_w + 2, tile_y + tile_h + 2, (0, 0, 0))
            draw_rect(image, tile_x, tile_y, tile_x + tile_w, tile_y + tile_h, (127, 127, 127))
            for bit in range(9):
                on = (code >> bit) & 1
                color = (255, 255, 255) if on else (0, 0, 0)
                inverse = (0, 0, 0) if on else (255, 255, 255)
                x0 = tile_x + bit * cell_w
                draw_rect(image, x0, tile_y, x0 + cell_w - 2, tile_y + cell_h - 2, color)
                draw_rect(image, x0, tile_y + cell_h, x0 + cell_w - 2, tile_y + tile_h - 2, inverse)

                x1 = tile_x + (bit + 9) * cell_w
                draw_rect(image, x1, tile_y, x1 + cell_w - 2, tile_y + cell_h - 2, inverse)
                draw_rect(image, x1, tile_y + cell_h, x1 + cell_w - 2, tile_y + tile_h - 2, color)


def fill_continuous_view_region(image: np.ndarray, view_index: int) -> None:
    width = image.shape[1]
    y0, y1 = 920, 1340
    draw_rect(image, 0, y0, width, y1, view_color(view_index))


def fill_coordinate_ramps(image: np.ndarray) -> None:
    width = image.shape[1]
    y0, y1 = 1340, 1940
    h = y1 - y0
    x = np.arange(width, dtype=np.uint16)
    y = np.arange(h, dtype=np.uint16)[:, None]
    block = image[y0:y1]
    block[:, :, 0] = (x * 255 // max(width - 1, 1)).astype(np.uint8)
    block[:, :, 1] = (y * 255 // max(h - 1, 1)).astype(np.uint8)
    block[:, :, 2] = ((((x[None, :] // 16) + (y // 16)) & 1) * 255).astype(np.uint8)

    for x0 in range(0, width, 270):
        draw_rect(image, x0, y0, x0 + 6, y1, (255, 255, 255))
    for yy in range(y0, y1, 150):
        draw_rect(image, 0, yy, width, yy + 6, (0, 0, 0))


def fill_resolution_chart(image: np.ndarray) -> None:
    width = image.shape[1]
    y0, y1 = 1940, 2300
    draw_rect(image, 0, y0, width, y1, (128, 128, 128))
    pitches = [2, 3, 4, 6, 8, 12, 16, 24, 32, 48]
    cell_w = width // len(pitches)
    for idx, pitch in enumerate(pitches):
        x0 = idx * cell_w
        x1 = width if idx == len(pitches) - 1 else (idx + 1) * cell_w
        xs = np.arange(x1 - x0, dtype=np.uint16)
        ys = np.arange(y1 - y0, dtype=np.uint16)[:, None]
        if idx % 2 == 0:
            values = (((xs[None, :] // pitch) & 1) * 255).astype(np.uint8)
        else:
            values = ((((xs[None, :] // pitch) + (ys // pitch)) & 1) * 255).astype(np.uint8)
        image[y0:y1, x0:x1] = values[:, :, None]


def fill_halftone_region(image: np.ndarray) -> None:
    width = image.shape[1]
    y0, y1 = 2300, 3050
    draw_rect(image, 0, y0, width, y1, (128, 128, 128))

    cols = 12
    rows = 5
    cell_w = width // cols
    cell_h = (y1 - y0) // rows
    total = cols * rows
    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col
            tone = round(idx * 255 / (total - 1))
            x0 = col * cell_w
            x1 = width if col == cols - 1 else (col + 1) * cell_w
            yy0 = y0 + row * cell_h
            yy1 = y0 + (row + 1) * cell_h - 4
            draw_rect(image, x0, yy0, x1, yy1, (tone, tone, tone))

    ramp_y0 = y1 - 110
    ramp = np.linspace(0, 255, width, dtype=np.uint8)
    image[ramp_y0:y1, :, :] = ramp[None, :, None]


def fill_angle_frequency_region(image: np.ndarray) -> None:
    width = image.shape[1]
    y0, y1 = 3050, 3440
    h = y1 - y0
    x = np.arange(width, dtype=np.float32)[None, :]
    y = np.arange(h, dtype=np.float32)[:, None]
    angles = [0.0, 15.0, 45.0, 75.0]
    periods = [10.0, 14.0, 20.0, 28.0]
    stripe_h = h // len(angles)
    for idx, angle in enumerate(angles):
        rad = math.radians(angle)
        coord = x * math.cos(rad) + y * math.sin(rad)
        values = (0.5 + 0.5 * np.sin(coord * (2.0 * math.pi / periods[idx]))) * 255.0
        yy0 = y0 + idx * stripe_h
        yy1 = y1 if idx == len(angles) - 1 else y0 + (idx + 1) * stripe_h
        image[yy0:yy1, :, :] = values[: yy1 - yy0, :, None].astype(np.uint8)


def fill_impulse_footer(image: np.ndarray, view_index: int, count: int) -> None:
    width = image.shape[1]
    y0, y1 = 3440, image.shape[0]
    footer_h = y1 - y0
    draw_rect(image, 0, y0, width, y1, (0, 0, 0))

    x = round((view_index + 0.5) * width / count)
    draw_rect(image, x - 2, y0, x + 3, y1, (255, 255, 255))

    group_y = y0 + round(((view_index % 50) + 0.5) * footer_h / 50)
    draw_rect(image, 0, group_y - 2, width, group_y + 3, view_color(view_index))

    code = gray_code(view_index)
    cell_w = width // 18
    for bit in range(9):
        on = (code >> bit) & 1
        color = (255, 255, 255) if on else (0, 0, 0)
        draw_rect(image, bit * cell_w, y1 - 42, (bit + 1) * cell_w - 4, y1, color)
        draw_rect(image, (bit + 9) * cell_w, y1 - 42, (bit + 10) * cell_w - 4, y1, (255 - color[0],) * 3)


def generate_view(width: int, height: int, count: int, view_index: int) -> np.ndarray:
    if width < DEFAULT_WIDTH or height < DEFAULT_HEIGHT:
        raise ValueError(f"probe layout requires at least {DEFAULT_WIDTH}x{DEFAULT_HEIGHT}")

    image = np.full((height, width, 3), 128, dtype=np.uint8)
    fill_header(image, view_index, count)
    fill_binary_gray_code_region(image, view_index)
    fill_continuous_view_region(image, view_index)
    fill_coordinate_ramps(image)
    fill_resolution_chart(image)
    fill_halftone_region(image)
    fill_angle_frequency_region(image)
    fill_impulse_footer(image, view_index, count)

    for region in REGIONS[1:]:
        if region["y0"] < height:
            draw_separator(image, region["y0"])
    return image


def write_manifest(out_dir: Path, width: int, height: int, count: int, compress_level: int) -> None:
    manifest = {
        "dataset": "light_field_reverse_engineering_probe_v001",
        "width": width,
        "height": height,
        "view_count": count,
        "filename_pattern": f"images/probe_000.png ... images/probe_{count - 1:03d}.png",
        "view_palette": {
            "levels": list(VIEW_COLOR_LEVELS),
            "minimum_rgb_distance": 44.0,
        },
        "png": {
            "color_type": "RGB",
            "bit_depth": 8,
            "interlace": "none",
            "compress_level": compress_level,
        },
        "regions": REGIONS,
        "view_codes": [
            {
                "index": index,
                "filename": f"images/probe_{index:03d}.png",
                "rgb": list(view_color(index)),
                "gray_code": gray_code(index),
            }
            for index in range(count)
        ],
        "recommended_vendor_request": [
            "Treat probe_000.png ... probe_149.png as ordered views 0 ... 149.",
            "Use the vendor's normal/default interlacing and film output workflow.",
            "Return any intermediate continuous interlaced TIFF if available.",
            "Return the final film TIFF as produced by the RIP if available.",
            "Do not manually crop, retouch, sharpen, denoise, rotate, or reorder files.",
        ],
    }
    (out_dir / "probe_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_factory_readme(out_dir: Path, count: int, width: int, height: int) -> None:
    text = f"""# Probe 数据集交付说明

请把 `images/probe_000.png` 到 `images/probe_{count - 1:03d}.png` 当作按顺序排列的 {count} 个视角输入。

每张图片尺寸为 `{width} x {height}`，RGB 8-bit PNG。请按你们正常/默认的光栅或菲林制作流程处理，不需要人工修图。

请尽量返回以下文件：

1. 如果软件能导出合成后、挂网前的连续调 TIFF，请返回这个 TIFF。
2. 如果最终会进入 RIP/挂网/菲林输出，请返回最终 TIFF，包含 1-bit TIFF 也可以。
3. 如果只能返回一种文件，请返回你们实际用于菲林生产的最终 TIFF。

请尽量不要做这些处理：重命名导致排序变化、裁切、缩放、旋转、锐化、降噪、自动调色、转 JPEG、有损压缩。

如果你们的软件必须设置参数，请使用你们真实生产默认值即可；不用为了这个 probe 特意优化。
"""
    (out_dir / "README_FOR_FACTORY.md").write_text(text, encoding="utf-8")


def iter_indices(count: int, limit: int | None) -> Iterable[int]:
    if limit is None:
        return range(count)
    return range(min(count, limit))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a light-field film reverse-engineering probe dataset.")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Output directory. Default: {DEFAULT_OUT}")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"Image width. Default: {DEFAULT_WIDTH}")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"Image height. Default: {DEFAULT_HEIGHT}")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"View count. Default: {DEFAULT_COUNT}")
    parser.add_argument("--limit", type=int, default=None, help="Generate only the first N images for a quick test.")
    parser.add_argument("--compress-level", type=int, default=1, choices=range(10), help="PNG zlib compression level.")
    parser.add_argument("--force", action="store_true", help="Overwrite the output directory if it already exists.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out)
    image_dir = out_dir / "images"

    if args.count > len(VIEW_PALETTE):
        raise SystemExit(f"View count {args.count} exceeds the palette capacity {len(VIEW_PALETTE)}.")

    if args.width != DEFAULT_WIDTH or args.height != DEFAULT_HEIGHT:
        print(
            "WARNING: the factory probe was designed for "
            f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}; custom dimensions are for experiments only."
        )

    if out_dir.exists():
        if not args.force:
            raise SystemExit(f"Output directory already exists: {out_dir}. Use --force to overwrite it.")
        shutil.rmtree(out_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    write_manifest(out_dir, args.width, args.height, args.count, args.compress_level)
    write_factory_readme(out_dir, args.count, args.width, args.height)

    indices = list(iter_indices(args.count, args.limit))
    for position, index in enumerate(indices, start=1):
        image = generate_view(args.width, args.height, args.count, index)
        path = image_dir / f"probe_{index:03d}.png"
        write_png_rgb(path, image, args.compress_level)
        print(f"[{position:03d}/{len(indices):03d}] wrote {path}")

    if args.limit is not None and args.limit < args.count:
        print(f"Generated a limited test set: {len(indices)} of {args.count} images.")
    else:
        print(f"Generated complete probe dataset: {len(indices)} images.")
    print(f"Output: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
