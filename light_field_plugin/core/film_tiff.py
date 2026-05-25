# 1-bit TIFF and halftone utilities.
#
# This module intentionally avoids Blender-only imports so it can be unit tested
# with regular Python and reused from Blender operators.

from __future__ import annotations

import math
import os
import struct
from typing import Iterable, List, Sequence


LumaRows = Sequence[Sequence[int]]
BitmapRows = Sequence[Sequence[bool]]


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def apply_gamma(luma_rows: LumaRows, gamma: float) -> List[List[int]]:
    if abs(gamma - 1.0) < 1e-6:
        return [[_clamp_byte(v) for v in row] for row in luma_rows]

    inv_gamma = 1.0 / max(gamma, 1e-6)
    corrected = []
    for row in luma_rows:
        corrected.append([
            _clamp_byte((max(0, min(255, v)) / 255.0) ** inv_gamma * 255.0)
            for v in row
        ])
    return corrected


def fm_error_diffusion_halftone(luma_rows: LumaRows) -> List[List[bool]]:
    """Return True for black pixels using Floyd-Steinberg error diffusion."""

    height = len(luma_rows)
    width = len(luma_rows[0]) if height else 0
    work = [[max(0.0, min(1.0, v / 255.0)) for v in row] for row in luma_rows]
    black = [[False for _ in range(width)] for _ in range(height)]

    for y in range(height):
        for x in range(width):
            old = work[y][x]
            new = 1.0 if old >= 0.5 else 0.0
            black[y][x] = new == 0.0
            error = old - new

            if x + 1 < width:
                work[y][x + 1] += error * 7.0 / 16.0
            if y + 1 < height:
                if x > 0:
                    work[y + 1][x - 1] += error * 3.0 / 16.0
                work[y + 1][x] += error * 5.0 / 16.0
                if x + 1 < width:
                    work[y + 1][x + 1] += error * 1.0 / 16.0

    return black


def am_clustered_halftone(
    luma_rows: LumaRows,
    dpi: int,
    lpi: int,
    angle_degrees: float,
    dot_shape: str,
) -> List[List[bool]]:
    """Return True for black pixels using a clustered AM screen."""

    height = len(luma_rows)
    width = len(luma_rows[0]) if height else 0
    cell = max(2.0, float(dpi) / max(1.0, float(lpi)))
    angle = math.radians(angle_degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    shape = (dot_shape or "ROUND").upper()

    black = [[False for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            xr = x * cos_a + y * sin_a
            yr = -x * sin_a + y * cos_a
            u = ((xr / cell) - math.floor(xr / cell)) * 2.0 - 1.0
            v = ((yr / cell) - math.floor(yr / cell)) * 2.0 - 1.0

            darkness = 1.0 - max(0.0, min(1.0, luma_rows[y][x] / 255.0))
            if darkness <= 0.0:
                continue
            if darkness >= 1.0:
                black[y][x] = True
                continue

            if shape == "DIAMOND":
                metric = (abs(u) + abs(v)) / 2.0
                threshold = darkness
            elif shape == "ELLIPSE":
                metric = math.sqrt(u * u + (v / 0.65) * (v / 0.65))
                threshold = math.sqrt(darkness)
            else:
                metric = math.sqrt(u * u + v * v)
                threshold = math.sqrt(darkness)

            black[y][x] = metric <= threshold

    return black


def halftone_luma(
    luma_rows: LumaRows,
    method: str = "FM",
    dpi: int = 2400,
    lpi: int = 200,
    angle_degrees: float = 45.0,
    dot_shape: str = "ROUND",
    gamma: float = 1.0,
) -> List[List[bool]]:
    corrected = apply_gamma(luma_rows, gamma)
    if (method or "FM").upper() == "AM":
        return am_clustered_halftone(corrected, dpi, lpi, angle_degrees, dot_shape)
    return fm_error_diffusion_halftone(corrected)


def _pack_bits(black_rows: BitmapRows) -> bytes:
    packed = bytearray()
    for row in black_rows:
        byte = 0
        bit_count = 0
        for is_black in row:
            byte = (byte << 1) | (1 if is_black else 0)
            bit_count += 1
            if bit_count == 8:
                packed.append(byte)
                byte = 0
                bit_count = 0
        if bit_count:
            packed.append(byte << (8 - bit_count))
    return bytes(packed)


def _ifd_entry(tag: int, field_type: int, count: int, value_or_offset: int) -> bytes:
    return struct.pack("<HHII", tag, field_type, count, value_or_offset)


def _short_value(value: int) -> int:
    return value & 0xFFFF


def write_1bit_tiff(path: str, black_rows: BitmapRows, dpi: int = 2400) -> None:
    """Write an uncompressed, single-strip, 1-bit TIFF.

    Pixel convention: True means black ink/dot, False means paper/white.
    TIFF photometric interpretation is WhiteIsZero, so bit value 1 is black.
    """

    height = len(black_rows)
    width = len(black_rows[0]) if height else 0
    if width <= 0 or height <= 0:
        raise ValueError("Cannot write an empty TIFF image")
    if any(len(row) != width for row in black_rows):
        raise ValueError("All bitmap rows must have the same width")

    image_data = _pack_bits(black_rows)
    software = b"Light Field Render 1-bit TIFF\0"

    entry_count = 13
    ifd_offset = 8
    ifd_size = 2 + entry_count * 12 + 4
    xres_offset = ifd_offset + ifd_size
    yres_offset = xres_offset + 8
    software_offset = yres_offset + 8
    image_offset = software_offset + len(software)

    entries = [
        _ifd_entry(256, 4, 1, width),  # ImageWidth
        _ifd_entry(257, 4, 1, height),  # ImageLength
        _ifd_entry(258, 3, 1, _short_value(1)),  # BitsPerSample
        _ifd_entry(259, 3, 1, _short_value(1)),  # Compression: none
        _ifd_entry(262, 3, 1, _short_value(0)),  # WhiteIsZero
        _ifd_entry(273, 4, 1, image_offset),  # StripOffsets
        _ifd_entry(277, 3, 1, _short_value(1)),  # SamplesPerPixel
        _ifd_entry(278, 4, 1, height),  # RowsPerStrip
        _ifd_entry(279, 4, 1, len(image_data)),  # StripByteCounts
        _ifd_entry(282, 5, 1, xres_offset),  # XResolution
        _ifd_entry(283, 5, 1, yres_offset),  # YResolution
        _ifd_entry(296, 3, 1, _short_value(2)),  # inch
        _ifd_entry(305, 2, len(software), software_offset),  # Software
    ]
    entries.sort(key=lambda item: struct.unpack("<H", item[:2])[0])

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"II")
        f.write(struct.pack("<H", 42))
        f.write(struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", entry_count))
        for entry in entries:
            f.write(entry)
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<II", int(dpi), 1))
        f.write(struct.pack("<II", int(dpi), 1))
        f.write(software)
        f.write(image_data)


def write_halftoned_1bit_tiff(
    path: str,
    luma_rows: LumaRows,
    method: str = "FM",
    dpi: int = 2400,
    lpi: int = 200,
    angle_degrees: float = 45.0,
    dot_shape: str = "ROUND",
    gamma: float = 1.0,
) -> List[List[bool]]:
    black_rows = halftone_luma(
        luma_rows,
        method=method,
        dpi=dpi,
        lpi=lpi,
        angle_degrees=angle_degrees,
        dot_shape=dot_shape,
        gamma=gamma,
    )
    write_1bit_tiff(path, black_rows, dpi=dpi)
    return black_rows
