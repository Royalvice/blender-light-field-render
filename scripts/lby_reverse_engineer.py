"""Developer tooling for reverse-engineering the LBY film halftone.

This script is intentionally outside the Blender add-on runtime. It reads local
probe/reference assets from ignored directories, summarizes source/target data,
and provides streaming 1-bit TIFF comparisons without storing target masks or
per-pixel residuals in the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
import sys
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_PATH = REPO_ROOT / "light_field_plugin" / "core" / "delivery.py"


def load_delivery_module():
    spec = importlib.util.spec_from_file_location("delivery_under_lby_reverse", DELIVERY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


delivery = load_delivery_module()


@dataclass(frozen=True)
class Region:
    name: str
    y0: int
    y1: int


PROBE_REGIONS = (
    Region("orientation_and_view_id_header", 0, 360),
    Region("binary_gray_code_view_id", 360, 920),
    Region("continuous_view_color_decode", 920, 1340),
    Region("shared_coordinate_ramps", 1340, 1940),
    Region("shared_resolution_frequency_chart", 1940, 2300),
    Region("shared_halftone_tone_scale", 2300, 3050),
    Region("shared_screen_angle_frequency_chart", 3050, 3440),
    Region("view_dependent_impulse_footer", 3440, 3651),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_paths(source_dir: Path) -> list[Path]:
    suffixes = {".jpg", ".jpeg", ".png"}
    return sorted(
        [path for path in source_dir.rglob("*") if path.suffix.lower() in suffixes],
        key=lambda path: path.name.lower(),
    )


def read_source_rgb_pillow(path: str):
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return delivery.PngImage(rgb.width, rgb.height, rgb.tobytes())


def source_dimensions(source_dir: Path) -> tuple[int, int]:
    paths = image_paths(source_dir)
    if not paths:
        raise SystemExit(f"No PNG/JPG source images found in {source_dir}")
    with Image.open(paths[0]) as image:
        return image.width, image.height


def pack_black_row(black_row, width: int) -> bytes:
    if hasattr(black_row, "dtype"):
        white_bits = 1 - black_row.astype(np.uint8)
    else:
        white_bits = np.asarray([0 if value else 1 for value in black_row], dtype=np.uint8)
    packed = np.packbits(white_bits, bitorder="big")
    return packed.tobytes()


def compare_candidate_row(black_row, target_row: bytes, width: int) -> tuple[int, int]:
    packed = pack_black_row(black_row, width)
    mismatch = delivery._count_mismatch_in_packed_rows(packed, target_row, width)
    black = delivery._count_black_in_packed_row(packed, width)
    return mismatch, black


def profile_summary(profile, ppi: int) -> dict:
    payload = delivery.halftone_profile_to_manifest(profile)
    payload["screen_effective_period_px"] = delivery.effective_halftone_period_px(profile, ppi, None)
    return payload


def unpack_black_row(row: bytes, width: int) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(row, dtype=np.uint8), bitorder="big")[:width]
    return bits == 0


def hash_threshold_u32(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.uint32, copy=False)
    values ^= values >> np.uint32(16)
    values *= np.uint32(0x7FEB352D)
    values ^= values >> np.uint32(15)
    values *= np.uint32(0x846CA68B)
    values ^= values >> np.uint32(16)
    return ((values & np.uint32(0x00FFFFFF)).astype(np.float32) + 0.5) / float(1 << 24)


def luma_to_tone(luma: np.ndarray, *, gamma: float, density: float, bias: float) -> np.ndarray:
    darkness = 1.0 - np.clip(luma.astype(np.float32) / 255.0, 0.0, 1.0)
    return np.clip(density * np.power(darkness, max(1.0e-6, gamma)) + bias, 0.0, 1.0)


@dataclass(frozen=True)
class SampleSpec:
    name: str
    source_dir: Path
    target_tiff: Path
    x0: int
    y0: int
    width: int
    rows: int
    ppi: int


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    name: str
    params: dict


class SourceRowSampler:
    """Small-window source sampler for reverse-engineering.

    This intentionally does not use the add-on InterlaceRenderer because that
    renderer eagerly loads every source image. The sampler keeps PIL images open
    lazily and caches only the source scanlines needed by the sampled windows.
    """

    def __init__(
        self,
        source_dir: Path,
        *,
        output_width: int,
        output_height: int,
        ppi: int,
        pe: float,
        angle_degrees: float,
        offset: float,
        reverse_views: bool,
        coordinate_mode: str = "align_corners",
        source_x_scale: float = 1.0,
        source_y_scale: float = 1.0,
        source_x_offset: float = 0.0,
        source_y_offset: float = 0.0,
        row_cache_limit: int = 4096,
    ):
        if abs(float(angle_degrees)) > 1.0e-9:
            raise SystemExit("SourceRowSampler currently supports angle=0 only; keep interlace formula unchanged and score angle separately.")
        self.paths = image_paths(source_dir)
        if not self.paths:
            raise SystemExit(f"No PNG/JPG sources found in {source_dir}")
        self.images = [Image.open(path) for path in self.paths]
        self.source_width, self.source_height = self.images[0].size
        for path, image in zip(self.paths, self.images):
            if image.size != (self.source_width, self.source_height):
                raise SystemExit(f"Source dimensions differ: {path} has {image.size}")
        self.output_width = int(output_width)
        self.output_height = int(output_height)
        self.ppi = int(ppi)
        self.pe = float(pe)
        self.offset = float(offset)
        self.reverse_views = bool(reverse_views)
        self.coordinate_mode = coordinate_mode
        self.source_x_scale = float(source_x_scale)
        self.source_y_scale = float(source_y_scale)
        self.source_x_offset = float(source_x_offset)
        self.source_y_offset = float(source_y_offset)
        self.view_order = delivery.build_view_order(len(self.paths), reverse_views)
        self._row_cache: OrderedDict[tuple[int, int], np.ndarray] = OrderedDict()
        self._row_cache_limit = int(row_cache_limit)

    def _map_coordinate(self, final: np.ndarray | float, *, source_size: int, output_size: int):
        if output_size <= 1:
            return np.zeros_like(final, dtype=np.float64) if hasattr(final, "shape") else 0.0
        if self.coordinate_mode == "align_corners":
            return final * ((source_size - 1) / float(output_size - 1))
        if self.coordinate_mode == "half_pixel":
            return (final + 0.5) * (source_size / float(output_size)) - 0.5
        if self.coordinate_mode == "asymmetric":
            return final * (source_size / float(output_size))
        if self.coordinate_mode == "floor_nearest":
            return np.floor(final * (source_size / float(output_size)))
        raise SystemExit(f"Unknown coordinate mode: {self.coordinate_mode}")

    def close(self) -> None:
        for image in self.images:
            image.close()

    def _source_row(self, view: int, y: int) -> np.ndarray:
        y = max(0, min(self.source_height - 1, int(y)))
        key = (int(view), y)
        cached = self._row_cache.get(key)
        if cached is not None:
            self._row_cache.move_to_end(key)
            return cached
        image = self.images[int(view)]
        row = np.asarray(image.crop((0, y, self.source_width, y + 1)).convert("RGB"), dtype=np.uint8)[0]
        self._row_cache[key] = row
        if len(self._row_cache) > self._row_cache_limit:
            self._row_cache.popitem(last=False)
        return row

    def rgb_window(self, *, x0: int, y0: int, width: int, rows: int) -> np.ndarray:
        x0 = max(0, min(self.output_width - 1, int(x0)))
        width = max(1, min(int(width), self.output_width - x0))
        y0 = max(0, min(self.output_height - 1, int(y0)))
        rows = max(1, min(int(rows), self.output_height - y0))

        final_x = np.arange(x0, x0 + width, dtype=np.float64)
        source_x = self._map_coordinate(final_x, source_size=self.source_width, output_size=self.output_width) * self.source_x_scale + self.source_x_offset
        source_x = np.clip(source_x, 0.0, float(self.source_width - 1))
        sx0 = np.floor(source_x).astype(np.int64)
        sx1 = np.minimum(self.source_width - 1, sx0 + 1)
        tx = (source_x - sx0).astype(np.float32)

        period_px = float(self.ppi) / float(self.pe)
        a_value = np.mod(final_x + self.offset, period_px)
        view = np.floor(a_value / (period_px / len(self.paths))).astype(np.int64) % len(self.paths)
        source_indices = np.asarray([self.view_order[int(v)] for v in view], dtype=np.int64)

        out = np.empty((rows, width, 3), dtype=np.uint8)
        for row_index, final_y in enumerate(range(y0, y0 + rows)):
            sy = float(self._map_coordinate(float(final_y), source_size=self.source_height, output_size=self.output_height)) * self.source_y_scale + self.source_y_offset
            sy = max(0.0, min(float(self.source_height - 1), sy))
            sy0 = int(math.floor(sy))
            sy1 = min(self.source_height - 1, sy0 + 1)
            ty = float(sy - sy0)
            for source_index in np.unique(source_indices):
                mask = source_indices == source_index
                idx = np.nonzero(mask)[0]
                top = self._source_row(int(source_index), sy0)
                bottom = self._source_row(int(source_index), sy1)
                top_rgb = top[sx0[idx]].astype(np.float32) * (1.0 - tx[idx, None]) + top[sx1[idx]].astype(np.float32) * tx[idx, None]
                bottom_rgb = bottom[sx0[idx]].astype(np.float32) * (1.0 - tx[idx, None]) + bottom[sx1[idx]].astype(np.float32) * tx[idx, None]
                out[row_index, idx] = np.rint(top_rgb * (1.0 - ty) + bottom_rgb * ty).clip(0, 255).astype(np.uint8)
        return out


def read_target_window(info, *, x0: int, y0: int, width: int, rows: int) -> np.ndarray:
    x0 = max(0, min(info.width - 1, int(x0)))
    width = max(1, min(int(width), info.width - x0))
    y0 = max(0, min(info.height - 1, int(y0)))
    rows = max(1, min(int(rows), info.height - y0))
    target = np.empty((rows, width), dtype=bool)
    with open(info.path, "rb") as handle:
        for row_index, y in enumerate(range(y0, y0 + rows)):
            handle.seek(info.image_offset + y * info.row_bytes)
            target[row_index] = unpack_black_row(handle.read(info.row_bytes), info.width)[x0:x0 + width]
    return target


def candidate_black(candidate: CandidateSpec, rgb: np.ndarray, *, x0: int, y0: int, ppi: int) -> np.ndarray:
    yy = np.arange(y0, y0 + rgb.shape[0], dtype=np.float32)[:, None]
    xx = np.arange(x0, x0 + rgb.shape[1], dtype=np.float32)[None, :]
    luma = 0.299 * rgb[..., 0].astype(np.float32) + 0.587 * rgb[..., 1].astype(np.float32) + 0.114 * rgb[..., 2].astype(np.float32)
    p = candidate.params
    tone = luma_to_tone(luma, gamma=float(p.get("gamma", 1.0)), density=float(p.get("density", 1.0)), bias=float(p.get("bias", 0.0)))
    family = candidate.family

    if family == "row_threshold":
        period = float(p["period_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_with_ppi") else float(p["period_px"])
        thresholds = np.asarray(p["thresholds"], dtype=np.float32)
        if p.get("phase_mode", "modulo") == "normalized":
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period)) / max(1.0, period) * thresholds.size).astype(np.int64) % thresholds.size
        else:
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period))).astype(np.int64) % thresholds.size
        return tone >= thresholds[phase]

    if family == "row_transition_dither":
        period = float(p["period_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_with_ppi") else float(p["period_px"])
        thresholds = np.asarray(p["thresholds"], dtype=np.float32)
        phase_mode = p.get("phase_mode", "modulo")
        if phase_mode == "normalized":
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period)) / max(1.0, period) * thresholds.size).astype(np.int64) % thresholds.size
        else:
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period))).astype(np.int64) % thresholds.size
        row_threshold = thresholds[phase]
        transition_width = max(1.0e-6, float(p.get("transition_width", 0.05)))
        low = row_threshold - transition_width * 0.5
        high = row_threshold + transition_width * 0.5
        fraction = np.clip((tone - low) / transition_width, 0.0, 1.0)
        dither_kind = p.get("dither", "bayer")
        if dither_kind == "hash":
            seed = np.uint32(int(p.get("seed", 0)) & 0xFFFFFFFF)
            seed_mix = np.uint32((int(seed) * 0x85EBCA6B) & 0xFFFFFFFF)
            values = xx.astype(np.uint32) ^ (yy.astype(np.uint32) * np.uint32(0x9E3779B1)) ^ seed_mix
            dither = hash_threshold_u32(values)
        else:
            matrix = bayer_matrix(int(p.get("matrix_size", 4)))
            mx = np.mod(xx.astype(np.int64) + int(p.get("phase_x", 0)), matrix.shape[1])
            my = np.mod(yy.astype(np.int64) + int(p.get("phase_y_dither", 0)), matrix.shape[0])
            dither = matrix[my, mx]
        return np.where(tone >= high, True, np.where(tone <= low, False, fraction >= dither))

    if family == "row_screen_diffusion":
        period = float(p["period_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_with_ppi") else float(p["period_px"])
        thresholds = np.asarray(p["thresholds"], dtype=np.float32)
        phase_mode = p.get("phase_mode", "modulo")
        rows_abs = np.arange(y0, y0 + rgb.shape[0], dtype=np.float32)
        if phase_mode == "normalized":
            row_phase = np.floor(np.mod(rows_abs + float(p.get("phase_y", 0.0)), max(1.0, period)) / max(1.0, period) * thresholds.size).astype(np.int64) % thresholds.size
        else:
            row_phase = np.floor(np.mod(rows_abs + float(p.get("phase_y", 0.0)), max(1.0, period))).astype(np.int64) % thresholds.size
        row_thresholds = thresholds[row_phase][:, None]
        return screen_diffusion_black(
            tone,
            row_thresholds,
            family=str(p.get("kernel", "fs")),
            strength=float(p.get("strength", 1.0)),
            serpentine=bool(p.get("serpentine", False)),
            threshold_bias=float(p.get("threshold_bias", 0.0)),
        )

    if family == "row_micro_spot":
        period = float(p["period_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_with_ppi") else float(p["period_px"])
        thresholds = np.asarray(p["thresholds"], dtype=np.float32)
        phase_mode = p.get("phase_mode", "modulo")
        if phase_mode == "normalized":
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period)) / max(1.0, period) * thresholds.size).astype(np.int64) % thresholds.size
        else:
            phase = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period))).astype(np.int64) % thresholds.size
        row_threshold = thresholds[phase]
        transition_width = max(1.0e-6, float(p.get("transition_width", 0.05)))
        low = row_threshold - transition_width * 0.5
        high = row_threshold + transition_width * 0.5
        coverage = np.clip((tone - low) / transition_width, 0.0, 1.0)
        cell_x = max(1.0, float(p.get("cell_x", 2.5)))
        cell_y = max(1.0, float(p.get("cell_y", cell_x)))
        angle = math.radians(float(p.get("angle", 0.0)))
        xr = xx * math.cos(angle) + yy * math.sin(angle) + float(p.get("phase_x", 0.0))
        yr = -xx * math.sin(angle) + yy * math.cos(angle) + float(p.get("phase_y_spot", 0.0))
        u = ((xr / cell_x) - np.floor(xr / cell_x)) * 2.0 - 1.0
        v = ((yr / cell_y) - np.floor(yr / cell_y)) * 2.0 - 1.0
        shape = str(p.get("shape", "line")).lower()
        if shape == "diamond":
            metric = (np.abs(u) + np.abs(v)) * 0.5
            cutoff = coverage
        elif shape == "round":
            metric = np.sqrt(u * u + v * v)
            cutoff = np.sqrt(coverage)
        else:
            metric = np.abs(u)
            cutoff = coverage
        spot = metric <= cutoff
        return np.where(tone >= high, True, np.where(tone <= low, False, spot))

    if family == "periodic_threshold":
        period_x = float(p["period_x_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_x_with_ppi") else float(p["period_x_px"])
        period_y = float(p["period_y_px"]) * (float(ppi) / float(p.get("reference_ppi", ppi))) if p.get("scale_y_with_ppi") else float(p["period_y_px"])
        thresholds = np.asarray(p["thresholds"], dtype=np.float32)
        px = np.floor(np.mod(xx + float(p.get("phase_x", 0.0)), max(1.0, period_x)) / max(1.0, period_x) * thresholds.shape[1]).astype(np.int64) % thresholds.shape[1]
        py = np.floor(np.mod(yy + float(p.get("phase_y", 0.0)), max(1.0, period_y)) / max(1.0, period_y) * thresholds.shape[0]).astype(np.int64) % thresholds.shape[0]
        return tone >= thresholds[py, px]

    if family == "am_spot":
        cell = max(2.0, float(ppi) / float(p.get("lpi", 200.0)))
        angle = math.radians(float(p.get("angle", 0.0)))
        xr = xx * math.cos(angle) + yy * math.sin(angle) + float(p.get("phase_x", 0.0))
        yr = -xx * math.sin(angle) + yy * math.cos(angle) + float(p.get("phase_y", 0.0))
        u = ((xr / cell) - np.floor(xr / cell)) * 2.0 - 1.0
        v = ((yr / cell) - np.floor(yr / cell)) * 2.0 - 1.0
        shape = p.get("shape", "round")
        if shape == "diamond":
            metric = (np.abs(u) + np.abs(v)) / 2.0
            threshold = tone
        elif shape == "line":
            metric = np.abs(v)
            threshold = tone
        else:
            metric = np.sqrt(u * u + v * v)
            threshold = np.sqrt(tone)
        return tone >= 1.0 if np.all(tone >= 1.0) else ((tone > 0.0) & (metric <= threshold))

    if family == "ordered_matrix":
        matrix = np.asarray(p["matrix"], dtype=np.float32)
        mx = np.mod(xx.astype(np.int64) + int(p.get("phase_x", 0)), matrix.shape[1])
        my = np.mod(yy.astype(np.int64) + int(p.get("phase_y", 0)), matrix.shape[0])
        return tone >= matrix[my, mx]

    if family in {"hash_fm", "blue_noise_hash", "green_noise_hash"}:
        seed = np.uint32(int(p.get("seed", 0)) & 0xFFFFFFFF)
        seed_mix = np.uint32((int(seed) * 0x85EBCA6B) & 0xFFFFFFFF)
        values = xx.astype(np.uint32) ^ (yy.astype(np.uint32) * np.uint32(0x9E3779B1)) ^ seed_mix
        threshold = hash_threshold_u32(values)
        if family == "green_noise_hash":
            threshold = np.clip(0.75 * threshold + 0.25 * (0.5 + 0.5 * np.sin((xx + yy) / max(2.0, float(p.get("cluster_period", 24.0))))), 0.0, 1.0)
        return tone >= threshold

    if family == "hybrid_am_fm":
        am = candidate_black(CandidateSpec("am_spot", candidate.name + "_am", p), rgb, x0=x0, y0=y0, ppi=ppi)
        fm = candidate_black(CandidateSpec("hash_fm", candidate.name + "_fm", p), rgb, x0=x0, y0=y0, ppi=ppi)
        low = float(p.get("fm_low", 0.18))
        high = float(p.get("fm_high", 0.82))
        return np.where((tone < low) | (tone > high), fm, am)

    if family in {"fs_diffusion", "jjn_diffusion"}:
        return diffusion_black(tone, family=family, serpentine=bool(p.get("serpentine", False)))

    raise ValueError(f"Unknown candidate family: {family}")


def screen_diffusion_black(
    tone: np.ndarray,
    threshold: np.ndarray,
    *,
    family: str,
    strength: float,
    serpentine: bool,
    threshold_bias: float,
) -> np.ndarray:
    rows, width = tone.shape
    work = tone.astype(np.float32, copy=True)
    out = np.zeros((rows, width), dtype=bool)
    family = family.lower()
    if family == "stucki":
        kernel = [
            (1, 0, 8 / 42), (2, 0, 4 / 42),
            (-2, 1, 2 / 42), (-1, 1, 4 / 42), (0, 1, 8 / 42), (1, 1, 4 / 42), (2, 1, 2 / 42),
            (-2, 2, 1 / 42), (-1, 2, 2 / 42), (0, 2, 4 / 42), (1, 2, 2 / 42), (2, 2, 1 / 42),
        ]
    elif family == "jjn":
        kernel = [
            (1, 0, 7 / 48), (2, 0, 5 / 48),
            (-2, 1, 3 / 48), (-1, 1, 5 / 48), (0, 1, 7 / 48), (1, 1, 5 / 48), (2, 1, 3 / 48),
            (-2, 2, 1 / 48), (-1, 2, 3 / 48), (0, 2, 5 / 48), (1, 2, 3 / 48), (2, 2, 1 / 48),
        ]
    else:
        kernel = [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)]
    strength = max(0.0, float(strength))
    for y in range(rows):
        xs = range(width - 1, -1, -1) if serpentine and y % 2 else range(width)
        direction = -1 if serpentine and y % 2 else 1
        for x in xs:
            old = max(0.0, min(1.0, float(work[y, x])))
            t = max(0.0, min(1.0, float(threshold[y, 0]) + threshold_bias))
            new = 1.0 if old >= t else 0.0
            out[y, x] = new >= 0.5
            err = old - new
            for dx, dy, weight in kernel:
                tx = x + dx * direction
                ty = y + dy
                if 0 <= tx < width and 0 <= ty < rows:
                    work[ty, tx] += err * weight * strength
    return out


def diffusion_black(tone: np.ndarray, *, family: str, serpentine: bool) -> np.ndarray:
    rows, width = tone.shape
    work = tone.astype(np.float32, copy=True)
    out = np.zeros((rows, width), dtype=bool)
    if family == "jjn_diffusion":
        kernel = [
            (1, 0, 7 / 48), (2, 0, 5 / 48),
            (-2, 1, 3 / 48), (-1, 1, 5 / 48), (0, 1, 7 / 48), (1, 1, 5 / 48), (2, 1, 3 / 48),
            (-2, 2, 1 / 48), (-1, 2, 3 / 48), (0, 2, 5 / 48), (1, 2, 3 / 48), (2, 2, 1 / 48),
        ]
    else:
        kernel = [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)]
    for y in range(rows):
        xs = range(width - 1, -1, -1) if serpentine and y % 2 else range(width)
        direction = -1 if serpentine and y % 2 else 1
        for x in xs:
            old = max(0.0, min(1.0, float(work[y, x])))
            new = 1.0 if old >= 0.5 else 0.0
            out[y, x] = new >= 0.5
            err = old - new
            for dx, dy, weight in kernel:
                tx = x + dx * direction
                ty = y + dy
                if 0 <= tx < width and 0 <= ty < rows:
                    work[ty, tx] += err * weight
    return out


def bayer_matrix(size: int) -> np.ndarray:
    matrix = np.array([[0]], dtype=np.float32)
    while matrix.shape[0] < size:
        matrix = np.block([[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]])
    return (matrix + 0.5) / float(size * size)


def candidate_grid() -> list[CandidateSpec]:
    thresholds = list(delivery.LBY_LINE_THRESHOLDS)
    candidates: list[CandidateSpec] = []
    for phase in [0, 1, 2, 4, 8, 12, 16]:
        for gamma in [0.18, 0.25, 0.36, 0.5, 1.0]:
            candidates.append(CandidateSpec("row_threshold", f"row18_phase{phase}_g{gamma}", {
                "period_px": 18.0,
                "reference_ppi": 4000,
                "scale_with_ppi": True,
                "phase_y": phase,
                "gamma": gamma,
                "density": 0.25,
                "bias": -0.05,
                "thresholds": thresholds,
            }))
    for shape in ["round", "diamond", "line"]:
        for lpi in [100, 150, 175, 200, 225]:
            candidates.append(CandidateSpec("am_spot", f"am_{shape}_{lpi}", {
                "shape": shape,
                "lpi": lpi,
                "angle": 0.0,
                "gamma": 1.0,
                "density": 1.0,
                "bias": 0.0,
            }))
    for size in [4, 8, 16]:
        matrix = bayer_matrix(size).tolist()
        candidates.append(CandidateSpec("ordered_matrix", f"bayer{size}", {"matrix": matrix, "gamma": 1.0, "density": 1.0, "bias": 0.0}))
        candidates.append(CandidateSpec("ordered_matrix", f"bayer{size}_dark", {"matrix": matrix, "gamma": 0.5, "density": 0.6, "bias": -0.05}))
    for family in ["hash_fm", "blue_noise_hash", "green_noise_hash"]:
        for seed in [0, 1, 17, 149, 624, 20260624]:
            candidates.append(CandidateSpec(family, f"{family}_{seed}", {
                "seed": seed,
                "gamma": 1.0 if family == "hash_fm" else 0.7,
                "density": 1.0,
                "bias": 0.0,
                "cluster_period": 24.0,
            }))
    for family in ["fs_diffusion", "jjn_diffusion"]:
        for serpentine in [False, True]:
            candidates.append(CandidateSpec(family, f"{family}_{'serp' if serpentine else 'scan'}", {
                "gamma": 1.0,
                "density": 1.0,
                "bias": 0.0,
                "serpentine": serpentine,
            }))
    for lpi in [150, 200]:
        for seed in [0, 624]:
            candidates.append(CandidateSpec("hybrid_am_fm", f"hybrid_{lpi}_{seed}", {
                "shape": "round",
                "lpi": lpi,
                "angle": 0.0,
                "seed": seed,
                "gamma": 0.8,
                "density": 1.0,
                "bias": 0.0,
                "fm_low": 0.18,
                "fm_high": 0.82,
            }))
    return candidates


def default_sample_specs(root: Path, *, rows: int, width: int) -> list[SampleSpec]:
    samples = [
        ("624_probe", root / "extracted", root / "raw" / "624_dats_dats.tif"),
        ("618_4000", root / "reference_618" / "source_views", root / "reference_618" / "618空间_dats_dats(1).tif"),
        ("618_8000", root / "reference_618" / "source_views", root / "reference_618" / "618空间高dpi_dats_dats.tif"),
    ]
    specs: list[SampleSpec] = []
    region_points = [0, 360, 920, 1340, 2300, 3050, 3440]
    for label, source_dir, target_tiff in samples:
        info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
        ppi = int(info.dpi_x or 4000)
        sample_width = min(width, info.width)
        x_positions = sorted({0, max(0, (info.width - sample_width) // 2), max(0, info.width - sample_width)})
        for source_y in region_points:
            y = int(round(source_y * info.height / 3651.0))
            y = max(0, min(info.height - rows, y))
            for x in x_positions:
                specs.append(SampleSpec(f"{label}_x{x}_y{y}", source_dir, target_tiff, x, y, sample_width, min(rows, info.height - y), ppi))
    return specs


def filter_informative_samples(
    samples: Sequence[SampleSpec],
    *,
    min_black_ratio: float,
    max_black_ratio: float,
    max_per_target: int,
) -> list[SampleSpec]:
    buckets: dict[Path, list[tuple[float, SampleSpec]]] = {}
    for sample in samples:
        info = delivery.read_uncompressed_one_bit_tiff_info(str(sample.target_tiff))
        target = read_target_window(info, x0=sample.x0, y0=sample.y0, width=sample.width, rows=sample.rows)
        ratio = float(np.mean(target))
        if min_black_ratio <= ratio <= max_black_ratio:
            score = abs(ratio - 0.5)
            buckets.setdefault(sample.target_tiff, []).append((score, sample))
    filtered: list[SampleSpec] = []
    for _target, items in buckets.items():
        items.sort(key=lambda item: item[0])
        filtered.extend(sample for _score, sample in items[:max_per_target])
    if not filtered:
        raise SystemExit("No informative sample windows survived the black-ratio filter")
    return filtered


def filter_samples_by_name(samples: Sequence[SampleSpec], patterns: Sequence[str] | None) -> list[SampleSpec]:
    if not patterns:
        return list(samples)
    lowered = [pattern.lower() for pattern in patterns]
    filtered = [sample for sample in samples if any(pattern in sample.name.lower() for pattern in lowered)]
    if not filtered:
        raise SystemExit(f"No sample windows matched filters: {patterns}")
    return filtered


def score_candidates_on_samples(
    candidates: Sequence[CandidateSpec],
    samples: Sequence[SampleSpec],
    *,
    pe: float,
    angle: float,
    offset: float,
    reverse_views: bool,
    coordinate_mode: str = "align_corners",
    source_x_scale: float = 1.0,
    source_y_scale: float = 1.0,
    source_x_offset: float = 0.0,
    source_y_offset: float = 0.0,
    progress_every: int = 25,
) -> list[dict]:
    grouped: dict[tuple[Path, Path, int], list[SampleSpec]] = {}
    for sample in samples:
        info = delivery.read_uncompressed_one_bit_tiff_info(str(sample.target_tiff))
        grouped.setdefault((sample.source_dir, sample.target_tiff, sample.ppi), []).append(sample)

    sample_payloads: list[dict] = []
    for (source_dir, target_tiff, ppi), group in grouped.items():
        info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
        sampler = SourceRowSampler(
            source_dir,
            output_width=info.width,
            output_height=info.height,
            ppi=ppi,
            pe=pe,
            angle_degrees=angle,
            offset=offset,
            reverse_views=reverse_views,
            coordinate_mode=coordinate_mode,
            source_x_scale=source_x_scale,
            source_y_scale=source_y_scale,
            source_x_offset=source_x_offset,
            source_y_offset=source_y_offset,
        )
        try:
            for sample in group:
                rgb = sampler.rgb_window(x0=sample.x0, y0=sample.y0, width=sample.width, rows=sample.rows)
                target = read_target_window(info, x0=sample.x0, y0=sample.y0, width=sample.width, rows=sample.rows)
                sample_payloads.append({"sample": sample, "rgb": rgb, "target": target})
        finally:
            sampler.close()

    results = []
    for index, candidate in enumerate(candidates, start=1):
        tp = tn = fp = fn = 0
        per_sample = []
        for payload in sample_payloads:
            sample: SampleSpec = payload["sample"]
            black = candidate_black(candidate, payload["rgb"], x0=sample.x0, y0=sample.y0, ppi=sample.ppi)
            target = payload["target"]
            local_tp, local_tn, local_fp, local_fn = confusion_tuple(black, target)
            local_pixels = int(target.size)
            tp += local_tp
            tn += local_tn
            fp += local_fp
            fn += local_fn
            per_sample.append({
                "name": sample.name,
                "mismatch_ratio": (local_fp + local_fn) / local_pixels if local_pixels else 0.0,
                "mismatch_count": local_fp + local_fn,
                "tp": local_tp,
                "tn": local_tn,
                "fp": local_fp,
                "fn": local_fn,
                "pixels": local_pixels,
            })
        pixels = tp + tn + fp + fn
        results.append({
            "candidate": {"family": candidate.family, "name": candidate.name, "params": candidate.params},
            "mismatch_ratio": (fp + fn) / pixels if pixels else 0.0,
            "mismatch_count": fp + fn,
            "pixels": pixels,
            "black_ratio_generated": (tp + fp) / pixels if pixels else 0.0,
            "black_ratio_target": (tp + fn) / pixels if pixels else 0.0,
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "samples": per_sample,
        })
        if progress_every > 0 and index % progress_every == 0:
            best = min(results, key=lambda item: item["mismatch_ratio"])
            print(f"searched {index}/{len(candidates)} best={best['mismatch_ratio']:.6f} {best['candidate']['name']}", file=sys.stderr)
    results.sort(key=lambda item: item["mismatch_ratio"])
    return results


def materialize_sample_payloads(
    samples: Sequence[SampleSpec],
    *,
    pe: float,
    angle: float,
    offset: float,
    reverse_views: bool,
    coordinate_mode: str = "align_corners",
    source_x_scale: float = 1.0,
    source_y_scale: float = 1.0,
    source_x_offset: float = 0.0,
    source_y_offset: float = 0.0,
) -> list[dict]:
    grouped: dict[tuple[Path, Path, int], list[SampleSpec]] = {}
    for sample in samples:
        grouped.setdefault((sample.source_dir, sample.target_tiff, sample.ppi), []).append(sample)
    payloads: list[dict] = []
    for (source_dir, target_tiff, ppi), group in grouped.items():
        info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
        sampler = SourceRowSampler(
            source_dir,
            output_width=info.width,
            output_height=info.height,
            ppi=ppi,
            pe=pe,
            angle_degrees=angle,
            offset=offset,
            reverse_views=reverse_views,
            coordinate_mode=coordinate_mode,
            source_x_scale=source_x_scale,
            source_y_scale=source_y_scale,
            source_x_offset=source_x_offset,
            source_y_offset=source_y_offset,
        )
        try:
            for sample in group:
                payloads.append({
                    "sample": sample,
                    "rgb": sampler.rgb_window(x0=sample.x0, y0=sample.y0, width=sample.width, rows=sample.rows),
                    "target": read_target_window(info, x0=sample.x0, y0=sample.y0, width=sample.width, rows=sample.rows),
                })
        finally:
            sampler.close()
    return payloads


def fit_thresholds_for_payloads(
    payloads: Sequence[dict],
    *,
    gamma: float,
    density: float,
    bias: float,
    period_px: float,
    phase_y: float,
    threshold_count: int,
    reference_ppi: int = 4000,
    phase_mode: str = "modulo",
    scale_with_ppi: bool = True,
) -> CandidateSpec:
    thresholds = []
    for phase in range(threshold_count):
        values = []
        labels = []
        for payload in payloads:
            sample: SampleSpec = payload["sample"]
            rgb = payload["rgb"]
            target = payload["target"]
            luma = 0.299 * rgb[..., 0].astype(np.float32) + 0.587 * rgb[..., 1].astype(np.float32) + 0.114 * rgb[..., 2].astype(np.float32)
            tone = luma_to_tone(luma, gamma=gamma, density=density, bias=bias)
            effective_period = float(period_px) * (float(sample.ppi) / float(reference_ppi)) if scale_with_ppi else float(period_px)
            rows = np.arange(sample.y0, sample.y0 + sample.rows, dtype=np.float32)
            if phase_mode == "normalized":
                row_phase = np.floor(np.mod(rows + phase_y, max(1.0, effective_period)) / max(1.0, effective_period) * threshold_count).astype(np.int64) % threshold_count
            else:
                row_phase = np.floor(np.mod(rows + phase_y, max(1.0, effective_period))).astype(np.int64) % threshold_count
            row_mask = row_phase == phase
            if not np.any(row_mask):
                continue
            values.append(tone[row_mask].reshape(-1))
            labels.append(target[row_mask].reshape(-1))
        if not values:
            thresholds.append(1.1)
            continue
        v = np.concatenate(values)
        y = np.concatenate(labels)
        quantiles = np.quantile(v, np.linspace(0.0, 1.0, 129))
        candidates = np.unique(np.clip(np.concatenate(([0.0, 0.01, 0.02], quantiles, [0.98, 1.0, 1.1])), 0.0, 1.1))
        best_t = 1.1
        best_error = y.size + 1
        for threshold in candidates:
            pred = v >= threshold
            error = int(np.count_nonzero(np.logical_xor(pred, y)))
            if error < best_error:
                best_error = error
                best_t = float(threshold)
        thresholds.append(best_t)
    return CandidateSpec("row_threshold", f"fit_row_period{period_px}_n{threshold_count}_g{gamma}_d{density}_b{bias}_p{phase_y}", {
        "period_px": float(period_px),
        "reference_ppi": int(reference_ppi),
        "scale_with_ppi": bool(scale_with_ppi),
        "phase_y": float(phase_y),
        "gamma": float(gamma),
        "density": float(density),
        "bias": float(bias),
        "phase_mode": phase_mode,
        "thresholds": thresholds,
    })


def fit_periodic_thresholds_for_payloads(
    payloads: Sequence[dict],
    *,
    gamma: float,
    density: float,
    bias: float,
    period_x_px: float,
    period_y_px: float,
    phase_x: float,
    phase_y: float,
    threshold_width: int,
    threshold_height: int,
    scale_x_with_ppi: bool,
    scale_y_with_ppi: bool,
    reference_ppi: int = 4000,
) -> CandidateSpec:
    prepared = []
    for payload in payloads:
        sample: SampleSpec = payload["sample"]
        rgb = payload["rgb"]
        luma = 0.299 * rgb[..., 0].astype(np.float32) + 0.587 * rgb[..., 1].astype(np.float32) + 0.114 * rgb[..., 2].astype(np.float32)
        tone = luma_to_tone(luma, gamma=gamma, density=density, bias=bias)
        effective_x = float(period_x_px) * (float(sample.ppi) / float(reference_ppi)) if scale_x_with_ppi else float(period_x_px)
        effective_y = float(period_y_px) * (float(sample.ppi) / float(reference_ppi)) if scale_y_with_ppi else float(period_y_px)
        xs = np.arange(sample.x0, sample.x0 + sample.width, dtype=np.float32)
        ys = np.arange(sample.y0, sample.y0 + sample.rows, dtype=np.float32)
        px = np.floor(np.mod(xs + phase_x, max(1.0, effective_x)) / max(1.0, effective_x) * threshold_width).astype(np.int64) % threshold_width
        py = np.floor(np.mod(ys + phase_y, max(1.0, effective_y)) / max(1.0, effective_y) * threshold_height).astype(np.int64) % threshold_height
        prepared.append({
            "tone": tone,
            "target": payload["target"],
            "px": px,
            "py": py,
        })
    thresholds = np.zeros((threshold_height, threshold_width), dtype=np.float32)
    for cell_y in range(threshold_height):
        for cell_x in range(threshold_width):
            values = []
            labels = []
            for item in prepared:
                mask = (item["py"][:, None] == cell_y) & (item["px"][None, :] == cell_x)
                if not np.any(mask):
                    continue
                values.append(item["tone"][mask].reshape(-1))
                labels.append(item["target"][mask].reshape(-1))
            if not values:
                thresholds[cell_y, cell_x] = 1.1
                continue
            v = np.concatenate(values)
            y = np.concatenate(labels)
            quantiles = np.quantile(v, np.linspace(0.0, 1.0, 129))
            candidates = np.unique(np.clip(np.concatenate(([0.0, 0.01, 0.02], quantiles, [0.98, 1.0, 1.1])), 0.0, 1.1))
            best_t = 1.1
            best_error = y.size + 1
            for threshold in candidates:
                pred = v >= threshold
                error = int(np.count_nonzero(np.logical_xor(pred, y)))
                if error < best_error:
                    best_error = error
                    best_t = float(threshold)
            thresholds[cell_y, cell_x] = best_t
    return CandidateSpec("periodic_threshold", f"fit_periodic_{threshold_width}x{threshold_height}_g{gamma}_d{density}_b{bias}_px{phase_x}_py{phase_y}", {
        "period_x_px": float(period_x_px),
        "period_y_px": float(period_y_px),
        "reference_ppi": int(reference_ppi),
        "scale_x_with_ppi": bool(scale_x_with_ppi),
        "scale_y_with_ppi": bool(scale_y_with_ppi),
        "phase_x": float(phase_x),
        "phase_y": float(phase_y),
        "gamma": float(gamma),
        "density": float(density),
        "bias": float(bias),
        "thresholds": thresholds.tolist(),
    })


def score_materialized_candidate(candidate: CandidateSpec, payloads: Sequence[dict]) -> dict:
    tp = tn = fp = fn = 0
    per_sample = []
    for payload in payloads:
        sample: SampleSpec = payload["sample"]
        black = candidate_black(candidate, payload["rgb"], x0=sample.x0, y0=sample.y0, ppi=sample.ppi)
        target = payload["target"]
        local_tp, local_tn, local_fp, local_fn = confusion_tuple(black, target)
        local_pixels = int(target.size)
        tp += local_tp
        tn += local_tn
        fp += local_fp
        fn += local_fn
        per_sample.append({
            "name": sample.name,
            "mismatch_ratio": (local_fp + local_fn) / local_pixels if local_pixels else 0.0,
            "mismatch_count": local_fp + local_fn,
            "tp": local_tp,
            "tn": local_tn,
            "fp": local_fp,
            "fn": local_fn,
            "pixels": local_pixels,
        })
    pixels = tp + tn + fp + fn
    return {
        "candidate": {"family": candidate.family, "name": candidate.name, "params": candidate.params},
        "mismatch_ratio": (fp + fn) / pixels if pixels else 0.0,
        "mismatch_count": fp + fn,
        "pixels": pixels,
        "black_ratio_generated": (tp + fp) / pixels if pixels else 0.0,
        "black_ratio_target": (tp + fn) / pixels if pixels else 0.0,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "samples": per_sample,
    }


def confusion_tuple(pred_black: np.ndarray, target_black: np.ndarray) -> tuple[int, int, int, int]:
    pred = pred_black.astype(bool, copy=False)
    target = target_black.astype(bool, copy=False)
    tp = int(np.count_nonzero(pred & target))
    tn = int(np.count_nonzero(~pred & ~target))
    fp = int(np.count_nonzero(pred & ~target))
    fn = int(np.count_nonzero(~pred & target))
    return tp, tn, fp, fn


def parse_float_grid(text: str, *, name: str) -> list[float]:
    values = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        values.append(float(piece))
    if not values:
        raise SystemExit(f"{name} grid is empty")
    return values


def parse_int_grid(text: str, *, name: str) -> list[int]:
    values = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        values.append(int(piece))
    if not values:
        raise SystemExit(f"{name} grid is empty")
    return values


def parse_text_grid(text: str, *, name: str) -> list[str]:
    values = [piece.strip() for piece in text.split(",") if piece.strip()]
    if not values:
        raise SystemExit(f"{name} grid is empty")
    return values


def compact_candidate_summary(candidate: CandidateSpec) -> dict:
    params = dict(candidate.params)
    thresholds = params.get("thresholds")
    if thresholds is not None:
        values = np.asarray(thresholds, dtype=np.float32)
        params["threshold_summary"] = {
            "shape": list(values.shape),
            "min": float(np.min(values)) if values.size else None,
            "max": float(np.max(values)) if values.size else None,
            "mean": float(np.mean(values)) if values.size else None,
            "sha256": hashlib.sha256(values.tobytes()).hexdigest(),
        }
        del params["thresholds"]
    return {"family": candidate.family, "name": candidate.name, "params": params}


def candidate_summary(candidate: CandidateSpec, *, include_thresholds: bool) -> dict:
    if include_thresholds:
        return {"family": candidate.family, "name": candidate.name, "params": candidate.params}
    return compact_candidate_summary(candidate)


def compact_result(result: dict, *, include_thresholds: bool) -> dict:
    payload = dict(result)
    candidate = payload.get("candidate")
    if candidate and not include_thresholds:
        payload["candidate"] = compact_candidate_summary(candidate_from_payload(candidate))
    return payload


def candidate_from_payload(payload: dict) -> CandidateSpec:
    return CandidateSpec(str(payload["family"]), str(payload.get("name", payload["family"])), dict(payload["params"]))


def load_candidate(path: Path, *, result_index: int = 0) -> CandidateSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "family" in payload and "params" in payload:
        return candidate_from_payload(payload)
    if "candidate" in payload:
        return candidate_from_payload(payload["candidate"])
    if "top_results" in payload:
        return candidate_from_payload(payload["top_results"][int(result_index)]["candidate"])
    raise SystemExit(f"Cannot find candidate payload in {path}")


def sample_to_dict(sample: SampleSpec) -> dict:
    return {
        "name": sample.name,
        "source_dir": str(sample.source_dir),
        "target_tiff": str(sample.target_tiff),
        "x0": sample.x0,
        "y0": sample.y0,
        "width": sample.width,
        "rows": sample.rows,
        "ppi": sample.ppi,
    }


def fixed_sample_specs(root: Path, *, rows: int, width: int, split: str) -> list[SampleSpec]:
    samples = [
        ("624_probe", root / "extracted", root / "raw" / "624_dats_dats.tif"),
        ("618_4000", root / "reference_618" / "source_views", root / "reference_618" / "618空间_dats_dats(1).tif"),
        ("618_8000", root / "reference_618" / "source_views", root / "reference_618" / "618空间高dpi_dats_dats.tif"),
    ]
    y_sets = {
        "train": [360, 1340, 2300, 3440],
        "holdout": [0, 920, 1940, 3050],
        "full": [0, 360, 920, 1340, 1940, 2300, 3050, 3440],
    }
    if split not in y_sets:
        raise SystemExit(f"Unknown split: {split}")
    specs: list[SampleSpec] = []
    for label, source_dir, target_tiff in samples:
        info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
        ppi = int(info.dpi_x or 4000)
        sample_width = min(width, info.width)
        x_positions = {
            "left": 0,
            "q1": max(0, int(round((info.width - sample_width) * 0.25))),
            "mid": max(0, (info.width - sample_width) // 2),
            "q3": max(0, int(round((info.width - sample_width) * 0.75))),
            "right": max(0, info.width - sample_width),
        }
        for source_y in y_sets[split]:
            y = int(round(source_y * info.height / 3651.0))
            y = max(0, min(info.height - rows, y))
            for band, x in x_positions.items():
                specs.append(SampleSpec(f"{label}_{split}_{band}_y{y}", source_dir, target_tiff, x, y, sample_width, min(rows, info.height - y), ppi))
    return specs


def probe_paths(root: Path) -> tuple[Path, Path]:
    return root / "extracted", root / "raw" / "624_dats_dats.tif"


def probe_region_sample_specs(
    root: Path,
    *,
    rows: int,
    width: int,
    regions: Sequence[str] | None,
    bands: Sequence[str],
    y_points_per_region: int,
) -> list[SampleSpec]:
    source_dir, target_tiff = probe_paths(root)
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    ppi = int(info.dpi_x or 4000)
    sample_width = min(width, info.width)
    band_positions = {
        "left": 0,
        "q1": max(0, int(round((info.width - sample_width) * 0.25))),
        "mid": max(0, (info.width - sample_width) // 2),
        "q3": max(0, int(round((info.width - sample_width) * 0.75))),
        "right": max(0, info.width - sample_width),
    }
    wanted = set(regions or [region.name for region in PROBE_REGIONS])
    specs: list[SampleSpec] = []
    for region in scaled_probe_regions(info.height):
        name = str(region["name"])
        if name not in wanted:
            continue
        y0 = int(region["y0"])
        y1 = int(region["y1"])
        if y1 <= y0:
            continue
        if y_points_per_region <= 1:
            y_values = [(y0 + y1) // 2]
        else:
            y_values = np.linspace(y0, max(y0, y1 - rows), y_points_per_region, dtype=int).tolist()
        for y in y_values:
            y = max(0, min(info.height - rows, int(y)))
            for band in bands:
                if band not in band_positions:
                    raise SystemExit(f"Unknown probe x band: {band}")
                x = band_positions[band]
                specs.append(
                    SampleSpec(
                        f"624_probe_{name}_{band}_y{y}",
                        source_dir,
                        target_tiff,
                        x,
                        y,
                        sample_width,
                        min(rows, info.height - y),
                        ppi,
                    )
                )
    if not specs:
        raise SystemExit("No probe sample specs were selected")
    return specs


def source_to_output_coordinate(source: float, *, source_size: int, output_size: int, coordinate_mode: str) -> float:
    if output_size <= 1 or source_size <= 1:
        return 0.0
    if coordinate_mode == "align_corners":
        return source * ((output_size - 1) / float(source_size - 1))
    if coordinate_mode == "half_pixel":
        return (source + 0.5) * (output_size / float(source_size)) - 0.5
    if coordinate_mode == "asymmetric":
        return source * (output_size / float(source_size))
    if coordinate_mode == "floor_nearest":
        return math.floor(source * (output_size / float(source_size)))
    raise SystemExit(f"Unknown coordinate mode: {coordinate_mode}")


def probe_tone_block_sample_specs(
    root: Path,
    *,
    rows: int,
    width: int,
    coordinate_mode: str,
    x_points_per_block: int,
    y_points_per_block: int,
    include_ramp_overlap_row: bool,
) -> list[SampleSpec]:
    """Return target-space windows centered inside known flat tone patches.

    The probe's fifth tone row is partly overwritten by the final continuous
    ramp. We only use its top flat strip when include_ramp_overlap_row is true.
    """
    source_dir, target_tiff = probe_paths(root)
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    source_width, source_height = source_dimensions(source_dir)
    ppi = int(info.dpi_x or 4000)
    sample_width = max(1, min(int(width), info.width))
    sample_rows = max(1, min(int(rows), info.height))
    cols = 12
    block_rows = 5
    source_y0, source_y1 = 2300, 3050
    cell_w = 2160 // cols
    cell_h = (source_y1 - source_y0) // block_rows
    ramp_y0 = source_y1 - 110
    x_fractions = np.linspace(0.25, 0.75, max(1, int(x_points_per_block)))
    y_fractions = np.linspace(0.25, 0.75, max(1, int(y_points_per_block)))
    specs: list[SampleSpec] = []

    for row in range(block_rows):
        for col in range(cols):
            tone = round((row * cols + col) * 255 / (cols * block_rows - 1))
            sx0 = col * cell_w
            sx1 = 2160 if col == cols - 1 else (col + 1) * cell_w
            sy0 = source_y0 + row * cell_h
            sy1 = source_y0 + (row + 1) * cell_h - 4
            if row == block_rows - 1:
                if not include_ramp_overlap_row:
                    continue
                sy1 = min(sy1, ramp_y0)
            sx0 += 12
            sx1 -= 12
            sy0 += 8
            sy1 -= 8
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            for xf in x_fractions:
                source_x = sx0 + (sx1 - sx0) * float(xf)
                final_x = int(round(source_to_output_coordinate(source_x, source_size=source_width, output_size=info.width, coordinate_mode=coordinate_mode)))
                x0 = max(0, min(info.width - sample_width, final_x - sample_width // 2))
                for yf in y_fractions:
                    source_y = sy0 + (sy1 - sy0) * float(yf)
                    final_y = int(round(source_to_output_coordinate(source_y, source_size=source_height, output_size=info.height, coordinate_mode=coordinate_mode)))
                    y0 = max(0, min(info.height - sample_rows, final_y - sample_rows // 2))
                    specs.append(
                        SampleSpec(
                            f"624_probe_tone_block_t{tone:03d}_r{row}_c{col}_x{float(xf):.2f}_y{float(yf):.2f}",
                            source_dir,
                            target_tiff,
                            x0,
                            y0,
                            sample_width,
                            sample_rows,
                            ppi,
                        )
                    )
    if not specs:
        raise SystemExit("No probe tone-block sample specs were selected")
    return specs


def sample_paths_for_dataset(root: Path, name: str) -> tuple[Path, Path]:
    key = name.lower().replace("-", "_")
    if key in {"624", "624_probe", "probe"}:
        return root / "extracted", root / "raw" / "624_dats_dats.tif"
    if key in {"618", "618_4000", "reference_618_4000"}:
        return root / "reference_618" / "source_views", root / "reference_618" / "618空间_dats_dats(1).tif"
    if key in {"618_8000", "reference_618_8000"}:
        return root / "reference_618" / "source_views", root / "reference_618" / "618空间高dpi_dats_dats.tif"
    raise SystemExit(f"Unknown dataset sample name: {name}")


def inspect_sources(source_dir: Path, *, hash_samples: int = 3) -> dict:
    paths = image_paths(source_dir)
    dimensions: dict[str, int] = {}
    modes: dict[str, int] = {}
    samples = []
    sample_indices = set(range(min(hash_samples, len(paths))))
    sample_indices.update(range(max(0, len(paths) - hash_samples), len(paths)))

    for index, path in enumerate(paths):
        with Image.open(path) as image:
            dimensions[f"{image.width}x{image.height}"] = dimensions.get(f"{image.width}x{image.height}", 0) + 1
            modes[image.mode] = modes.get(image.mode, 0) + 1
        if index in sample_indices:
            samples.append(
                {
                    "index": index,
                    "name": path.name,
                    "relative_path": str(path.relative_to(source_dir)),
                    "sha256": sha256(path),
                }
            )

    return {
        "directory": str(source_dir),
        "count": len(paths),
        "dimensions": dimensions,
        "modes": modes,
        "first": paths[0].name if paths else None,
        "last": paths[-1].name if paths else None,
        "samples": samples,
    }


def inspect_one_bit_tiff(path: Path, *, hash_file: bool = True) -> dict:
    info = delivery.read_uncompressed_one_bit_tiff_info(str(path))
    image_bytes = info.row_bytes * info.height
    payload_black = 0
    total_pixels = info.width * info.height
    sample_rows = []
    for y in sorted({0, 1, 2, info.height // 2, max(0, info.height - 3), max(0, info.height - 2), max(0, info.height - 1)}):
        row = read_tiff_row(info, y)
        sample_rows.append(
            {
                "y": y,
                "sha256": hashlib.sha256(row).hexdigest(),
                "black_count": delivery._count_black_in_packed_row(row, info.width),
            }
        )
    for row in delivery.iter_tiff_rows(info):
        payload_black += delivery._count_black_in_packed_row(row, info.width)

    result = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path) if hash_file else None,
        "width": info.width,
        "height": info.height,
        "bits_per_sample": list(info.bits_per_sample),
        "samples_per_pixel": info.samples_per_pixel,
        "compression": info.compression,
        "photometric_interpretation": info.photometric_interpretation,
        "dpi_x": info.dpi_x,
        "dpi_y": info.dpi_y,
        "is_bigtiff": info.is_bigtiff,
        "image_offset": info.image_offset,
        "row_bytes": info.row_bytes,
        "payload_bytes": image_bytes,
        "payload_black_ratio": payload_black / total_pixels if total_pixels else 0.0,
        "sample_rows": sample_rows,
    }
    if info.dpi_x:
        result["width_mm"] = info.width / info.dpi_x * delivery.MM_PER_INCH
    if info.dpi_y:
        result["height_mm"] = info.height / info.dpi_y * delivery.MM_PER_INCH
    return result


def read_tiff_row(info, y: int) -> bytes:
    with open(info.path, "rb") as handle:
        handle.seek(info.image_offset + y * info.row_bytes)
        return handle.read(info.row_bytes)


def scaled_probe_regions(height: int) -> list[dict]:
    scale = height / 3651.0
    regions = []
    for region in PROBE_REGIONS:
        y0 = max(0, min(height, int(round(region.y0 * scale))))
        y1 = max(y0, min(height, int(round(region.y1 * scale))))
        regions.append({"name": region.name, "y0": y0, "y1": y1})
    return regions


def region_for_y(regions: Sequence[dict], y: int) -> int:
    for index, region in enumerate(regions):
        if int(region["y0"]) <= y < int(region["y1"]):
            return index
    return max(0, len(regions) - 1)


def confusion_dict(tp: int, tn: int, fp: int, fn: int) -> dict:
    pixels = int(tp + tn + fp + fn)
    return {
        "tp": int(tp),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "pixels": pixels,
        "tp_pct": (tp / pixels * 100.0) if pixels else 0.0,
        "tn_pct": (tn / pixels * 100.0) if pixels else 0.0,
        "fp_pct": (fp / pixels * 100.0) if pixels else 0.0,
        "fn_pct": (fn / pixels * 100.0) if pixels else 0.0,
        "error_pct": ((fp + fn) / pixels * 100.0) if pixels else 0.0,
    }


def density_fft_peaks(values: np.ndarray, *, top: int = 8) -> list[dict]:
    values = np.asarray(values, dtype=np.float64)
    if values.size < 4:
        return []
    centered = values - float(np.mean(values))
    spectrum = np.abs(np.fft.rfft(centered))
    freqs = np.fft.rfftfreq(values.size)
    if spectrum.size:
        spectrum[0] = 0.0
    candidates = []
    for index in np.argsort(spectrum)[::-1][: max(top * 3, top)]:
        freq = float(freqs[index])
        if freq <= 0.0:
            continue
        candidates.append(
            {
                "bin": int(index),
                "frequency_cycles_per_sample": freq,
                "period_samples": 1.0 / freq,
                "magnitude": float(spectrum[index]),
            }
        )
        if len(candidates) >= top:
            break
    return candidates


def summarize_region_density(info, region: dict, *, fft_top: int = 8) -> dict:
    y0 = int(region["y0"])
    y1 = int(region["y1"])
    col_black = np.zeros(info.width, dtype=np.uint64)
    row_density = []
    black = 0
    pixels = 0
    with open(info.path, "rb") as handle:
        for y in range(y0, y1):
            handle.seek(info.image_offset + y * info.row_bytes)
            target = unpack_black_row(handle.read(info.row_bytes), info.width)
            count = int(np.count_nonzero(target))
            black += count
            pixels += info.width
            row_density.append(count / float(info.width))
            col_black += target.astype(np.uint64)
    rows = max(0, y1 - y0)
    col_density = col_black.astype(np.float64) / float(max(1, rows))
    row_density_array = np.asarray(row_density, dtype=np.float64)
    return {
        **region,
        "rows": rows,
        "pixels": pixels,
        "black_ratio": black / pixels if pixels else 0.0,
        "row_density": {
            "mean": float(np.mean(row_density_array)) if row_density_array.size else 0.0,
            "min": float(np.min(row_density_array)) if row_density_array.size else 0.0,
            "max": float(np.max(row_density_array)) if row_density_array.size else 0.0,
            "std": float(np.std(row_density_array)) if row_density_array.size else 0.0,
            "fft_peaks": density_fft_peaks(row_density_array, top=fft_top),
        },
        "column_density": {
            "mean": float(np.mean(col_density)) if col_density.size else 0.0,
            "min": float(np.min(col_density)) if col_density.size else 0.0,
            "max": float(np.max(col_density)) if col_density.size else 0.0,
            "std": float(np.std(col_density)) if col_density.size else 0.0,
            "fft_peaks": density_fft_peaks(col_density, top=fft_top),
        },
    }


def region_black_stats(target: Path, regions: Sequence[dict] | None = None) -> dict:
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target))
    if regions is None:
        regions = scaled_probe_regions(info.height)
    stats = []
    with open(info.path, "rb") as handle:
        for region in regions:
            black = 0
            pixels = 0
            y0 = int(region["y0"])
            y1 = int(region["y1"])
            for y in range(y0, y1):
                handle.seek(info.image_offset + y * info.row_bytes)
                row = handle.read(info.row_bytes)
                black += delivery._count_black_in_packed_row(row, info.width)
                pixels += info.width
            stats.append(
                {
                    **region,
                    "rows": y1 - y0,
                    "pixels": pixels,
                    "black_ratio": black / pixels if pixels else 0.0,
                }
            )
    return {
        "target": str(target),
        "width": info.width,
        "height": info.height,
        "regions": stats,
    }


def row_density_spectrum(target: Path, *, y0: int, rows: int, max_period: int = 256) -> dict:
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target))
    y1 = min(info.height, y0 + rows)
    densities = []
    with open(info.path, "rb") as handle:
        for y in range(y0, y1):
            handle.seek(info.image_offset + y * info.row_bytes)
            row = handle.read(info.row_bytes)
            densities.append(delivery._count_black_in_packed_row(row, info.width) / float(info.width))

    overall = sum(densities) / len(densities) if densities else 0.0
    scores = []
    for period in range(2, max_period + 1):
        buckets = [[] for _ in range(period)]
        for offset, value in enumerate(densities):
            buckets[offset % period].append(value)
        means = [sum(bucket) / len(bucket) if bucket else overall for bucket in buckets]
        variance = sum((value - overall) ** 2 for value in means) / period
        scores.append({"period": period, "score": variance, "min_mean": min(means), "max_mean": max(means)})
    scores.sort(key=lambda item: item["score"], reverse=True)
    return {
        "target": str(target),
        "y0": y0,
        "rows": len(densities),
        "overall_black_ratio": overall,
        "top_periods": scores[:20],
    }


def compare_payloads(left: Path, right: Path) -> dict:
    start = time.perf_counter()
    comparison = delivery.compare_one_bit_tiffs(str(left), str(right))
    comparison["left"] = str(left)
    comparison["right"] = str(right)
    comparison["elapsed_seconds"] = time.perf_counter() - start
    if comparison.get("same_shape"):
        total = comparison["total_pixels"]
        mismatch = comparison["mismatch_count"]
        comparison["match_ratio"] = 1.0 - (mismatch / total if total else 0.0)
    return comparison


def two_x_window_stats(
    info4,
    info8,
    *,
    x4: int,
    y4: int,
    width4: int,
    rows4: int,
    y_mode: str,
) -> dict:
    if y_mode == "double":
        y8 = 2 * y4
    elif y_mode == "align_corners":
        y8 = int(round(y4 * ((info8.height - 1) / float(max(1, info4.height - 1)))))
    elif y_mode == "asymmetric":
        y8 = int(math.floor((y4 + 0.5) * (info8.height / float(info4.height)) - 0.5))
    else:
        raise SystemExit(f"Unknown 2x y mode: {y_mode}")
    x8 = 2 * x4
    width4 = max(1, min(int(width4), info4.width - x4, (info8.width - x8) // 2))
    rows4 = max(1, min(int(rows4), info4.height - y4, (info8.height - y8) // 2))
    target4 = read_target_window(info4, x0=x4, y0=y4, width=width4, rows=rows4)
    target8 = read_target_window(info8, x0=x8, y0=y8, width=width4 * 2, rows=rows4 * 2)
    phase_stats = []
    best_phase = None
    for dy in [0, 1]:
        for dx in [0, 1]:
            sub = target8[dy::2, dx::2]
            tp, tn, fp, fn = confusion_tuple(sub, target4)
            pixels = tp + tn + fp + fn
            item = {
                "dx": dx,
                "dy": dy,
                "tp": tp,
                "tn": tn,
                "fp": fp,
                "fn": fn,
                "mismatch_ratio": (fp + fn) / pixels if pixels else 0.0,
                "black_ratio_4k": (tp + fn) / pixels if pixels else 0.0,
                "black_ratio_8k": (tp + fp) / pixels if pixels else 0.0,
            }
            phase_stats.append(item)
            if best_phase is None or item["mismatch_ratio"] < best_phase["mismatch_ratio"]:
                best_phase = item
    block_black = target8.reshape(rows4, 2, width4, 2).sum(axis=(1, 3))
    majority = block_black >= 2
    tp, tn, fp, fn = confusion_tuple(majority, target4)
    pixels = tp + tn + fp + fn
    return {
        "x4": x4,
        "y4": y4,
        "x8": x8,
        "y8": y8,
        "width4": width4,
        "rows4": rows4,
        "pixels4": pixels,
        "phase_stats": phase_stats,
        "best_phase": best_phase,
        "majority_2x2": {
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "mismatch_ratio": (fp + fn) / pixels if pixels else 0.0,
            "black_ratio_4k": (tp + fn) / pixels if pixels else 0.0,
            "black_ratio_8k_majority": (tp + fp) / pixels if pixels else 0.0,
            "black_fraction_8k_mean": float(np.mean(block_black) / 4.0) if pixels else 0.0,
        },
    }


def audit_two_x_consistency(
    *,
    target4: Path,
    target8: Path,
    width: int,
    rows: int,
    y_modes: Sequence[str],
) -> dict:
    info4 = delivery.read_uncompressed_one_bit_tiff_info(str(target4))
    info8 = delivery.read_uncompressed_one_bit_tiff_info(str(target8))
    width4 = min(int(width), info4.width, info8.width // 2)
    x_bands = {
        "left": 0,
        "mid": max(0, (info4.width - width4) // 2),
        "right": max(0, info4.width - width4),
    }
    source_y_points = [0, 360, 920, 1340, 1940, 2300, 3050, 3440]
    windows = []
    for y_mode in y_modes:
        for source_y in source_y_points:
            y4 = int(round(source_y * info4.height / 3651.0))
            y4 = max(0, min(info4.height - 1, y4))
            for band, x4 in x_bands.items():
                item = two_x_window_stats(
                    info4,
                    info8,
                    x4=x4,
                    y4=y4,
                    width4=width4,
                    rows4=rows,
                    y_mode=y_mode,
                )
                item["band"] = band
                item["source_y"] = source_y
                item["y_mode"] = y_mode
                windows.append(item)
    band_summary = []
    for y_mode in y_modes:
        for band in x_bands:
            group = [item for item in windows if item["band"] == band and item["y_mode"] == y_mode]
            pixels = sum(item["pixels4"] for item in group)
            tp = sum(item["best_phase"]["tp"] for item in group)
            tn = sum(item["best_phase"]["tn"] for item in group)
            fp = sum(item["best_phase"]["fp"] for item in group)
            fn = sum(item["best_phase"]["fn"] for item in group)
            band_summary.append({
                "y_mode": y_mode,
                "band": band,
                "pixels": pixels,
                "tp": tp,
                "tn": tn,
                "fp": fp,
                "fn": fn,
                "mismatch_ratio": (fp + fn) / pixels if pixels else 0.0,
            })
    best = min(band_summary, key=lambda item: item["mismatch_ratio"]) if band_summary else None
    return {
        "target_4k": str(target4),
        "target_8k": str(target8),
        "shape_4k": {"width": info4.width, "height": info4.height, "dpi_x": info4.dpi_x, "dpi_y": info4.dpi_y},
        "shape_8k": {"width": info8.width, "height": info8.height, "dpi_x": info8.dpi_x, "dpi_y": info8.dpi_y},
        "window": {"width4": width4, "rows4_requested": rows},
        "band_summary": sorted(band_summary, key=lambda item: (item["y_mode"], item["band"])),
        "best_band_summary": best,
        "windows": windows,
    }


def score_interlaced_profile(
    *,
    interlaced_tiff: Path,
    target_tiff: Path,
    method: str,
    ppi: int | None,
    row_start: int,
    row_count: int | None,
    progress_every: int,
) -> dict:
    rgb_info = delivery.read_uncompressed_rgb_tiff_info(str(interlaced_tiff))
    target_info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    if rgb_info.width != target_info.width or rgb_info.height != target_info.height:
        raise SystemExit(
            f"Shape mismatch: interlaced={rgb_info.width}x{rgb_info.height}, "
            f"target={target_info.width}x{target_info.height}"
        )
    output_ppi = int(ppi or rgb_info.dpi_x or target_info.dpi_x or 4000)
    profile = delivery.get_halftone_profile(method)
    halftoner = delivery.StreamingHalftoner(
        rgb_info.width,
        delivery.HalftoneSettings(method=method, gamma=profile.gamma, line_period_px=profile.period_px, line_density=profile.density),
        output_ppi,
        profile=profile,
    )
    y0 = max(0, int(row_start))
    y1 = target_info.height if row_count is None else min(target_info.height, y0 + max(0, int(row_count)))
    start = time.perf_counter()
    mismatch = generated_black = target_black = 0
    rows_done = 0
    with open(rgb_info.path, "rb") as rgb_handle, open(target_info.path, "rb") as target_handle:
        for y in range(y0, y1):
            rgb_handle.seek(rgb_info.image_offset + y * rgb_info.row_bytes)
            target_handle.seek(target_info.image_offset + y * target_info.row_bytes)
            black_row = halftoner.process_rgb_row(y, rgb_handle.read(rgb_info.row_bytes))
            target_row = target_handle.read(target_info.row_bytes)
            row_mismatch, row_black = compare_candidate_row(black_row, target_row, target_info.width)
            mismatch += row_mismatch
            generated_black += row_black
            target_black += delivery._count_black_in_packed_row(target_row, target_info.width)
            rows_done += 1
            if progress_every > 0 and rows_done % progress_every == 0:
                print(f"scored {rows_done}/{y1 - y0} rows", file=sys.stderr)
    pixels = rows_done * target_info.width
    return {
        "mode": "interlaced_tiff",
        "interlaced_tiff": str(interlaced_tiff),
        "target_tiff": str(target_tiff),
        "row_start": y0,
        "rows": rows_done,
        "width": target_info.width,
        "height": target_info.height,
        "ppi": output_ppi,
        "profile": profile_summary(profile, output_ppi),
        "mismatch_count": mismatch,
        "total_pixels": pixels,
        "mismatch_ratio": mismatch / pixels if pixels else 0.0,
        "match_ratio": 1.0 - (mismatch / pixels if pixels else 0.0),
        "black_ratio_generated": generated_black / pixels if pixels else 0.0,
        "black_ratio_target": target_black / pixels if pixels else 0.0,
        "elapsed_seconds": time.perf_counter() - start,
    }


def score_full_probe_candidate(
    *,
    dataset_root: Path,
    candidate: CandidateSpec,
    pe: float,
    angle: float,
    offset: float,
    reverse_views: bool,
    coordinate_mode: str,
    source_x_scale: float,
    source_y_scale: float,
    source_x_offset: float,
    source_y_offset: float,
    row_start: int,
    row_count: int | None,
    batch_rows: int,
    progress_every: int,
) -> dict:
    started = time.perf_counter()
    source_dir, target_tiff = probe_paths(dataset_root)
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    ppi = int(info.dpi_x or 4000)
    y0 = max(0, int(row_start))
    y1 = info.height if row_count is None else min(info.height, y0 + max(0, int(row_count)))
    regions = scaled_probe_regions(info.height)
    region_counts = [{"region": region, "tp": 0, "tn": 0, "fp": 0, "fn": 0} for region in regions]
    tp = tn = fp = fn = 0
    rows_done = 0
    batch_rows = max(1, int(batch_rows))

    sampler = SourceRowSampler(
        source_dir,
        output_width=info.width,
        output_height=info.height,
        ppi=ppi,
        pe=pe,
        angle_degrees=angle,
        offset=offset,
        reverse_views=reverse_views,
        coordinate_mode=coordinate_mode,
        source_x_scale=source_x_scale,
        source_y_scale=source_y_scale,
        source_x_offset=source_x_offset,
        source_y_offset=source_y_offset,
    )
    try:
        with open(info.path, "rb") as target_handle:
            y = y0
            while y < y1:
                rows = min(batch_rows, y1 - y)
                rgb = sampler.rgb_window(x0=0, y0=y, width=info.width, rows=rows)
                pred = candidate_black(candidate, rgb, x0=0, y0=y, ppi=ppi)
                target = np.empty((rows, info.width), dtype=bool)
                for row_index in range(rows):
                    target_handle.seek(info.image_offset + (y + row_index) * info.row_bytes)
                    target[row_index] = unpack_black_row(target_handle.read(info.row_bytes), info.width)
                local_tp, local_tn, local_fp, local_fn = confusion_tuple(pred, target)
                tp += local_tp
                tn += local_tn
                fp += local_fp
                fn += local_fn

                start_region = region_for_y(regions, y)
                end_region = region_for_y(regions, y + rows - 1)
                if start_region == end_region:
                    item = region_counts[start_region]
                    item["tp"] += local_tp
                    item["tn"] += local_tn
                    item["fp"] += local_fp
                    item["fn"] += local_fn
                else:
                    for region_index in range(start_region, end_region + 1):
                        region = regions[region_index]
                        sub_y0 = max(y, int(region["y0"]))
                        sub_y1 = min(y + rows, int(region["y1"]))
                        if sub_y1 <= sub_y0:
                            continue
                        sl = slice(sub_y0 - y, sub_y1 - y)
                        rtp, rtn, rfp, rfn = confusion_tuple(pred[sl], target[sl])
                        item = region_counts[region_index]
                        item["tp"] += rtp
                        item["tn"] += rtn
                        item["fp"] += rfp
                        item["fn"] += rfn

                y += rows
                rows_done += rows
                if progress_every > 0 and rows_done % progress_every < batch_rows:
                    pixels = tp + tn + fp + fn
                    error = (fp + fn) / pixels * 100.0 if pixels else 0.0
                    print(f"score-full-probe rows={rows_done}/{y1 - y0} error={error:.4f}%", file=sys.stderr)
    finally:
        sampler.close()

    per_region = []
    for item in region_counts:
        metrics = confusion_dict(item["tp"], item["tn"], item["fp"], item["fn"])
        per_region.append({**item["region"], **metrics})
    return {
        "mode": "score-full-probe",
        "dataset_root": str(dataset_root),
        "source_dir": str(source_dir),
        "target_tiff": str(target_tiff),
        "candidate": candidate_summary(candidate, include_thresholds=False),
        "interlace": {
            "pe": pe,
            "angle_degrees": angle,
            "offset": offset,
            "reverse_views": reverse_views,
            "coordinate_mode": coordinate_mode,
            "source_x_scale": source_x_scale,
            "source_y_scale": source_y_scale,
            "source_x_offset": source_x_offset,
            "source_y_offset": source_y_offset,
            "formula_changed": False,
        },
        "row_start": y0,
        "rows": rows_done,
        "width": info.width,
        "height": info.height,
        "ppi": ppi,
        "overall": confusion_dict(tp, tn, fp, fn),
        "regions": per_region,
        "constraints": {
            "stores_target_residual_table": False,
            "uses_per_pixel_answer_table": False,
            "probe_filename_special_case": False,
            "interlace_formula_modified": False,
        },
        "elapsed_seconds": time.perf_counter() - started,
    }


def cmd_score_full_probe(args: argparse.Namespace) -> None:
    candidate = load_candidate(args.candidate_json, result_index=args.result_index)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "score-full-probe",
        "result": score_full_probe_candidate(
            dataset_root=args.dataset_root,
            candidate=candidate,
            pe=args.pe,
            angle=args.angle,
            offset=args.offset,
            reverse_views=args.reverse_views,
            coordinate_mode=args.coordinate_mode,
            source_x_scale=args.source_x_scale,
            source_y_scale=args.source_y_scale,
            source_x_offset=args.source_x_offset,
            source_y_offset=args.source_y_offset,
            row_start=args.row_start,
            row_count=args.rows,
            batch_rows=args.batch_rows,
            progress_every=args.progress_every,
        ),
    }
    if args.include_thresholds:
        payload["result"]["candidate"] = candidate_summary(candidate, include_thresholds=True)
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def score_full_source_candidate(
    *,
    source_dir: Path,
    target_tiff: Path,
    candidate: CandidateSpec,
    pe: float,
    angle: float,
    offset: float,
    reverse_views: bool,
    coordinate_mode: str,
    source_x_scale: float,
    source_y_scale: float,
    source_x_offset: float,
    source_y_offset: float,
    row_start: int,
    row_count: int | None,
    batch_rows: int,
    progress_every: int,
    label: str,
) -> dict:
    started = time.perf_counter()
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    ppi = int(info.dpi_x or 4000)
    y0 = max(0, int(row_start))
    y1 = info.height if row_count is None else min(info.height, y0 + max(0, int(row_count)))
    tp = tn = fp = fn = 0
    rows_done = 0
    batch_rows = max(1, int(batch_rows))
    sampler = SourceRowSampler(
        source_dir,
        output_width=info.width,
        output_height=info.height,
        ppi=ppi,
        pe=pe,
        angle_degrees=angle,
        offset=offset,
        reverse_views=reverse_views,
        coordinate_mode=coordinate_mode,
        source_x_scale=source_x_scale,
        source_y_scale=source_y_scale,
        source_x_offset=source_x_offset,
        source_y_offset=source_y_offset,
    )
    try:
        with open(info.path, "rb") as target_handle:
            y = y0
            while y < y1:
                rows = min(batch_rows, y1 - y)
                rgb = sampler.rgb_window(x0=0, y0=y, width=info.width, rows=rows)
                pred = candidate_black(candidate, rgb, x0=0, y0=y, ppi=ppi)
                target = np.empty((rows, info.width), dtype=bool)
                for row_index in range(rows):
                    target_handle.seek(info.image_offset + (y + row_index) * info.row_bytes)
                    target[row_index] = unpack_black_row(target_handle.read(info.row_bytes), info.width)
                local_tp, local_tn, local_fp, local_fn = confusion_tuple(pred, target)
                tp += local_tp
                tn += local_tn
                fp += local_fp
                fn += local_fn
                y += rows
                rows_done += rows
                if progress_every > 0 and rows_done % progress_every < batch_rows:
                    pixels = tp + tn + fp + fn
                    error = (fp + fn) / pixels * 100.0 if pixels else 0.0
                    print(f"score-full-source {label} rows={rows_done}/{y1 - y0} error={error:.4f}%", file=sys.stderr)
    finally:
        sampler.close()
    return {
        "mode": "score-full-source",
        "label": label,
        "source_dir": str(source_dir),
        "target_tiff": str(target_tiff),
        "candidate": candidate_summary(candidate, include_thresholds=False),
        "interlace": {
            "pe": pe,
            "angle_degrees": angle,
            "offset": offset,
            "reverse_views": reverse_views,
            "coordinate_mode": coordinate_mode,
            "source_x_scale": source_x_scale,
            "source_y_scale": source_y_scale,
            "source_x_offset": source_x_offset,
            "source_y_offset": source_y_offset,
            "formula_changed": False,
        },
        "row_start": y0,
        "rows": rows_done,
        "width": info.width,
        "height": info.height,
        "ppi": ppi,
        "overall": confusion_dict(tp, tn, fp, fn),
        "constraints": {
            "stores_target_residual_table": False,
            "uses_per_pixel_answer_table": False,
            "sample_filename_special_case": False,
            "interlace_formula_modified": False,
        },
        "elapsed_seconds": time.perf_counter() - started,
    }


def cmd_score_full_source(args: argparse.Namespace) -> None:
    candidate = load_candidate(args.candidate_json, result_index=args.result_index)
    results = []
    for sample_name in (args.sample or ["624_probe"]):
        source_dir, target_tiff = sample_paths_for_dataset(args.dataset_root, sample_name)
        results.append(
            score_full_source_candidate(
                source_dir=source_dir,
                target_tiff=target_tiff,
                candidate=candidate,
                pe=args.pe,
                angle=args.angle,
                offset=args.offset,
                reverse_views=args.reverse_views,
                coordinate_mode=args.coordinate_mode,
                source_x_scale=args.source_x_scale,
                source_y_scale=args.source_y_scale,
                source_x_offset=args.source_x_offset,
                source_y_offset=args.source_y_offset,
                row_start=args.row_start,
                row_count=args.rows,
                batch_rows=args.batch_rows,
                progress_every=args.progress_every,
                label=sample_name,
            )
        )
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "score-full-source",
        "dataset_root": str(args.dataset_root),
        "candidate_json": str(args.candidate_json),
        "result_index": args.result_index,
        "results": results,
    }
    if args.include_thresholds:
        for result in payload["results"]:
            result["candidate"] = candidate_summary(candidate, include_thresholds=True)
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def score_source_profile(
    *,
    source_dir: Path,
    target_tiff: Path,
    method: str,
    pe: float,
    angle: float,
    offset: float,
    reverse_views: bool,
    row_start: int,
    row_count: int | None,
    progress_every: int,
) -> dict:
    paths = image_paths(source_dir)
    if not paths:
        raise SystemExit(f"No PNG/JPG source images found in {source_dir}")
    target_info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    ppi = int(target_info.dpi_x or 4000)
    source_width, source_height = source_dimensions(source_dir)
    settings = delivery.DeliverySettings(
        width_mm=target_info.width / float(ppi) * delivery.MM_PER_INCH,
        height_mm=target_info.height / float(ppi) * delivery.MM_PER_INCH,
        ppi=ppi,
        frame=1,
        camera_count=len(paths),
        source_width=source_width,
        source_height=source_height,
        interlace=delivery.InterlaceSettings(pe=pe, angle_degrees=angle, offset=offset, reverse_views=reverse_views),
        halftone=delivery.HalftoneSettings(method=method),
        write_interlaced_tiff=False,
        write_film_tiff=True,
        source_format=paths[0].suffix.lstrip(".").upper(),
    )
    profile = delivery.get_halftone_profile(method)
    original_reader = delivery.read_source_rgb
    delivery.read_source_rgb = read_source_rgb_pillow
    try:
        renderer = delivery.InterlaceRenderer([str(path) for path in paths], settings, target_info.width, target_info.height)
    finally:
        delivery.read_source_rgb = original_reader
    halftoner = delivery.StreamingHalftoner(
        target_info.width,
        delivery.HalftoneSettings(method=method, gamma=profile.gamma, line_period_px=profile.period_px, line_density=profile.density),
        ppi,
        profile=profile,
    )
    y0 = max(0, int(row_start))
    y1 = target_info.height if row_count is None else min(target_info.height, y0 + max(0, int(row_count)))
    start = time.perf_counter()
    mismatch = generated_black = target_black = 0
    rows_done = 0
    with open(target_info.path, "rb") as target_handle:
        for y in range(y0, y1):
            target_handle.seek(target_info.image_offset + y * target_info.row_bytes)
            row = renderer.generate_final_row(y)
            black_row = halftoner.process_rgb_row(y, row)
            target_row = target_handle.read(target_info.row_bytes)
            row_mismatch, row_black = compare_candidate_row(black_row, target_row, target_info.width)
            mismatch += row_mismatch
            generated_black += row_black
            target_black += delivery._count_black_in_packed_row(target_row, target_info.width)
            rows_done += 1
            if progress_every > 0 and rows_done % progress_every == 0:
                print(f"scored {rows_done}/{y1 - y0} rows", file=sys.stderr)
    pixels = rows_done * target_info.width
    return {
        "mode": "source_sequence",
        "source_dir": str(source_dir),
        "target_tiff": str(target_tiff),
        "source_count": len(paths),
        "source_width": source_width,
        "source_height": source_height,
        "interlace": asdict(settings.interlace),
        "row_start": y0,
        "rows": rows_done,
        "width": target_info.width,
        "height": target_info.height,
        "ppi": ppi,
        "profile": profile_summary(profile, ppi),
        "mismatch_count": mismatch,
        "total_pixels": pixels,
        "mismatch_ratio": mismatch / pixels if pixels else 0.0,
        "match_ratio": 1.0 - (mismatch / pixels if pixels else 0.0),
        "black_ratio_generated": generated_black / pixels if pixels else 0.0,
        "black_ratio_target": target_black / pixels if pixels else 0.0,
        "elapsed_seconds": time.perf_counter() - start,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_manifest(args: argparse.Namespace) -> None:
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sources": [inspect_sources(Path(path)) for path in args.source_dir],
        "targets": [inspect_one_bit_tiff(Path(path), hash_file=not args.no_hash) for path in args.target_tiff],
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_analyze_target(args: argparse.Namespace) -> None:
    payload = {
        "target": inspect_one_bit_tiff(args.target_tiff, hash_file=not args.no_hash),
        "probe_scaled_regions": region_black_stats(args.target_tiff),
        "row_periodicity": [
            row_density_spectrum(args.target_tiff, y0=y0, rows=args.rows, max_period=args.max_period)
            for y0 in args.y0
        ],
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_analyze_probe_regions(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    source_dir, target_tiff = probe_paths(args.dataset_root)
    info = delivery.read_uncompressed_one_bit_tiff_info(str(target_tiff))
    regions = scaled_probe_regions(info.height)
    density = [summarize_region_density(info, region, fft_top=args.fft_top) for region in regions]
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "analyze-probe-regions",
        "dataset_root": str(args.dataset_root),
        "source_dir": str(source_dir),
        "target_tiff": str(target_tiff),
        "shape": {
            "width": info.width,
            "height": info.height,
            "dpi_x": info.dpi_x,
            "dpi_y": info.dpi_y,
            "row_bytes": info.row_bytes,
        },
        "probe_regions": density,
        "interlace_evidence_lock": {
            "pe": args.pe,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "formula_changed": False,
            "note": "Geometry is reported for evidence only; this command does not modify interlace_view_index or the interlace formula.",
        },
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.candidate_json:
        candidate = load_candidate(args.candidate_json, result_index=args.result_index)
        score = score_full_probe_candidate(
            dataset_root=args.dataset_root,
            candidate=candidate,
            pe=args.pe,
            angle=args.angle,
            offset=args.offset,
            reverse_views=args.reverse_views,
            coordinate_mode=args.coordinate_mode,
            source_x_scale=args.source_x_scale,
            source_y_scale=args.source_y_scale,
            source_x_offset=args.source_x_offset,
            source_y_offset=args.source_y_offset,
            row_start=args.row_start,
            row_count=args.rows,
            batch_rows=args.batch_rows,
            progress_every=args.progress_every,
        )
        payload["candidate_score"] = score
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_compare(args: argparse.Namespace) -> None:
    payload = compare_payloads(args.left_tiff, args.right_tiff)
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_audit_input_consistency(args: argparse.Namespace) -> None:
    root = args.dataset_root
    y_modes = [piece.strip() for piece in args.y_modes.split(",") if piece.strip()]
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "audit-input-consistency",
        "dataset_root": str(root),
        "two_x_618": audit_two_x_consistency(
            target4=root / "reference_618" / "618空间_dats_dats(1).tif",
            target8=root / "reference_618" / "618空间高dpi_dats_dats.tif",
            width=args.width,
            rows=args.rows,
            y_modes=y_modes,
        ),
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_score_profile(args: argparse.Namespace) -> None:
    if args.interlaced_tiff:
        payload = score_interlaced_profile(
            interlaced_tiff=args.interlaced_tiff,
            target_tiff=args.target_tiff,
            method=args.method,
            ppi=args.ppi,
            row_start=args.row_start,
            row_count=args.rows,
            progress_every=args.progress_every,
        )
    else:
        if not args.source_dir:
            raise SystemExit("score-profile requires --interlaced-tiff or --source-dir")
        payload = score_source_profile(
            source_dir=args.source_dir,
            target_tiff=args.target_tiff,
            method=args.method,
            pe=args.pe,
            angle=args.angle,
            offset=args.offset,
            reverse_views=args.reverse_views,
            row_start=args.row_start,
            row_count=args.rows,
            progress_every=args.progress_every,
        )
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_score_candidate(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    candidate = load_candidate(args.candidate_json, result_index=args.result_index)
    samples = fixed_sample_specs(args.dataset_root, rows=args.rows, width=args.width, split=args.split)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    payloads = materialize_sample_payloads(
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
    )
    result = score_materialized_candidate(candidate, payloads)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "score-candidate",
        "dataset_root": str(args.dataset_root),
        "candidate_json": str(args.candidate_json),
        "result_index": args.result_index,
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
        },
        "sample_window": {
            "split": args.split,
            "rows": args.rows,
            "width": args.width,
            "sample_count": len(samples),
            "samples": [sample_to_dict(sample) for sample in samples],
        },
        "result": compact_result(result, include_thresholds=args.include_thresholds),
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_search_families(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    root = args.dataset_root
    candidates = candidate_grid()
    if args.family:
        wanted = set(args.family)
        candidates = [candidate for candidate in candidates if candidate.family in wanted]
    if args.limit:
        candidates = candidates[: args.limit]
    samples = default_sample_specs(root, rows=args.rows, width=args.width)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    results = score_candidates_on_samples(
        candidates,
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
        progress_every=args.progress_every,
    )
    family_best = {}
    for result in results:
        family = result["candidate"]["family"]
        family_best.setdefault(family, result)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "search-families",
        "dataset_root": str(root),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
        },
        "sample_window": {
            "rows": args.rows,
            "width": args.width,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "informative_only": args.informative_only,
            "min_black_ratio": args.min_black_ratio,
            "max_black_ratio": args.max_black_ratio,
        },
        "candidate_count": len(candidates),
        "family_best": list(family_best.values()),
        "top_results": results[: args.top],
        "elapsed_seconds": time.perf_counter() - started,
        "note": (
            "This is an adversarial small-window model-family screen. "
            "It does not store target residuals or per-coordinate answer tables; "
            "full payload bitwise validation must still be run for any candidate with zero sampled mismatch."
        ),
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def geometry_candidate() -> CandidateSpec:
    return CandidateSpec("row_threshold", "row18_phase0_g0.18", {
        "period_px": 18.0,
        "reference_ppi": 4000,
        "scale_with_ppi": True,
        "phase_y": 0,
        "gamma": 0.18,
        "density": 0.25,
        "bias": -0.05,
        "thresholds": list(delivery.LBY_LINE_THRESHOLDS),
    })


def cmd_search_geometry(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    root = args.dataset_root
    samples = default_sample_specs(root, rows=args.rows, width=args.width)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    offsets = []
    value = float(args.offset_start)
    while value <= float(args.offset_stop) + 1.0e-9:
        offsets.append(value)
        value += float(args.offset_step)
    reverses = [False, True] if args.try_reverse else [bool(args.reverse_views)]
    rows = []
    for reverse in reverses:
        for offset in offsets:
            result = score_candidates_on_samples(
                [geometry_candidate()],
                samples,
                pe=args.pe,
                angle=args.angle,
                offset=offset,
                reverse_views=reverse,
                coordinate_mode=args.coordinate_mode,
                source_x_scale=args.source_x_scale,
                source_y_scale=args.source_y_scale,
                source_x_offset=args.source_x_offset,
                source_y_offset=args.source_y_offset,
                progress_every=0,
            )[0]
            rows.append({
                "offset": offset,
                "reverse_views": reverse,
                **result,
            })
            print(
                f"geometry offset={offset:.4f} reverse={reverse} mismatch={result['mismatch_ratio']:.6f}",
                file=sys.stderr,
            )
    rows.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "search-geometry",
        "dataset_root": str(root),
        "pe": args.pe,
        "angle_degrees": args.angle,
        "offset_start": args.offset_start,
        "offset_stop": args.offset_stop,
        "offset_step": args.offset_step,
        "coordinate_mode": args.coordinate_mode,
        "source_x_scale": args.source_x_scale,
        "source_y_scale": args.source_y_scale,
        "source_x_offset": args.source_x_offset,
        "source_y_offset": args.source_y_offset,
        "sample_window": {
            "rows": args.rows,
            "width": args.width,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "informative_only": args.informative_only,
        },
        "top_results": rows[: args.top],
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_search_source_geometry(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    candidate = load_candidate(args.candidate_json, result_index=args.result_index) if args.candidate_json else geometry_candidate()
    samples = fixed_sample_specs(args.dataset_root, rows=args.rows, width=args.width, split=args.split)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    coordinate_modes = parse_text_grid(args.coordinate_mode_grid, name="coordinate-mode")
    x_scales = parse_float_grid(args.source_x_scale_grid, name="source-x-scale")
    y_scales = parse_float_grid(args.source_y_scale_grid, name="source-y-scale")
    x_offsets = parse_float_grid(args.source_x_offset_grid, name="source-x-offset")
    y_offsets = parse_float_grid(args.source_y_offset_grid, name="source-y-offset")
    rows = []
    total = len(coordinate_modes) * len(x_scales) * len(y_scales) * len(x_offsets) * len(y_offsets)
    searched = 0
    for coordinate_mode, x_scale, y_scale, x_offset, y_offset in itertools.product(
        coordinate_modes,
        x_scales,
        y_scales,
        x_offsets,
        y_offsets,
    ):
        result = score_candidates_on_samples(
            [candidate],
            samples,
            pe=args.pe,
            angle=args.angle,
            offset=args.offset,
            reverse_views=args.reverse_views,
            coordinate_mode=coordinate_mode,
            source_x_scale=x_scale,
            source_y_scale=y_scale,
            source_x_offset=x_offset,
            source_y_offset=y_offset,
            progress_every=0,
        )[0]
        result["source_geometry"] = {
            "coordinate_mode": coordinate_mode,
            "source_x_scale": x_scale,
            "source_y_scale": y_scale,
            "source_x_offset": x_offset,
            "source_y_offset": y_offset,
        }
        rows.append(result)
        searched += 1
        if args.progress_every > 0 and searched % args.progress_every == 0:
            best = min(rows, key=lambda item: item["mismatch_ratio"])
            print(
                f"source-geometry {searched}/{total} best={best['mismatch_ratio']:.8f} {best['source_geometry']}",
                file=sys.stderr,
            )
    rows.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "search-source-geometry",
        "dataset_root": str(args.dataset_root),
        "candidate": candidate_summary(candidate, include_thresholds=args.include_thresholds),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
        },
        "sample_window": {
            "split": args.split,
            "rows": args.rows,
            "width": args.width,
            "sample_count": len(samples),
            "informative_only": args.informative_only,
        },
        "grid": {
            "coordinate_mode": coordinate_modes,
            "source_x_scale": x_scales,
            "source_y_scale": y_scales,
            "source_x_offset": x_offsets,
            "source_y_offset": y_offsets,
        },
        "candidate_count": searched,
        "top_results": [compact_result(item, include_thresholds=args.include_thresholds) for item in rows[: args.top]],
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_fit_row_threshold(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    root = args.dataset_root
    samples = default_sample_specs(root, rows=args.rows, width=args.width)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    print(f"materializing {len(samples)} sampled windows", file=sys.stderr)
    payloads = materialize_sample_payloads(
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
    )

    gammas = parse_float_grid(args.gamma_grid, name="gamma")
    densities = parse_float_grid(args.density_grid, name="density")
    biases = parse_float_grid(args.bias_grid, name="bias")
    periods = parse_float_grid(args.period_grid, name="period")
    threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
    phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None

    combinations = list(itertools.product(gammas, densities, biases, periods, threshold_counts))
    phase_count = len(phase_values) if phase_values is not None else max(threshold_counts)
    total = len(combinations) * phase_count
    results = []
    searched = 0
    for gamma, density, bias, period_px, threshold_count in combinations:
        phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
        for phase_y in phases:
            candidate = fit_thresholds_for_payloads(
                payloads,
                gamma=gamma,
                density=density,
                bias=bias,
                period_px=period_px,
                phase_y=phase_y,
                threshold_count=threshold_count,
                reference_ppi=args.reference_ppi,
                phase_mode=args.phase_mode,
                scale_with_ppi=args.scale_with_ppi,
            )
            result = score_materialized_candidate(candidate, payloads)
            results.append(result)
            searched += 1
            if args.progress_every > 0 and searched % args.progress_every == 0:
                best = min(results, key=lambda item: item["mismatch_ratio"])
                print(
                    f"fit {searched}/{total} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}",
                    file=sys.stderr,
                )
    results.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "fit-row-threshold",
        "dataset_root": str(root),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
        },
        "sample_window": {
            "rows": args.rows,
            "width": args.width,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "informative_only": args.informative_only,
            "min_black_ratio": args.min_black_ratio,
            "max_black_ratio": args.max_black_ratio,
        },
        "grid": {
            "gamma": gammas,
            "density": densities,
            "bias": biases,
            "period_px": periods,
            "threshold_count": threshold_counts,
            "phase_y": phase_values if phase_values is not None else "range(threshold_count)",
            "phase_mode": args.phase_mode,
            "reference_ppi": args.reference_ppi,
            "scale_with_ppi": args.scale_with_ppi,
        },
        "candidate_count": searched,
        "top_results": results[: args.top],
        "elapsed_seconds": time.perf_counter() - started,
        "note": (
            "Thresholds are fitted globally from sampled windows only. "
            "This report stores fitted scalar parameters and aggregate mismatch statistics, "
            "not target residual maps or per-pixel answer tables."
        ),
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_size_grid(text: str) -> list[tuple[int, int]]:
    sizes = []
    for piece in text.split(","):
        piece = piece.strip().lower()
        if not piece:
            continue
        if "x" not in piece:
            raise SystemExit(f"Size grid item must look like WIDTHxHEIGHT: {piece}")
        width, height = piece.split("x", 1)
        sizes.append((int(width), int(height)))
    if not sizes:
        raise SystemExit("size grid is empty")
    return sizes


def cmd_fit_periodic_threshold(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    root = args.dataset_root
    samples = default_sample_specs(root, rows=args.rows, width=args.width)
    samples = filter_samples_by_name(samples, args.sample_name_contains)
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    print(f"materializing {len(samples)} sampled windows", file=sys.stderr)
    payloads = materialize_sample_payloads(
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
    )

    gammas = parse_float_grid(args.gamma_grid, name="gamma")
    densities = parse_float_grid(args.density_grid, name="density")
    biases = parse_float_grid(args.bias_grid, name="bias")
    sizes = parse_size_grid(args.size_grid)
    period_x_values = parse_float_grid(args.period_x_grid, name="period-x") if args.period_x_grid else None
    period_y_values = parse_float_grid(args.period_y_grid, name="period-y") if args.period_y_grid else None
    phase_x_values = parse_float_grid(args.phase_x_grid, name="phase-x")
    phase_y_values = parse_float_grid(args.phase_y_grid, name="phase-y")

    period_multiplier = (len(period_x_values) if period_x_values else 1) * (len(period_y_values) if period_y_values else 1)
    total = len(gammas) * len(densities) * len(biases) * len(sizes) * period_multiplier * len(phase_x_values) * len(phase_y_values)
    results = []
    searched = 0
    for gamma, density, bias, (width_cells, height_cells), phase_x, phase_y in itertools.product(
        gammas,
        densities,
        biases,
        sizes,
        phase_x_values,
        phase_y_values,
    ):
        candidate_period_x_values = period_x_values or [float(width_cells)]
        candidate_period_y_values = period_y_values or [float(height_cells)]
        for period_x, period_y in itertools.product(candidate_period_x_values, candidate_period_y_values):
            candidate = fit_periodic_thresholds_for_payloads(
                payloads,
                gamma=gamma,
                density=density,
                bias=bias,
                period_x_px=period_x,
                period_y_px=period_y,
                phase_x=phase_x,
                phase_y=phase_y,
                threshold_width=width_cells,
                threshold_height=height_cells,
                scale_x_with_ppi=args.scale_x_with_ppi,
                scale_y_with_ppi=args.scale_y_with_ppi,
                reference_ppi=args.reference_ppi,
            )
            result = score_materialized_candidate(candidate, payloads)
            results.append(result)
            searched += 1
            if args.progress_every > 0 and searched % args.progress_every == 0:
                best = min(results, key=lambda item: item["mismatch_ratio"])
                print(
                    f"fit-periodic {searched}/{total} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}",
                    file=sys.stderr,
                )
    results.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "fit-periodic-threshold",
        "dataset_root": str(root),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
        },
        "sample_window": {
            "rows": args.rows,
            "width": args.width,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "informative_only": args.informative_only,
            "min_black_ratio": args.min_black_ratio,
            "max_black_ratio": args.max_black_ratio,
        },
        "grid": {
            "gamma": gammas,
            "density": densities,
            "bias": biases,
            "size": [f"{w}x{h}" for w, h in sizes],
            "period_x_px": period_x_values if period_x_values is not None else "threshold_width",
            "period_y_px": period_y_values if period_y_values is not None else "threshold_height",
            "phase_x": phase_x_values,
            "phase_y": phase_y_values,
            "scale_x_with_ppi": args.scale_x_with_ppi,
            "scale_y_with_ppi": args.scale_y_with_ppi,
            "reference_ppi": args.reference_ppi,
        },
        "candidate_count": searched,
        "top_results": results[: args.top],
        "elapsed_seconds": time.perf_counter() - started,
        "note": (
            "This fits a global periodic threshold matrix, not per-image residuals. "
            "It is useful for checking whether LBY's remaining RIP behavior is a fixed 2D screen."
        ),
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_fit_probe_halftone(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    bands = parse_text_grid(args.bands, name="bands")
    regions = parse_text_grid(args.regions, name="regions") if args.regions else [
        "shared_halftone_tone_scale",
        "shared_coordinate_ramps",
        "shared_screen_angle_frequency_chart",
    ]
    samples = probe_region_sample_specs(
        args.dataset_root,
        rows=args.rows,
        width=args.width,
        regions=regions,
        bands=bands,
        y_points_per_region=args.y_points_per_region,
    )
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    print(f"materializing {len(samples)} probe sampled windows", file=sys.stderr)
    payloads = materialize_sample_payloads(
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
    )

    results = []
    searched = 0
    strategies = set(parse_text_grid(args.strategy, name="strategy"))

    if "families" in strategies:
        candidates = candidate_grid()
        if args.family:
            wanted = set(args.family)
            candidates = [candidate for candidate in candidates if candidate.family in wanted]
        for candidate in candidates:
            results.append(score_materialized_candidate(candidate, payloads))
            searched += 1

    if "row" in strategies:
        gammas = parse_float_grid(args.gamma_grid, name="gamma")
        densities = parse_float_grid(args.density_grid, name="density")
        biases = parse_float_grid(args.bias_grid, name="bias")
        periods = parse_float_grid(args.period_grid, name="period")
        threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
        phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None
        for gamma, density, bias, period_px, threshold_count in itertools.product(gammas, densities, biases, periods, threshold_counts):
            phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
            for phase_y in phases:
                candidate = fit_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_px=period_px,
                    phase_y=phase_y,
                    threshold_count=threshold_count,
                    reference_ppi=args.reference_ppi,
                    phase_mode=args.phase_mode,
                    scale_with_ppi=args.scale_with_ppi,
                )
                results.append(score_materialized_candidate(candidate, payloads))
                searched += 1
                if args.progress_every > 0 and searched % args.progress_every == 0:
                    best = min(results, key=lambda item: item["mismatch_ratio"])
                    print(f"fit-probe {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if "periodic" in strategies:
        gammas = parse_float_grid(args.gamma_grid, name="gamma")
        densities = parse_float_grid(args.density_grid, name="density")
        biases = parse_float_grid(args.bias_grid, name="bias")
        sizes = parse_size_grid(args.size_grid)
        period_x_values = parse_float_grid(args.period_x_grid, name="period-x") if args.period_x_grid else None
        period_y_values = parse_float_grid(args.period_y_grid, name="period-y") if args.period_y_grid else None
        phase_x_values = parse_float_grid(args.phase_x_grid, name="phase-x")
        phase_y_values = parse_float_grid(args.phase_y_grid, name="phase-y")
        for gamma, density, bias, (width_cells, height_cells), phase_x, phase_y in itertools.product(
            gammas,
            densities,
            biases,
            sizes,
            phase_x_values,
            phase_y_values,
        ):
            candidate_period_x_values = period_x_values or [float(width_cells)]
            candidate_period_y_values = period_y_values or [float(height_cells)]
            for period_x, period_y in itertools.product(candidate_period_x_values, candidate_period_y_values):
                candidate = fit_periodic_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_x_px=period_x,
                    period_y_px=period_y,
                    phase_x=phase_x,
                    phase_y=phase_y,
                    threshold_width=width_cells,
                    threshold_height=height_cells,
                    scale_x_with_ppi=args.scale_x_with_ppi,
                    scale_y_with_ppi=args.scale_y_with_ppi,
                    reference_ppi=args.reference_ppi,
                )
                results.append(score_materialized_candidate(candidate, payloads))
                searched += 1
                if args.progress_every > 0 and searched % args.progress_every == 0:
                    best = min(results, key=lambda item: item["mismatch_ratio"])
                    print(f"fit-probe {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if not results:
        raise SystemExit("No fit-probe-halftone strategies were selected")
    results.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "fit-probe-halftone",
        "dataset_root": str(args.dataset_root),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
            "formula_changed": False,
        },
        "sample_window": {
            "regions": regions,
            "bands": bands,
            "rows": args.rows,
            "width": args.width,
            "y_points_per_region": args.y_points_per_region,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "samples": [sample_to_dict(sample) for sample in samples],
            "informative_only": args.informative_only,
            "min_black_ratio": args.min_black_ratio,
            "max_black_ratio": args.max_black_ratio,
        },
        "strategy": sorted(strategies),
        "candidate_count": searched,
        "top_results": [compact_result(item, include_thresholds=args.include_thresholds) for item in results[: args.top]],
        "constraints": {
            "uses_probe_source_only": True,
            "stores_target_residual_table": False,
            "uses_per_pixel_answer_table": False,
            "probe_filename_special_case": False,
            "interlace_formula_modified": False,
        },
        "note": (
            "Candidates are fitted from sampled 624 probe windows and must be validated with score-full-probe. "
            "The fitted threshold matrices are periodic/global model parameters, not coordinate residual tables."
        ),
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_fit_probe_tone_block_screen(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    samples = probe_tone_block_sample_specs(
        args.dataset_root,
        rows=args.rows,
        width=args.width,
        coordinate_mode=args.coordinate_mode,
        x_points_per_block=args.x_points_per_block,
        y_points_per_block=args.y_points_per_block,
        include_ramp_overlap_row=args.include_ramp_overlap_row,
    )
    raw_sample_count = len(samples)
    if args.informative_only:
        samples = filter_informative_samples(
            samples,
            min_black_ratio=args.min_black_ratio,
            max_black_ratio=args.max_black_ratio,
            max_per_target=args.max_per_target,
        )
    print(f"materializing {len(samples)} flat tone-block windows", file=sys.stderr)
    payloads = materialize_sample_payloads(
        samples,
        pe=args.pe,
        angle=args.angle,
        offset=args.offset,
        reverse_views=args.reverse_views,
        coordinate_mode=args.coordinate_mode,
        source_x_scale=args.source_x_scale,
        source_y_scale=args.source_y_scale,
        source_x_offset=args.source_x_offset,
        source_y_offset=args.source_y_offset,
    )

    results = []
    searched = 0
    strategies = set(parse_text_grid(args.strategy, name="strategy"))
    gammas = parse_float_grid(args.gamma_grid, name="gamma")
    densities = parse_float_grid(args.density_grid, name="density")
    biases = parse_float_grid(args.bias_grid, name="bias")

    if "row" in strategies:
        periods = parse_float_grid(args.period_grid, name="period")
        threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
        phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None
        for gamma, density, bias, period_px, threshold_count in itertools.product(gammas, densities, biases, periods, threshold_counts):
            phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
            for phase_y in phases:
                candidate = fit_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_px=period_px,
                    phase_y=phase_y,
                    threshold_count=threshold_count,
                    reference_ppi=args.reference_ppi,
                    phase_mode=args.phase_mode,
                    scale_with_ppi=args.scale_with_ppi,
                )
                result = score_materialized_candidate(candidate, payloads)
                results.append(result)
                searched += 1
                if args.progress_every > 0 and searched % args.progress_every == 0:
                    best = min(results, key=lambda item: item["mismatch_ratio"])
                    print(f"fit-tone-block {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if "transition" in strategies:
        periods = parse_float_grid(args.period_grid, name="period")
        threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
        phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None
        transition_widths = parse_float_grid(args.transition_width_grid, name="transition-width")
        dither_kinds = parse_text_grid(args.transition_dither_grid, name="transition-dither")
        matrix_sizes = parse_int_grid(args.transition_matrix_size_grid, name="transition-matrix-size")
        seeds = parse_int_grid(args.transition_seed_grid, name="transition-seed")
        for gamma, density, bias, period_px, threshold_count in itertools.product(gammas, densities, biases, periods, threshold_counts):
            phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
            for phase_y in phases:
                base = fit_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_px=period_px,
                    phase_y=phase_y,
                    threshold_count=threshold_count,
                    reference_ppi=args.reference_ppi,
                    phase_mode=args.phase_mode,
                    scale_with_ppi=args.scale_with_ppi,
                )
                for transition_width in transition_widths:
                    for dither_kind in dither_kinds:
                        if dither_kind == "hash":
                            iterator = (("hash", 0, seed) for seed in seeds)
                        else:
                            iterator = ((dither_kind, size, 0) for size in matrix_sizes)
                        for kind, matrix_size, seed in iterator:
                            params = dict(base.params)
                            params.update(
                                {
                                    "transition_width": float(transition_width),
                                    "dither": kind,
                                    "matrix_size": int(matrix_size),
                                    "seed": int(seed),
                                }
                            )
                            candidate = CandidateSpec(
                                "row_transition_dither",
                                f"transition_{kind}_w{transition_width}_m{matrix_size}_s{seed}_{base.name}",
                                params,
                            )
                            result = score_materialized_candidate(candidate, payloads)
                            results.append(result)
                            searched += 1
                            if args.progress_every > 0 and searched % args.progress_every == 0:
                                best = min(results, key=lambda item: item["mismatch_ratio"])
                                print(f"fit-tone-block {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if "diffusion" in strategies:
        periods = parse_float_grid(args.period_grid, name="period")
        threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
        phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None
        kernels = parse_text_grid(args.diffusion_kernel_grid, name="diffusion-kernel")
        strengths = parse_float_grid(args.diffusion_strength_grid, name="diffusion-strength")
        threshold_biases = parse_float_grid(args.diffusion_threshold_bias_grid, name="diffusion-threshold-bias")
        serpentines = [value.lower() in {"1", "true", "yes", "serpentine"} for value in parse_text_grid(args.diffusion_serpentine_grid, name="diffusion-serpentine")]
        for gamma, density, bias, period_px, threshold_count in itertools.product(gammas, densities, biases, periods, threshold_counts):
            phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
            for phase_y in phases:
                base = fit_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_px=period_px,
                    phase_y=phase_y,
                    threshold_count=threshold_count,
                    reference_ppi=args.reference_ppi,
                    phase_mode=args.phase_mode,
                    scale_with_ppi=args.scale_with_ppi,
                )
                for kernel, strength, threshold_bias, serpentine in itertools.product(kernels, strengths, threshold_biases, serpentines):
                    params = dict(base.params)
                    params.update(
                        {
                            "kernel": kernel,
                            "strength": float(strength),
                            "threshold_bias": float(threshold_bias),
                            "serpentine": bool(serpentine),
                        }
                    )
                    candidate = CandidateSpec(
                        "row_screen_diffusion",
                        f"diffusion_{kernel}_s{strength}_tb{threshold_bias}_{'serp' if serpentine else 'scan'}_{base.name}",
                        params,
                    )
                    result = score_materialized_candidate(candidate, payloads)
                    results.append(result)
                    searched += 1
                    if args.progress_every > 0 and searched % args.progress_every == 0:
                        best = min(results, key=lambda item: item["mismatch_ratio"])
                        print(f"fit-tone-block {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if "microspot" in strategies:
        periods = parse_float_grid(args.period_grid, name="period")
        threshold_counts = parse_int_grid(args.threshold_count_grid, name="threshold-count")
        phase_values = parse_float_grid(args.phase_grid, name="phase-y") if args.phase_grid else None
        transition_widths = parse_float_grid(args.microspot_transition_width_grid, name="microspot-transition-width")
        cell_x_values = parse_float_grid(args.microspot_cell_x_grid, name="microspot-cell-x")
        cell_y_values = parse_float_grid(args.microspot_cell_y_grid, name="microspot-cell-y")
        shapes = parse_text_grid(args.microspot_shape_grid, name="microspot-shape")
        phase_x_values = parse_float_grid(args.microspot_phase_x_grid, name="microspot-phase-x")
        phase_y_values = parse_float_grid(args.microspot_phase_y_grid, name="microspot-phase-y")
        for gamma, density, bias, period_px, threshold_count in itertools.product(gammas, densities, biases, periods, threshold_counts):
            phases = phase_values if phase_values is not None else [float(value) for value in range(threshold_count)]
            for phase_y in phases:
                base = fit_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_px=period_px,
                    phase_y=phase_y,
                    threshold_count=threshold_count,
                    reference_ppi=args.reference_ppi,
                    phase_mode=args.phase_mode,
                    scale_with_ppi=args.scale_with_ppi,
                )
                for transition_width, cell_x, cell_y, shape, phase_x, phase_y_spot in itertools.product(
                    transition_widths,
                    cell_x_values,
                    cell_y_values,
                    shapes,
                    phase_x_values,
                    phase_y_values,
                ):
                    params = dict(base.params)
                    params.update(
                        {
                            "transition_width": float(transition_width),
                            "cell_x": float(cell_x),
                            "cell_y": float(cell_y),
                            "shape": shape,
                            "phase_x": float(phase_x),
                            "phase_y_spot": float(phase_y_spot),
                        }
                    )
                    candidate = CandidateSpec(
                        "row_micro_spot",
                        f"microspot_{shape}_w{transition_width}_cx{cell_x}_cy{cell_y}_px{phase_x}_py{phase_y_spot}_{base.name}",
                        params,
                    )
                    result = score_materialized_candidate(candidate, payloads)
                    results.append(result)
                    searched += 1
                    if args.progress_every > 0 and searched % args.progress_every == 0:
                        best = min(results, key=lambda item: item["mismatch_ratio"])
                        print(f"fit-tone-block {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if "periodic" in strategies:
        sizes = parse_size_grid(args.size_grid)
        period_x_values = parse_float_grid(args.period_x_grid, name="period-x") if args.period_x_grid else None
        period_y_values = parse_float_grid(args.period_y_grid, name="period-y") if args.period_y_grid else None
        phase_x_values = parse_float_grid(args.phase_x_grid, name="phase-x")
        phase_y_values = parse_float_grid(args.phase_y_grid, name="phase-y")
        for gamma, density, bias, (width_cells, height_cells), phase_x, phase_y in itertools.product(
            gammas,
            densities,
            biases,
            sizes,
            phase_x_values,
            phase_y_values,
        ):
            candidate_period_x_values = period_x_values or [float(width_cells)]
            candidate_period_y_values = period_y_values or [float(height_cells)]
            for period_x, period_y in itertools.product(candidate_period_x_values, candidate_period_y_values):
                candidate = fit_periodic_thresholds_for_payloads(
                    payloads,
                    gamma=gamma,
                    density=density,
                    bias=bias,
                    period_x_px=period_x,
                    period_y_px=period_y,
                    phase_x=phase_x,
                    phase_y=phase_y,
                    threshold_width=width_cells,
                    threshold_height=height_cells,
                    scale_x_with_ppi=args.scale_x_with_ppi,
                    scale_y_with_ppi=args.scale_y_with_ppi,
                    reference_ppi=args.reference_ppi,
                )
                result = score_materialized_candidate(candidate, payloads)
                results.append(result)
                searched += 1
                if args.progress_every > 0 and searched % args.progress_every == 0:
                    best = min(results, key=lambda item: item["mismatch_ratio"])
                    print(f"fit-tone-block {searched} best={best['mismatch_ratio']:.8f} {best['candidate']['name']}", file=sys.stderr)

    if not results:
        raise SystemExit("No fit-probe-tone-block-screen strategies were selected")
    results.sort(key=lambda item: item["mismatch_ratio"])
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": "fit-probe-tone-block-screen",
        "dataset_root": str(args.dataset_root),
        "interlace": {
            "pe": args.pe,
            "angle_degrees": args.angle,
            "offset": args.offset,
            "reverse_views": args.reverse_views,
            "coordinate_mode": args.coordinate_mode,
            "source_x_scale": args.source_x_scale,
            "source_y_scale": args.source_y_scale,
            "source_x_offset": args.source_x_offset,
            "source_y_offset": args.source_y_offset,
            "formula_changed": False,
        },
        "sample_window": {
            "kind": "probe_flat_tone_blocks",
            "rows": args.rows,
            "width": args.width,
            "x_points_per_block": args.x_points_per_block,
            "y_points_per_block": args.y_points_per_block,
            "include_ramp_overlap_row": args.include_ramp_overlap_row,
            "raw_sample_count": raw_sample_count,
            "sample_count": len(samples),
            "samples": [sample_to_dict(sample) for sample in samples],
            "informative_only": args.informative_only,
        },
        "strategy": sorted(strategies),
        "candidate_count": searched,
        "top_results": [compact_result(item, include_thresholds=args.include_thresholds) for item in results[: args.top]],
        "constraints": {
            "uses_probe_design_tone_blocks": True,
            "stores_target_residual_table": False,
            "uses_per_pixel_answer_table": False,
            "probe_filename_special_case": False,
            "interlace_formula_modified": False,
        },
        "note": (
            "This fitter uses known flat tone patches from the probe design. "
            "The learned parameters are global row/periodic screen thresholds, not coordinate residual repairs."
        ),
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.output:
        write_json(args.output, payload)
    if not args.json_only or not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def add_source_geometry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-x-scale", type=float, default=1.0)
    parser.add_argument("--source-y-scale", type=float, default=1.0)
    parser.add_argument("--source-x-offset", type=float, default=0.0)
    parser.add_argument("--source-y-offset", type=float, default=0.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LBY probe/reference reverse-engineering helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="Inspect source sequences and target 1-bit TIFFs.")
    manifest.add_argument("--source-dir", type=Path, action="append", default=[], help="Source image directory.")
    manifest.add_argument("--target-tiff", type=Path, action="append", default=[], help="Target 1-bit TIFF.")
    manifest.add_argument("--output", type=Path)
    manifest.add_argument("--no-hash", action="store_true", help="Skip full-file target hashes.")
    manifest.set_defaults(func=cmd_manifest)

    analyze = subparsers.add_parser("analyze-target", help="Summarize target density and row periodicity.")
    analyze.add_argument("--target-tiff", type=Path, required=True)
    analyze.add_argument("--y0", type=int, action="append", default=[0], help="Start row for periodicity scan.")
    analyze.add_argument("--rows", type=int, default=4096)
    analyze.add_argument("--max-period", type=int, default=256)
    analyze.add_argument("--output", type=Path)
    analyze.add_argument("--no-hash", action="store_true", help="Skip full-file target hash.")
    analyze.set_defaults(func=cmd_analyze_target)

    probe_analyze = subparsers.add_parser("analyze-probe-regions", help="Analyze the 624 probe target by the 8 known probe regions.")
    probe_analyze.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    probe_analyze.add_argument("--candidate-json", type=Path, help="Optional candidate to score while analyzing regions.")
    probe_analyze.add_argument("--result-index", type=int, default=0)
    probe_analyze.add_argument("--pe", type=float, default=52.64)
    probe_analyze.add_argument("--angle", type=float, default=0.0)
    probe_analyze.add_argument("--offset", type=float, default=0.0)
    probe_analyze.add_argument("--reverse-views", action="store_true")
    probe_analyze.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(probe_analyze)
    probe_analyze.add_argument("--row-start", type=int, default=0)
    probe_analyze.add_argument("--rows", type=int, help="Optional number of rows to score when --candidate-json is set.")
    probe_analyze.add_argument("--batch-rows", type=int, default=4)
    probe_analyze.add_argument("--progress-every", type=int, default=512)
    probe_analyze.add_argument("--fft-top", type=int, default=8)
    probe_analyze.add_argument("--json-only", action="store_true")
    probe_analyze.add_argument("--output", type=Path)
    probe_analyze.set_defaults(func=cmd_analyze_probe_regions)

    compare = subparsers.add_parser("compare", help="Compare two uncompressed 1-bit TIFF payloads.")
    compare.add_argument("--left-tiff", type=Path, required=True)
    compare.add_argument("--right-tiff", type=Path, required=True)
    compare.add_argument("--output", type=Path)
    compare.set_defaults(func=cmd_compare)

    audit = subparsers.add_parser("audit-input-consistency", help="Audit 618 4k/8k target payload consistency under 2x mappings.")
    audit.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    audit.add_argument("--width", type=int, default=1024)
    audit.add_argument("--rows", type=int, default=256)
    audit.add_argument("--y-modes", default="double,align_corners,asymmetric")
    audit.add_argument("--output", type=Path)
    audit.set_defaults(func=cmd_audit_input_consistency)

    score = subparsers.add_parser("score-profile", help="Stream-score an LBY profile against a target TIFF without writing full output.")
    score.add_argument("--target-tiff", type=Path, required=True)
    score.add_argument("--interlaced-tiff", type=Path, help="Existing RGB interlaced.tif to halftone and score.")
    score.add_argument("--source-dir", type=Path, help="PNG/JPG source sequence directory to interlace, halftone, and score.")
    score.add_argument("--method", default="LBY_V2", choices=sorted(delivery.HALFTONE_PROFILES.keys()))
    score.add_argument("--ppi", type=int, help="Override output PPI for interlaced TIFF scoring.")
    score.add_argument("--pe", type=float, default=52.64)
    score.add_argument("--angle", type=float, default=0.0)
    score.add_argument("--offset", type=float, default=0.0)
    score.add_argument("--reverse-views", action="store_true")
    score.add_argument("--row-start", type=int, default=0)
    score.add_argument("--rows", type=int, help="Optional number of rows to score.")
    score.add_argument("--progress-every", type=int, default=512)
    score.add_argument("--output", type=Path)
    score.set_defaults(func=cmd_score_profile)

    score_candidate = subparsers.add_parser("score-candidate", help="Score a saved candidate on fixed train/holdout/full windows.")
    score_candidate.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    score_candidate.add_argument("--candidate-json", type=Path, required=True)
    score_candidate.add_argument("--result-index", type=int, default=0)
    score_candidate.add_argument("--pe", type=float, default=52.64)
    score_candidate.add_argument("--angle", type=float, default=0.0)
    score_candidate.add_argument("--offset", type=float, default=0.0)
    score_candidate.add_argument("--reverse-views", action="store_true")
    score_candidate.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(score_candidate)
    score_candidate.add_argument("--split", default="holdout", choices=["train", "holdout", "full"])
    score_candidate.add_argument("--sample-name-contains", action="append")
    score_candidate.add_argument("--rows", type=int, default=64)
    score_candidate.add_argument("--width", type=int, default=1024)
    score_candidate.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=False)
    score_candidate.add_argument("--min-black-ratio", type=float, default=0.02)
    score_candidate.add_argument("--max-black-ratio", type=float, default=0.98)
    score_candidate.add_argument("--max-per-target", type=int, default=24)
    score_candidate.add_argument("--include-thresholds", action="store_true")
    score_candidate.add_argument("--json-only", action="store_true")
    score_candidate.add_argument("--output", type=Path)
    score_candidate.set_defaults(func=cmd_score_candidate)

    score_full_probe = subparsers.add_parser("score-full-probe", help="Stream-score a saved candidate against the full 624 probe payload.")
    score_full_probe.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    score_full_probe.add_argument("--candidate-json", type=Path, required=True)
    score_full_probe.add_argument("--result-index", type=int, default=0)
    score_full_probe.add_argument("--pe", type=float, default=52.64)
    score_full_probe.add_argument("--angle", type=float, default=0.0)
    score_full_probe.add_argument("--offset", type=float, default=0.0)
    score_full_probe.add_argument("--reverse-views", action="store_true")
    score_full_probe.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(score_full_probe)
    score_full_probe.add_argument("--row-start", type=int, default=0)
    score_full_probe.add_argument("--rows", type=int, help="Optional number of rows to score; omit for full payload.")
    score_full_probe.add_argument("--batch-rows", type=int, default=4)
    score_full_probe.add_argument("--progress-every", type=int, default=512)
    score_full_probe.add_argument("--include-thresholds", action="store_true")
    score_full_probe.add_argument("--json-only", action="store_true")
    score_full_probe.add_argument("--output", type=Path)
    score_full_probe.set_defaults(func=cmd_score_full_probe)

    score_full_source = subparsers.add_parser("score-full-source", help="Stream-score a saved candidate against 624/618 full source targets.")
    score_full_source.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    score_full_source.add_argument("--sample", action="append", help="Sample name: 624_probe, 618_4000, 618_8000. Defaults to 624_probe.")
    score_full_source.add_argument("--candidate-json", type=Path, required=True)
    score_full_source.add_argument("--result-index", type=int, default=0)
    score_full_source.add_argument("--pe", type=float, default=52.64)
    score_full_source.add_argument("--angle", type=float, default=0.0)
    score_full_source.add_argument("--offset", type=float, default=0.0)
    score_full_source.add_argument("--reverse-views", action="store_true")
    score_full_source.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(score_full_source)
    score_full_source.add_argument("--row-start", type=int, default=0)
    score_full_source.add_argument("--rows", type=int, help="Optional number of rows to score; omit for full payload.")
    score_full_source.add_argument("--batch-rows", type=int, default=4)
    score_full_source.add_argument("--progress-every", type=int, default=512)
    score_full_source.add_argument("--include-thresholds", action="store_true")
    score_full_source.add_argument("--json-only", action="store_true")
    score_full_source.add_argument("--output", type=Path)
    score_full_source.set_defaults(func=cmd_score_full_source)

    search = subparsers.add_parser("search-families", help="Run the 10-model-family adversarial small-window screen.")
    search.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    search.add_argument("--pe", type=float, default=52.64)
    search.add_argument("--angle", type=float, default=0.0)
    search.add_argument("--offset", type=float, default=0.0)
    search.add_argument("--reverse-views", action="store_true")
    search.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(search)
    search.add_argument("--rows", type=int, default=24, help="Rows per sampled window.")
    search.add_argument("--width", type=int, default=768, help="Pixels per sampled window.")
    search.add_argument("--sample-name-contains", action="append")
    search.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    search.add_argument("--min-black-ratio", type=float, default=0.02)
    search.add_argument("--max-black-ratio", type=float, default=0.98)
    search.add_argument("--max-per-target", type=int, default=18)
    search.add_argument("--family", action="append", help="Restrict to one or more candidate families.")
    search.add_argument("--limit", type=int, help="Evaluate only the first N candidates for smoke tests.")
    search.add_argument("--top", type=int, default=20)
    search.add_argument("--progress-every", type=int, default=25)
    search.add_argument("--output", type=Path)
    search.set_defaults(func=cmd_search_families)

    geom = subparsers.add_parser("search-geometry", help="Coarse global offset/reverse search without changing the interlace formula.")
    geom.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    geom.add_argument("--pe", type=float, default=52.64)
    geom.add_argument("--angle", type=float, default=0.0)
    geom.add_argument("--offset-start", type=float, default=0.0)
    geom.add_argument("--offset-stop", type=float, default=76.0)
    geom.add_argument("--offset-step", type=float, default=4.0)
    geom.add_argument("--reverse-views", action="store_true")
    geom.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(geom)
    geom.add_argument("--try-reverse", action=argparse.BooleanOptionalAction, default=True)
    geom.add_argument("--rows", type=int, default=8)
    geom.add_argument("--width", type=int, default=384)
    geom.add_argument("--sample-name-contains", action="append")
    geom.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    geom.add_argument("--min-black-ratio", type=float, default=0.02)
    geom.add_argument("--max-black-ratio", type=float, default=0.98)
    geom.add_argument("--max-per-target", type=int, default=12)
    geom.add_argument("--top", type=int, default=20)
    geom.add_argument("--output", type=Path)
    geom.set_defaults(func=cmd_search_geometry)

    source_geom = subparsers.add_parser("search-source-geometry", help="Search global source sampling scale/offset without changing interlace formula.")
    source_geom.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    source_geom.add_argument("--candidate-json", type=Path)
    source_geom.add_argument("--result-index", type=int, default=0)
    source_geom.add_argument("--pe", type=float, default=52.64)
    source_geom.add_argument("--angle", type=float, default=0.0)
    source_geom.add_argument("--offset", type=float, default=0.0)
    source_geom.add_argument("--reverse-views", action="store_true")
    source_geom.add_argument("--coordinate-mode-grid", default="align_corners")
    source_geom.add_argument("--source-x-scale-grid", default="1.0")
    source_geom.add_argument("--source-y-scale-grid", default="1.0")
    source_geom.add_argument("--source-x-offset-grid", default="0.0")
    source_geom.add_argument("--source-y-offset-grid", default="0.0")
    source_geom.add_argument("--split", default="holdout", choices=["train", "holdout", "full"])
    source_geom.add_argument("--sample-name-contains", action="append")
    source_geom.add_argument("--rows", type=int, default=32)
    source_geom.add_argument("--width", type=int, default=768)
    source_geom.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    source_geom.add_argument("--min-black-ratio", type=float, default=0.02)
    source_geom.add_argument("--max-black-ratio", type=float, default=0.98)
    source_geom.add_argument("--max-per-target", type=int, default=24)
    source_geom.add_argument("--top", type=int, default=20)
    source_geom.add_argument("--progress-every", type=int, default=10)
    source_geom.add_argument("--include-thresholds", action="store_true")
    source_geom.add_argument("--json-only", action="store_true")
    source_geom.add_argument("--output", type=Path)
    source_geom.set_defaults(func=cmd_search_source_geometry)

    fit = subparsers.add_parser("fit-row-threshold", help="Fit global row-threshold parameters on sampled windows.")
    fit.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    fit.add_argument("--pe", type=float, default=52.64)
    fit.add_argument("--angle", type=float, default=0.0)
    fit.add_argument("--offset", type=float, default=0.0)
    fit.add_argument("--reverse-views", action="store_true")
    fit.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(fit)
    fit.add_argument("--rows", type=int, default=12, help="Rows per sampled window.")
    fit.add_argument("--width", type=int, default=512, help="Pixels per sampled window.")
    fit.add_argument("--sample-name-contains", action="append")
    fit.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    fit.add_argument("--min-black-ratio", type=float, default=0.02)
    fit.add_argument("--max-black-ratio", type=float, default=0.98)
    fit.add_argument("--max-per-target", type=int, default=18)
    fit.add_argument("--gamma-grid", default="0.16,0.18,0.22,0.25,0.30,0.36,0.50,0.70,1.00")
    fit.add_argument("--density-grid", default="0.18,0.22,0.25,0.30,0.36,0.45,0.60,0.80,1.00")
    fit.add_argument("--bias-grid", default="-0.12,-0.08,-0.05,-0.03,0.00,0.03")
    fit.add_argument("--period-grid", default="18.0")
    fit.add_argument("--threshold-count-grid", default="18")
    fit.add_argument("--phase-grid", help="Optional comma-separated phase_y grid; defaults to 0..threshold_count-1.")
    fit.add_argument("--phase-mode", default="modulo", choices=["modulo", "normalized"])
    fit.add_argument("--reference-ppi", type=int, default=4000)
    fit.add_argument("--scale-with-ppi", action=argparse.BooleanOptionalAction, default=True)
    fit.add_argument("--top", type=int, default=20)
    fit.add_argument("--progress-every", type=int, default=100)
    fit.add_argument("--output", type=Path)
    fit.set_defaults(func=cmd_fit_row_threshold)

    periodic = subparsers.add_parser("fit-periodic-threshold", help="Fit a global 2D periodic threshold matrix on sampled windows.")
    periodic.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    periodic.add_argument("--pe", type=float, default=52.64)
    periodic.add_argument("--angle", type=float, default=0.0)
    periodic.add_argument("--offset", type=float, default=0.0)
    periodic.add_argument("--reverse-views", action="store_true")
    periodic.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(periodic)
    periodic.add_argument("--rows", type=int, default=12, help="Rows per sampled window.")
    periodic.add_argument("--width", type=int, default=512, help="Pixels per sampled window.")
    periodic.add_argument("--sample-name-contains", action="append")
    periodic.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    periodic.add_argument("--min-black-ratio", type=float, default=0.02)
    periodic.add_argument("--max-black-ratio", type=float, default=0.98)
    periodic.add_argument("--max-per-target", type=int, default=18)
    periodic.add_argument("--gamma-grid", default="0.22")
    periodic.add_argument("--density-grid", default="0.18")
    periodic.add_argument("--bias-grid", default="-0.12")
    periodic.add_argument("--size-grid", default="1x18,2x18,3x18,6x18,9x18,18x18,18x36,36x18")
    periodic.add_argument("--period-x-grid", help="Optional comma-separated physical x periods at reference PPI; defaults to threshold width.")
    periodic.add_argument("--period-y-grid", help="Optional comma-separated physical y periods at reference PPI; defaults to threshold height.")
    periodic.add_argument("--phase-x-grid", default="0")
    periodic.add_argument("--phase-y-grid", default="0")
    periodic.add_argument("--scale-x-with-ppi", action=argparse.BooleanOptionalAction, default=True)
    periodic.add_argument("--scale-y-with-ppi", action=argparse.BooleanOptionalAction, default=True)
    periodic.add_argument("--reference-ppi", type=int, default=4000)
    periodic.add_argument("--top", type=int, default=12)
    periodic.add_argument("--progress-every", type=int, default=1)
    periodic.add_argument("--output", type=Path)
    periodic.set_defaults(func=cmd_fit_periodic_threshold)

    probe_fit = subparsers.add_parser("fit-probe-halftone", help="Fit explainable halftone candidates from 624 probe sampled windows.")
    probe_fit.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    probe_fit.add_argument("--pe", type=float, default=52.64)
    probe_fit.add_argument("--angle", type=float, default=0.0)
    probe_fit.add_argument("--offset", type=float, default=0.0)
    probe_fit.add_argument("--reverse-views", action="store_true")
    probe_fit.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(probe_fit)
    probe_fit.add_argument("--regions", help="Comma-separated probe region names; default uses shared tone/ramp/screen regions.")
    probe_fit.add_argument("--bands", default="left,q1,mid,q3,right")
    probe_fit.add_argument("--y-points-per-region", type=int, default=3)
    probe_fit.add_argument("--rows", type=int, default=24, help="Rows per sampled window.")
    probe_fit.add_argument("--width", type=int, default=1024, help="Pixels per sampled window.")
    probe_fit.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    probe_fit.add_argument("--min-black-ratio", type=float, default=0.02)
    probe_fit.add_argument("--max-black-ratio", type=float, default=0.98)
    probe_fit.add_argument("--max-per-target", type=int, default=64)
    probe_fit.add_argument("--strategy", default="row,periodic", help="Comma-separated: families,row,periodic.")
    probe_fit.add_argument("--family", action="append", help="Restrict families strategy to one or more candidate families.")
    probe_fit.add_argument("--gamma-grid", default="0.16,0.18,0.22,0.25,0.30,0.36,0.50,0.70,1.00")
    probe_fit.add_argument("--density-grid", default="0.18,0.22,0.25,0.30,0.36,0.45,0.60,0.80,1.00")
    probe_fit.add_argument("--bias-grid", default="-0.12,-0.08,-0.05,-0.03,0.00,0.03,0.06")
    probe_fit.add_argument("--period-grid", default="18.0,36.0,76.0,144.0,180.0,216.0,234.0,252.0,270.0,288.0,306.0,324.0,342.0,360.0,378.0,396.0,414.0,432.0,450.0,468.0,486.0,504.0")
    probe_fit.add_argument("--threshold-count-grid", default="18")
    probe_fit.add_argument("--phase-grid", help="Optional comma-separated phase_y grid; defaults to 0..threshold_count-1.")
    probe_fit.add_argument("--phase-mode", default="modulo", choices=["modulo", "normalized"])
    probe_fit.add_argument("--reference-ppi", type=int, default=4000)
    probe_fit.add_argument("--scale-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    probe_fit.add_argument("--size-grid", default="1x18,2x18,3x18,6x18,9x18,18x18,38x18,76x18,76x36")
    probe_fit.add_argument("--period-x-grid", default="76.0", help="Comma-separated physical x periods at reference PPI.")
    probe_fit.add_argument("--period-y-grid", default="18.0,36.0,76.0,144.0,180.0,216.0,234.0,252.0,270.0,288.0,306.0,324.0,342.0,360.0,378.0,396.0,414.0,432.0,450.0,468.0,486.0,504.0")
    probe_fit.add_argument("--phase-x-grid", default="0")
    probe_fit.add_argument("--phase-y-grid", default="0")
    probe_fit.add_argument("--scale-x-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    probe_fit.add_argument("--scale-y-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    probe_fit.add_argument("--top", type=int, default=20)
    probe_fit.add_argument("--progress-every", type=int, default=100)
    probe_fit.add_argument("--include-thresholds", action="store_true")
    probe_fit.add_argument("--json-only", action="store_true")
    probe_fit.add_argument("--output", type=Path)
    probe_fit.set_defaults(func=cmd_fit_probe_halftone)

    tone_fit = subparsers.add_parser("fit-probe-tone-block-screen", help="Fit row/periodic screens from known flat probe tone blocks.")
    tone_fit.add_argument("--dataset-root", type=Path, default=Path("probe_dataset_lby_return_20260624"))
    tone_fit.add_argument("--pe", type=float, default=52.64)
    tone_fit.add_argument("--angle", type=float, default=0.0)
    tone_fit.add_argument("--offset", type=float, default=0.0)
    tone_fit.add_argument("--reverse-views", action="store_true")
    tone_fit.add_argument("--coordinate-mode", default="align_corners", choices=["align_corners", "half_pixel", "asymmetric", "floor_nearest"])
    add_source_geometry_args(tone_fit)
    tone_fit.add_argument("--rows", type=int, default=72, help="Rows per flat tone-block sampled window.")
    tone_fit.add_argument("--width", type=int, default=1024, help="Pixels per flat tone-block sampled window.")
    tone_fit.add_argument("--x-points-per-block", type=int, default=1)
    tone_fit.add_argument("--y-points-per-block", type=int, default=1)
    tone_fit.add_argument("--include-ramp-overlap-row", action=argparse.BooleanOptionalAction, default=True)
    tone_fit.add_argument("--informative-only", action=argparse.BooleanOptionalAction, default=True)
    tone_fit.add_argument("--min-black-ratio", type=float, default=0.02)
    tone_fit.add_argument("--max-black-ratio", type=float, default=0.98)
    tone_fit.add_argument("--max-per-target", type=int, default=240)
    tone_fit.add_argument("--strategy", default="row,periodic", help="Comma-separated: row,transition,diffusion,microspot,periodic.")
    tone_fit.add_argument("--gamma-grid", default="0.35,0.5,0.7,1.0,1.3,1.8")
    tone_fit.add_argument("--density-grid", default="1.0")
    tone_fit.add_argument("--bias-grid", default="0.0")
    tone_fit.add_argument("--period-grid", default="18")
    tone_fit.add_argument("--threshold-count-grid", default="18")
    tone_fit.add_argument("--phase-grid", help="Optional comma-separated phase_y grid; defaults to 0..threshold_count-1.")
    tone_fit.add_argument("--phase-mode", default="modulo", choices=["modulo", "normalized"])
    tone_fit.add_argument("--reference-ppi", type=int, default=4000)
    tone_fit.add_argument("--scale-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    tone_fit.add_argument("--transition-width-grid", default="0.02,0.04,0.06,0.08,0.12,0.18")
    tone_fit.add_argument("--transition-dither-grid", default="bayer,hash")
    tone_fit.add_argument("--transition-matrix-size-grid", default="2,3,4,8,16")
    tone_fit.add_argument("--transition-seed-grid", default="0,1,17,149,624,20260624")
    tone_fit.add_argument("--diffusion-kernel-grid", default="fs,jjn,stucki")
    tone_fit.add_argument("--diffusion-strength-grid", default="0.35,0.5,0.75,1.0,1.25")
    tone_fit.add_argument("--diffusion-threshold-bias-grid", default="-0.06,-0.03,0.0,0.03,0.06")
    tone_fit.add_argument("--diffusion-serpentine-grid", default="false,true")
    tone_fit.add_argument("--microspot-transition-width-grid", default="0.02,0.04,0.06,0.08,0.12")
    tone_fit.add_argument("--microspot-cell-x-grid", default="2.5,2.65,2.6666667,3,4.05")
    tone_fit.add_argument("--microspot-cell-y-grid", default="1,2,3")
    tone_fit.add_argument("--microspot-shape-grid", default="line,round,diamond")
    tone_fit.add_argument("--microspot-phase-x-grid", default="0")
    tone_fit.add_argument("--microspot-phase-y-grid", default="0")
    tone_fit.add_argument("--size-grid", default="1x18,2x18,3x18,6x18,9x18,18x18,38x18,76x18")
    tone_fit.add_argument("--period-x-grid", default="76")
    tone_fit.add_argument("--period-y-grid", default="18")
    tone_fit.add_argument("--phase-x-grid", default="0,19,38,57")
    tone_fit.add_argument("--phase-y-grid", default="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17")
    tone_fit.add_argument("--scale-x-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    tone_fit.add_argument("--scale-y-with-ppi", action=argparse.BooleanOptionalAction, default=False)
    tone_fit.add_argument("--top", type=int, default=20)
    tone_fit.add_argument("--progress-every", type=int, default=25)
    tone_fit.add_argument("--include-thresholds", action="store_true")
    tone_fit.add_argument("--json-only", action="store_true")
    tone_fit.add_argument("--output", type=Path)
    tone_fit.set_defaults(func=cmd_fit_probe_tone_block_screen)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
