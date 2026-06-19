"""Fit explainable line-screen film halftone candidates against an LBY TIFF.

This is a developer calibration tool. It intentionally keeps large source,
target, generated, and report artifacts outside git by default.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_PATH = REPO_ROOT / "light_field_plugin" / "core" / "delivery.py"
spec = importlib.util.spec_from_file_location("delivery", DELIVERY_PATH)
delivery = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["delivery"] = delivery
spec.loader.exec_module(delivery)


@dataclass(frozen=True)
class LineScreenParams:
    period_px: float
    phase_y: float
    density: float
    gamma: float
    bias: float = 0.0
    max_coverage: float = 1.0


@dataclass(frozen=True)
class RowThresholdParams:
    period_px: int
    phase_y: int
    thresholds: tuple[float, ...]
    gamma: float = 1.0
    density: float = 1.0
    bias: float = 0.0


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    width: int
    height: int
    black_ratio: float


def unpack_black(row: bytes, width: int) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(row, dtype=np.uint8), bitorder="big")[:width]
    return bits == 0


def pack_black(black: np.ndarray) -> bytes:
    white_bits = np.where(black, 0, 1).astype(np.uint8)
    pad = (-white_bits.size) % 8
    if pad:
        white_bits = np.pad(white_bits, (0, pad), constant_values=1)
    return np.packbits(white_bits, bitorder="big").tobytes()


def read_onebit_tile(info, tile: Tile) -> np.ndarray:
    out = np.zeros((tile.height, tile.width), dtype=bool)
    row_byte_start = tile.x // 8
    row_byte_end = (tile.x + tile.width + 7) // 8
    with open(info.path, "rb") as handle:
        for row_index, y in enumerate(range(tile.y, tile.y + tile.height)):
            handle.seek(info.image_offset + y * info.row_bytes + row_byte_start)
            row = handle.read(row_byte_end - row_byte_start)
            bits = np.unpackbits(np.frombuffer(row, dtype=np.uint8), bitorder="big")
            bit_start = tile.x % 8
            out[row_index] = bits[bit_start:bit_start + tile.width] == 0
    return out


def read_rgb_tile(info, tile: Tile) -> np.ndarray:
    out = np.empty((tile.height, tile.width, 3), dtype=np.uint8)
    with open(info.path, "rb") as handle:
        for row_index, y in enumerate(range(tile.y, tile.y + tile.height)):
            offset = info.image_offset + y * info.row_bytes + tile.x * 3
            handle.seek(offset)
            row = handle.read(tile.width * 3)
            out[row_index] = np.frombuffer(row, dtype=np.uint8).reshape(tile.width, 3)
    return out


def read_onebit_rows(info, y_start: int, rows: int) -> np.ndarray:
    out = np.zeros((rows, info.width), dtype=bool)
    with open(info.path, "rb") as handle:
        for row_index, y in enumerate(range(y_start, y_start + rows)):
            handle.seek(info.image_offset + y * info.row_bytes)
            out[row_index] = unpack_black(handle.read(info.row_bytes), info.width)
    return out


def read_rgb_rows(info, y_start: int, rows: int) -> np.ndarray:
    out = np.empty((rows, info.width, 3), dtype=np.uint8)
    with open(info.path, "rb") as handle:
        for row_index, y in enumerate(range(y_start, y_start + rows)):
            handle.seek(info.image_offset + y * info.row_bytes)
            out[row_index] = np.frombuffer(handle.read(info.row_bytes), dtype=np.uint8).reshape(info.width, 3)
    return out


def rgb_to_luma(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.float32)
    return values[..., 0] * 0.299 + values[..., 1] * 0.587 + values[..., 2] * 0.114


def line_screen_from_luma(luma: np.ndarray, y0: int, params: LineScreenParams) -> np.ndarray:
    yn = np.arange(y0, y0 + luma.shape[0], dtype=np.float32)[:, None]
    luma_norm = np.clip(luma / 255.0, 0.0, 1.0)
    darkness = 1.0 - luma_norm
    coverage = params.density * np.power(darkness, params.gamma) + params.bias
    coverage = np.clip(coverage, 0.0, params.max_coverage)
    line_pos = np.mod(yn + params.phase_y, params.period_px)
    return line_pos < (coverage * params.period_px)


def row_threshold_from_luma(luma: np.ndarray, y0: int, params: RowThresholdParams) -> np.ndarray:
    luma_norm = np.clip(luma / 255.0, 0.0, 1.0)
    darkness = 1.0 - luma_norm
    adjusted = np.clip(params.density * np.power(darkness, params.gamma) + params.bias, 0.0, 1.0)
    rows = np.arange(y0, y0 + luma.shape[0], dtype=np.int32)
    phases = np.mod(rows + params.phase_y, params.period_px)
    thresholds = np.asarray(params.thresholds, dtype=np.float32)[phases][:, None]
    return adjusted >= thresholds


def dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.copy()
    h, w = mask.shape
    horizontal = np.zeros_like(mask)
    cols = np.cumsum(mask.astype(np.uint16), axis=1)
    for x in range(w):
        left = max(0, x - radius)
        right = min(w - 1, x + radius)
        total = cols[:, right].copy()
        if left > 0:
            total -= cols[:, left - 1]
        horizontal[:, x] = total > 0

    vertical = np.zeros_like(mask)
    rows = np.cumsum(horizontal.astype(np.uint16), axis=0)
    for y in range(h):
        top = max(0, y - radius)
        bottom = min(h - 1, y + radius)
        total = rows[bottom, :].copy()
        if top > 0:
            total -= rows[top - 1, :]
        vertical[y, :] = total > 0
    return vertical


def dilate_mask_chunk(mask_with_y_overlap: np.ndarray, radius: int, central_start: int, central_rows: int) -> np.ndarray:
    if radius <= 0:
        return mask_with_y_overlap[central_start:central_start + central_rows].copy()
    row_count, width = mask_with_y_overlap.shape
    padded_x = np.pad(mask_with_y_overlap, ((0, 0), (radius, radius)), constant_values=False)
    csum_x = np.concatenate(
        (np.zeros((row_count, 1), dtype=np.uint16), np.cumsum(padded_x.astype(np.uint16), axis=1)),
        axis=1,
    )
    horizontal = (csum_x[:, 2 * radius + 1:2 * radius + 1 + width] - csum_x[:, :width]) > 0
    csum_y = np.concatenate(
        (np.zeros((1, width), dtype=np.uint16), np.cumsum(horizontal.astype(np.uint16), axis=0)),
        axis=0,
    )
    active_rows = []
    for local_y in range(central_start, central_start + central_rows):
        top = max(0, local_y - radius)
        bottom = min(row_count - 1, local_y + radius)
        active_rows.append((csum_y[bottom + 1] - csum_y[top]) > 0)
    return np.stack(active_rows, axis=0)


def active_mismatch(candidate: np.ndarray, target_black: np.ndarray, radius: int) -> tuple[float, int, int]:
    active = dilate_mask(target_black, radius)
    total = int(np.count_nonzero(active))
    if total == 0:
        return 0.0, 0, 0
    mismatches = int(np.count_nonzero(np.logical_xor(candidate, target_black) & active))
    return mismatches / total, mismatches, total


def scan_active_tiles(target_info, tile_size: int, top_k: int) -> list[Tile]:
    tiles_x = math.ceil(target_info.width / tile_size)
    tiles_y = math.ceil(target_info.height / tile_size)
    counts = np.zeros((tiles_y, tiles_x), dtype=np.uint32)
    for y, row in enumerate(delivery.iter_tiff_rows(target_info)):
        black = unpack_black(row, target_info.width)
        ty = y // tile_size
        for tx in np.flatnonzero(np.add.reduceat(black, np.arange(0, target_info.width, tile_size))):
            pass
        split_counts = np.add.reduceat(black.astype(np.uint16), np.arange(0, target_info.width, tile_size))
        counts[ty, : split_counts.size] += split_counts.astype(np.uint32)

    result: list[Tile] = []
    flat_order = np.argsort(counts.ravel())[::-1]
    for index in flat_order:
        count = int(counts.ravel()[index])
        if count <= 0:
            break
        ty, tx = divmod(int(index), tiles_x)
        x = tx * tile_size
        y = ty * tile_size
        width = min(tile_size, target_info.width - x)
        height = min(tile_size, target_info.height - y)
        result.append(Tile(x=x, y=y, width=width, height=height, black_ratio=count / float(width * height)))
        if len(result) >= top_k:
            break
    return result


def make_contact_sheet(entries: list[dict], output: Path, scale: int = 1) -> None:
    if not entries:
        return
    tile_h, tile_w = entries[0]["target"].shape
    panel_w = tile_w * scale
    panel_h = tile_h * scale
    label_h = 34
    sheet = Image.new("RGB", (panel_w * 3, (panel_h + label_h) * len(entries)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, entry in enumerate(entries):
        y = i * (panel_h + label_h)
        target = Image.fromarray(np.where(entry["target"], 0, 255).astype(np.uint8), "L").resize((panel_w, panel_h), Image.Resampling.NEAREST)
        candidate = Image.fromarray(np.where(entry["candidate"], 0, 255).astype(np.uint8), "L").resize((panel_w, panel_h), Image.Resampling.NEAREST)
        diff = np.zeros((*entry["target"].shape, 3), dtype=np.uint8)
        diff[:] = 255
        diff[np.logical_xor(entry["target"], entry["candidate"])] = (255, 0, 0)
        diff_img = Image.fromarray(diff, "RGB").resize((panel_w, panel_h), Image.Resampling.NEAREST)
        sheet.paste(target.convert("RGB"), (0, y + label_h))
        sheet.paste(candidate.convert("RGB"), (panel_w, y + label_h))
        sheet.paste(diff_img, (panel_w * 2, y + label_h))
        draw.text((8, y + 8), f"target x={entry['tile'].x} y={entry['tile'].y}", fill=(0, 0, 0))
        draw.text((panel_w + 8, y + 8), f"candidate mismatch={entry['mismatch']:.4%}", fill=(0, 0, 0))
        draw.text((panel_w * 2 + 8, y + 8), "diff red=mismatch", fill=(0, 0, 0))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def parameter_grid() -> Iterable[LineScreenParams]:
    periods = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40, 52, 64, 80, 96, 128]
    gammas = [0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 2.8, 4.0]
    densities = [0.25, 0.4, 0.65, 1.0, 1.5, 2.2, 3.2, 4.8, 7.0]
    biases = [-0.2, -0.1, 0.0, 0.1, 0.2]
    max_coverages = [0.35, 0.5, 0.7, 0.9, 1.0]
    for period in periods:
        phases = range(0, int(period))
        for phase in phases:
            for gamma in gammas:
                for density in densities:
                    for bias in biases:
                        for max_coverage in max_coverages:
                            yield LineScreenParams(float(period), float(phase), density, gamma, bias, max_coverage)


def refine_grid(best: LineScreenParams) -> Iterable[LineScreenParams]:
    periods = sorted(set(max(1.25, best.period_px + delta) for delta in [-4, -2, -1, -0.5, 0, 0.5, 1, 2, 4]))
    gammas = sorted(set(max(0.1, best.gamma * factor) for factor in [0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5]))
    densities = sorted(set(max(0.01, best.density * factor) for factor in [0.5, 0.7, 0.85, 1.0, 1.15, 1.4, 2.0]))
    biases = sorted(set(best.bias + delta for delta in [-0.1, -0.05, -0.025, 0, 0.025, 0.05, 0.1]))
    max_coverages = sorted(set(min(1.0, max(0.05, best.max_coverage + delta)) for delta in [-0.2, -0.1, -0.05, 0, 0.05, 0.1]))
    for period in periods:
        phase_values = [best.phase_y + delta for delta in [-2, -1, -0.5, 0, 0.5, 1, 2]]
        for phase in phase_values:
            for gamma in gammas:
                for density in densities:
                    for bias in biases:
                        for max_coverage in max_coverages:
                            yield LineScreenParams(float(period), float(phase), density, gamma, bias, max_coverage)


def evaluate_params(params: LineScreenParams, tiles_data: list[dict], radius: int) -> tuple[float, list[dict]]:
    weighted_mismatches = 0
    weighted_total = 0
    details = []
    for item in tiles_data:
        candidate = line_screen_from_luma(item["luma"], item["tile"].y, params)
        ratio, mismatches, total = active_mismatch(candidate, item["target"], radius)
        weighted_mismatches += mismatches
        weighted_total += total
        details.append({"tile": asdict(item["tile"]), "mismatch": ratio, "mismatches": mismatches, "active_pixels": total})
    return weighted_mismatches / weighted_total if weighted_total else 0.0, details


def evaluate_row_threshold(params: RowThresholdParams, tiles_data: list[dict], radius: int) -> tuple[float, list[dict]]:
    weighted_mismatches = 0
    weighted_total = 0
    details = []
    for item in tiles_data:
        candidate = row_threshold_from_luma(item["luma"], item["tile"].y, params)
        ratio, mismatches, total = active_mismatch(candidate, item["target"], radius)
        weighted_mismatches += mismatches
        weighted_total += total
        details.append({"tile": asdict(item["tile"]), "mismatch": ratio, "mismatches": mismatches, "active_pixels": total})
    return weighted_mismatches / weighted_total if weighted_total else 0.0, details


def fit_row_thresholds(
    period: int,
    phase: int,
    tiles_data: list[dict],
    *,
    gamma: float,
    density: float,
    bias: float,
    radius: int,
) -> tuple[RowThresholdParams, float, list[dict]]:
    best_thresholds: list[float] = []
    for screen_phase in range(period):
        values = []
        labels = []
        for item in tiles_data:
            tile: Tile = item["tile"]
            rows = np.arange(tile.y, tile.y + tile.height, dtype=np.int32)
            row_mask = np.mod(rows + phase, period) == screen_phase
            if not np.any(row_mask):
                continue
            cached_active = item.get("active_32") if radius == 32 else None
            active = (cached_active if cached_active is not None else dilate_mask(item["target"], radius))[row_mask]
            luma = item["luma"][row_mask]
            target = item["target"][row_mask]
            luma_norm = np.clip(luma / 255.0, 0.0, 1.0)
            darkness = 1.0 - luma_norm
            adjusted = np.clip(density * np.power(darkness, gamma) + bias, 0.0, 1.0)
            if not np.any(active):
                continue
            values.append(adjusted[active].reshape(-1))
            labels.append(target[active].reshape(-1))
        if not values:
            best_thresholds.append(1.1)
            continue
        v = np.concatenate(values)
        y = np.concatenate(labels)
        # Candidate black when v >= threshold. Test quantiles plus fixed extremes.
        quantiles = np.quantile(v, np.linspace(0.0, 1.0, 33))
        candidates = np.unique(np.clip(np.concatenate(([0.01, 0.02, 0.05], quantiles, [0.95, 1.0, 1.1])), 0.01, 1.1))
        best_error = y.size + 1
        best_t = 1.1
        for threshold in candidates:
            pred = v >= threshold
            error = int(np.count_nonzero(np.logical_xor(pred, y)))
            if error < best_error:
                best_error = error
                best_t = float(threshold)
        best_thresholds.append(best_t)
    params = RowThresholdParams(
        period_px=int(period),
        phase_y=int(phase),
        thresholds=tuple(best_thresholds),
        gamma=float(gamma),
        density=float(density),
        bias=float(bias),
    )
    score, details = evaluate_row_threshold(params, tiles_data, radius)
    return params, score, details


def load_tile_data(rgb_info, target_info, tiles: list[Tile]) -> list[dict]:
    data = []
    for tile in tiles:
        target = read_onebit_tile(target_info, tile)
        rgb = read_rgb_tile(rgb_info, tile)
        data.append({"tile": tile, "target": target, "luma": rgb_to_luma(rgb), "active_32": dilate_mask(target, 32)})
    return data


def full_active_mismatch(rgb_info, target_info, params: LineScreenParams, radius: int) -> dict:
    # Full-size exact 32px dilation is expensive; use streaming vertical window around
    # horizontal dilation to avoid unpacking the whole image into memory.
    row_radius = radius
    target_iter = delivery.iter_tiff_rows(target_info)
    rgb_handle = open(rgb_info.path, "rb")
    try:
        target_window: list[np.ndarray] = []
        generated_window: list[np.ndarray] = []
        y_loaded = 0
        total_mismatch = 0
        total_active = 0

        def load_row(y: int) -> None:
            nonlocal y_loaded
            target_row = unpack_black(next(target_iter), target_info.width)
            rgb_handle.seek(rgb_info.image_offset + y * rgb_info.row_bytes)
            rgb = np.frombuffer(rgb_handle.read(rgb_info.row_bytes), dtype=np.uint8).reshape(rgb_info.width, 3)
            luma = rgb_to_luma(rgb[None, :, :])
            generated = line_screen_from_luma(luma, y, params)[0]
            target_window.append(target_row)
            generated_window.append(generated)
            y_loaded += 1

        for _ in range(min(target_info.height, row_radius + 1)):
            load_row(y_loaded)

        for y in range(target_info.height):
            while y_loaded < target_info.height and y_loaded <= y + row_radius:
                load_row(y_loaded)
            top = max(0, y - row_radius)
            center = y - top
            horiz_any = []
            for row in target_window:
                padded = np.pad(row, (radius, radius), constant_values=False)
                csum = np.cumsum(padded.astype(np.uint16))
                counts = csum[2 * radius:] - np.concatenate(([0], csum[:-2 * radius - 1]))
                horiz_any.append(counts[: target_info.width] > 0)
            active = np.any(np.stack(horiz_any, axis=0), axis=0)
            target_black = target_window[center]
            candidate = generated_window[center]
            total_active += int(np.count_nonzero(active))
            total_mismatch += int(np.count_nonzero(np.logical_xor(candidate, target_black) & active))
            if y - row_radius >= 0:
                target_window.pop(0)
                generated_window.pop(0)
            if y % 1000 == 0:
                print(f"full_eval {y}/{target_info.height} mismatch={total_mismatch / max(1, total_active):.6f}", flush=True)
        return {
            "active_mismatch_ratio": total_mismatch / total_active if total_active else 0.0,
            "active_mismatch_count": total_mismatch,
            "active_pixels": total_active,
        }
    finally:
        rgb_handle.close()


def full_active_mismatch_row_threshold(
    rgb_info,
    target_info,
    params: RowThresholdParams,
    radius: int,
    *,
    chunk_rows: int = 512,
) -> dict:
    started = time.perf_counter()
    total_mismatch = 0
    total_active = 0
    total_black = 0
    for y0 in range(0, target_info.height, chunk_rows):
        rows = min(chunk_rows, target_info.height - y0)
        ext_y0 = max(0, y0 - radius)
        ext_y1 = min(target_info.height, y0 + rows + radius)
        ext_rows = ext_y1 - ext_y0
        central_start = y0 - ext_y0
        target_ext = read_onebit_rows(target_info, ext_y0, ext_rows)
        target = target_ext[central_start:central_start + rows]
        active = dilate_mask_chunk(target_ext, radius, central_start, rows)
        rgb = read_rgb_rows(rgb_info, y0, rows)
        candidate = row_threshold_from_luma(rgb_to_luma(rgb), y0, params)
        total_active += int(np.count_nonzero(active))
        total_mismatch += int(np.count_nonzero(np.logical_xor(candidate, target) & active))
        total_black += int(np.count_nonzero(candidate))
        print(
            f"full_row_threshold {y0 + rows}/{target_info.height} "
            f"active_mismatch={total_mismatch / max(1, total_active):.6f}",
            flush=True,
        )
    return {
        "active_mismatch_ratio": total_mismatch / total_active if total_active else 0.0,
        "active_mismatch_count": total_mismatch,
        "active_pixels": total_active,
        "generated_black_ratio": total_black / float(target_info.width * target_info.height),
        "elapsed_seconds": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interlaced", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--tile-count", type=int, default=12)
    parser.add_argument("--mask-radius", type=int, default=32)
    parser.add_argument("--full-eval", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rgb_info = delivery.read_uncompressed_rgb_tiff_info(str(args.interlaced))
    target_info = delivery.read_uncompressed_one_bit_tiff_info(str(args.target))
    if (rgb_info.width, rgb_info.height) != (target_info.width, target_info.height):
        raise SystemExit(f"shape mismatch: interlaced={rgb_info.width}x{rgb_info.height} target={target_info.width}x{target_info.height}")

    print("scanning active tiles", flush=True)
    tiles = scan_active_tiles(target_info, args.tile_size, args.tile_count)
    print(json.dumps([asdict(tile) for tile in tiles], indent=2), flush=True)
    tiles_data = load_tile_data(rgb_info, target_info, tiles)

    best_params = None
    best_score = float("inf")
    best_details = []
    checked = 0
    for params in parameter_grid():
        score, details = evaluate_params(params, tiles_data, args.mask_radius)
        checked += 1
        if score < best_score:
            best_score = score
            best_params = params
            best_details = details
            print(f"coarse best {best_score:.6f} {best_params}", flush=True)
        if checked % 5000 == 0:
            print(f"coarse checked={checked} best={best_score:.6f}", flush=True)

    assert best_params is not None
    for pass_index in range(3):
        local_best = best_params
        for params in refine_grid(best_params):
            score, details = evaluate_params(params, tiles_data, args.mask_radius)
            if score < best_score:
                best_score = score
                local_best = params
                best_details = details
                print(f"refine{pass_index} best {best_score:.6f} {local_best}", flush=True)
        best_params = local_best

    contact_entries = []
    for item, detail in zip(tiles_data, best_details):
        candidate = line_screen_from_luma(item["luma"], item["tile"].y, best_params)
        contact_entries.append(
            {
                "tile": item["tile"],
                "target": item["target"],
                "candidate": candidate,
                "mismatch": detail["mismatch"],
            }
        )
    make_contact_sheet(contact_entries, args.output_dir / "best_tiles_side_by_side.png")

    report = {
        "interlaced": str(args.interlaced),
        "target": str(args.target),
        "width": rgb_info.width,
        "height": rgb_info.height,
        "mask_radius": args.mask_radius,
        "tile_size": args.tile_size,
        "tiles": [asdict(tile) for tile in tiles],
        "best_tile_active_mismatch_ratio": best_score,
        "best_params": asdict(best_params),
        "tile_details": best_details,
        "elapsed_seconds": time.perf_counter() - started,
        "contact_sheet": str(args.output_dir / "best_tiles_side_by_side.png"),
    }
    if args.full_eval:
        report["full_eval"] = full_active_mismatch(rgb_info, target_info, best_params, args.mask_radius)
    (args.output_dir / "calibration_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
