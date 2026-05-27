# Final delivery interlace and TIFF output utilities.
#
# This module intentionally avoids Blender imports so the delivery pipeline can
# be unit-tested with regular Python. It also avoids third-party dependencies.

from __future__ import annotations

import json
import math
import os
import struct
import time
import traceback
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional, Sequence, Tuple


MM_PER_INCH = 25.4
LARGE_OUTPUT_PIXELS = 100_000_000
SOURCE_UPSCALE_WARNING_FACTOR = 2.0
PREVIEW_MAX_EDGE = 2048


class DeliveryError(RuntimeError):
    """Base error for final delivery generation."""


class DeliveryCancelled(DeliveryError):
    """Raised when the user requests delivery generation to stop."""


@dataclass
class InterlaceSettings:
    pe: float = 16.7240
    angle_degrees: float = math.degrees(0.106395)
    offset: float = 12.5
    reverse_views: bool = False


@dataclass
class HalftoneSettings:
    method: str = "FM"
    lpi: int = 200
    angle_degrees: float = 45.0
    dot_shape: str = "ROUND"
    gamma: float = 1.0


@dataclass
class DeliverySettings:
    width_mm: float
    height_mm: float
    ppi: int
    frame: int
    camera_count: int
    source_width: int
    source_height: int
    interlace: InterlaceSettings
    halftone: HalftoneSettings
    plugin_version: str = ""
    preview_max_edge: int = PREVIEW_MAX_EDGE
    large_output_pixels: int = LARGE_OUTPUT_PIXELS
    confirm_large_output: bool = False


@dataclass
class DeliveryPaths:
    output_dir: str
    interlaced_tiff: str
    preview_png: str
    film_1bit_tiff: str
    manifest_json: str
    error_log: str


@dataclass
class DeliveryResult:
    width_px: int
    height_px: int
    preview_width: int
    preview_height: int
    elapsed_seconds: float
    paths: DeliveryPaths
    large_output_warning: bool
    source_upscale_warning: bool


ProgressCallback = Callable[[str, int, int, str], None]
StopCallback = Callable[[], bool]


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def calculate_delivery_pixels(width_mm: float, height_mm: float, ppi: int) -> Tuple[int, int]:
    if width_mm <= 0:
        raise ValueError("Delivery width must be greater than 0 mm")
    if height_mm <= 0:
        raise ValueError("Delivery height must be greater than 0 mm")
    if ppi <= 0:
        raise ValueError("Delivery PPI must be greater than 0")
    return (
        max(1, round_half_up(width_mm / MM_PER_INCH * ppi)),
        max(1, round_half_up(height_mm / MM_PER_INCH * ppi)),
    )


def is_large_output(width_px: int, height_px: int, threshold: int = LARGE_OUTPUT_PIXELS) -> bool:
    return width_px * height_px > threshold


def has_source_upscale_warning(
    final_width: int,
    final_height: int,
    source_width: int,
    source_height: int,
    factor: float = SOURCE_UPSCALE_WARNING_FACTOR,
) -> bool:
    if source_width <= 0 or source_height <= 0:
        return False
    return final_width > source_width * factor or final_height > source_height * factor


def preview_dimensions(width: int, height: int, max_edge: int = PREVIEW_MAX_EDGE) -> Tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Preview dimensions require a non-empty image")
    if max(width, height) <= max_edge:
        return width, height
    scale = max_edge / float(max(width, height))
    return max(1, round_half_up(width * scale)), max(1, round_half_up(height * scale))


def interlace_view_index(
    x: int,
    y: int,
    channel: int,
    num_views: int,
    pe: float,
    angle_radians: float,
    offset: float,
) -> int:
    if num_views <= 0:
        raise ValueError("num_views must be greater than 0")
    if pe <= 0:
        raise ValueError("PE must be greater than 0")
    d_value = 3.0 * x + 3.0 * y * math.tan(angle_radians) + channel + offset
    a_value = d_value % pe
    view = int(math.floor(a_value / (pe / num_views))) % num_views
    return view


def build_view_order(num_views: int, reverse: bool = False) -> List[int]:
    order = list(range(num_views))
    if reverse:
        order.reverse()
    return order


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


class PngImage:
    def __init__(self, width: int, height: int, data: bytes):
        self.width = width
        self.height = height
        self.data = data

    def _offset(self, x: int, y: int) -> int:
        return (y * self.width + x) * 3

    def pixel_rgb(self, x: int, y: int) -> Tuple[int, int, int]:
        x = max(0, min(self.width - 1, x))
        y = max(0, min(self.height - 1, y))
        offset = self._offset(x, y)
        return self.data[offset], self.data[offset + 1], self.data[offset + 2]

    def sample_bilinear(self, x: float, y: float) -> Tuple[int, int, int]:
        if self.width == 1 and self.height == 1:
            return self.pixel_rgb(0, 0)

        x = max(0.0, min(float(self.width - 1), x))
        y = max(0.0, min(float(self.height - 1), y))
        x0 = int(math.floor(x))
        y0 = int(math.floor(y))
        x1 = min(self.width - 1, x0 + 1)
        y1 = min(self.height - 1, y0 + 1)
        tx = x - x0
        ty = y - y0

        p00 = self.pixel_rgb(x0, y0)
        p10 = self.pixel_rgb(x1, y0)
        p01 = self.pixel_rgb(x0, y1)
        p11 = self.pixel_rgb(x1, y1)

        result = []
        for channel in range(3):
            top = p00[channel] * (1.0 - tx) + p10[channel] * tx
            bottom = p01[channel] * (1.0 - tx) + p11[channel] * tx
            result.append(max(0, min(255, int(round(top * (1.0 - ty) + bottom * ty)))))
        return result[0], result[1], result[2]


def read_png_info(path: str) -> Tuple[int, int, int, int]:
    with open(path, "rb") as f:
        signature = f.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise DeliveryError(f"Not a PNG file: {path}")
        while True:
            length_data = f.read(4)
            if len(length_data) != 4:
                raise DeliveryError(f"PNG missing IHDR: {path}")
            length = struct.unpack(">I", length_data)[0]
            chunk_type = f.read(4)
            data = f.read(length)
            f.read(4)
            if chunk_type == b"IHDR":
                width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                    ">IIBBBBB",
                    data,
                )
                return width, height, bit_depth, color_type
            if chunk_type == b"IEND":
                raise DeliveryError(f"PNG missing IHDR: {path}")


def read_png_rgb(path: str) -> PngImage:
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DeliveryError(f"Not a PNG file: {path}")

    cursor = 8
    width = height = bit_depth = color_type = interlace = None
    idat_parts = []
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise DeliveryError(f"Corrupt PNG chunk header: {path}")
        length = struct.unpack(">I", data[cursor:cursor + 4])[0]
        chunk_type = data[cursor + 4:cursor + 8]
        chunk_data = data[cursor + 8:cursor + 8 + length]
        cursor += 12 + length

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB",
                chunk_data,
            )
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None:
        raise DeliveryError(f"PNG missing IHDR: {path}")
    if bit_depth != 8:
        raise DeliveryError(f"Only 8-bit PNG sources are supported: {path}")
    if interlace != 0:
        raise DeliveryError(f"Interlaced PNG sources are not supported: {path}")
    if color_type not in {0, 2, 4, 6}:
        raise DeliveryError(f"Unsupported PNG color type {color_type}: {path}")

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    bpp = channels
    scanline_len = width * channels
    raw = zlib.decompress(b"".join(idat_parts))
    expected = (scanline_len + 1) * height
    if len(raw) < expected:
        raise DeliveryError(f"PNG image data is truncated: {path}")

    previous = bytearray(scanline_len)
    rows = []
    offset = 0
    for _y in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + scanline_len])
        offset += scanline_len

        for i in range(scanline_len):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            upper_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 0:
                recon = row[i]
            elif filter_type == 1:
                recon = (row[i] + left) & 0xFF
            elif filter_type == 2:
                recon = (row[i] + up) & 0xFF
            elif filter_type == 3:
                recon = (row[i] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                recon = (row[i] + _paeth_predictor(left, up, upper_left)) & 0xFF
            else:
                raise DeliveryError(f"Unsupported PNG filter {filter_type}: {path}")
            row[i] = recon

        rows.append(bytes(row))
        previous = row

    rgb = bytearray(width * height * 3)
    target = 0
    for row in rows:
        source = 0
        for _x in range(width):
            if color_type == 0:
                r = g = b = row[source]
            elif color_type == 2:
                r, g, b = row[source], row[source + 1], row[source + 2]
            elif color_type == 4:
                r = g = b = row[source]
            else:
                r, g, b = row[source], row[source + 1], row[source + 2]
            rgb[target:target + 3] = bytes((r, g, b))
            source += channels
            target += 3

    return PngImage(width, height, bytes(rgb))


def write_rgb_png(path: str, width: int, height: int, rows: Iterable[bytes]) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("Cannot write an empty PNG image")
    raw = bytearray()
    expected = width * 3
    count = 0
    for row in rows:
        if len(row) != expected:
            raise ValueError("PNG RGB row length does not match image width")
        raw.append(0)
        raw.extend(row)
        count += 1
    if count != height:
        raise ValueError("PNG row count does not match image height")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=6)))
        f.write(_png_chunk(b"IEND", b""))


def _ifd_entry(tag: int, field_type: int, count: int, value_or_offset: int) -> bytes:
    return struct.pack("<HHII", tag, field_type, count, value_or_offset)


def _short_value(value: int) -> int:
    return value & 0xFFFF


class RgbTiffWriter:
    def __init__(self, path: str, width: int, height: int, dpi: int):
        self.path = path
        self.width = width
        self.height = height
        self.dpi = int(dpi)
        self._file = None
        self._rows_written = 0

    def __enter__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Cannot write an empty TIFF image")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._file = open(self.path, "wb")

        software = b"Light Field Render RGB TIFF\0"
        entry_count = 13
        ifd_offset = 8
        ifd_size = 2 + entry_count * 12 + 4
        bits_offset = ifd_offset + ifd_size
        xres_offset = bits_offset + 6
        yres_offset = xres_offset + 8
        software_offset = yres_offset + 8
        image_offset = software_offset + len(software)
        image_bytes = self.width * self.height * 3

        entries = [
            _ifd_entry(256, 4, 1, self.width),
            _ifd_entry(257, 4, 1, self.height),
            _ifd_entry(258, 3, 3, bits_offset),
            _ifd_entry(259, 3, 1, _short_value(1)),
            _ifd_entry(262, 3, 1, _short_value(2)),
            _ifd_entry(273, 4, 1, image_offset),
            _ifd_entry(277, 3, 1, _short_value(3)),
            _ifd_entry(278, 4, 1, self.height),
            _ifd_entry(279, 4, 1, image_bytes),
            _ifd_entry(282, 5, 1, xres_offset),
            _ifd_entry(283, 5, 1, yres_offset),
            _ifd_entry(296, 3, 1, _short_value(2)),
            _ifd_entry(305, 2, len(software), software_offset),
        ]
        entries.sort(key=lambda item: struct.unpack("<H", item[:2])[0])

        f = self._file
        f.write(b"II")
        f.write(struct.pack("<H", 42))
        f.write(struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", entry_count))
        for entry in entries:
            f.write(entry)
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<HHH", 8, 8, 8))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(software)
        return self

    def write_row(self, row: bytes) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if len(row) != self.width * 3:
            raise ValueError("TIFF RGB row length does not match image width")
        self._file.write(row)
        self._rows_written += 1

    def __exit__(self, exc_type, exc, tb):
        if self._file is not None:
            self._file.close()
            self._file = None
        if exc_type is None and self._rows_written != self.height:
            raise ValueError("TIFF row count does not match image height")
        return False


class OneBitTiffWriter:
    def __init__(self, path: str, width: int, height: int, dpi: int):
        self.path = path
        self.width = width
        self.height = height
        self.dpi = int(dpi)
        self.row_bytes = (width + 7) // 8
        self._file = None
        self._rows_written = 0

    def __enter__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Cannot write an empty TIFF image")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._file = open(self.path, "wb")

        software = b"Light Field Render 1-bit TIFF\0"
        entry_count = 13
        ifd_offset = 8
        ifd_size = 2 + entry_count * 12 + 4
        xres_offset = ifd_offset + ifd_size
        yres_offset = xres_offset + 8
        software_offset = yres_offset + 8
        image_offset = software_offset + len(software)
        image_bytes = self.row_bytes * self.height

        entries = [
            _ifd_entry(256, 4, 1, self.width),
            _ifd_entry(257, 4, 1, self.height),
            _ifd_entry(258, 3, 1, _short_value(1)),
            _ifd_entry(259, 3, 1, _short_value(1)),
            _ifd_entry(262, 3, 1, _short_value(0)),
            _ifd_entry(273, 4, 1, image_offset),
            _ifd_entry(277, 3, 1, _short_value(1)),
            _ifd_entry(278, 4, 1, self.height),
            _ifd_entry(279, 4, 1, image_bytes),
            _ifd_entry(282, 5, 1, xres_offset),
            _ifd_entry(283, 5, 1, yres_offset),
            _ifd_entry(296, 3, 1, _short_value(2)),
            _ifd_entry(305, 2, len(software), software_offset),
        ]
        entries.sort(key=lambda item: struct.unpack("<H", item[:2])[0])

        f = self._file
        f.write(b"II")
        f.write(struct.pack("<H", 42))
        f.write(struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", entry_count))
        for entry in entries:
            f.write(entry)
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(software)
        return self

    def write_black_row(self, black_row: Sequence[bool]) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if len(black_row) != self.width:
            raise ValueError("1-bit TIFF row length does not match image width")
        packed = bytearray()
        byte = 0
        bit_count = 0
        for is_black in black_row:
            byte = (byte << 1) | (1 if is_black else 0)
            bit_count += 1
            if bit_count == 8:
                packed.append(byte)
                byte = 0
                bit_count = 0
        if bit_count:
            packed.append(byte << (8 - bit_count))
        self._file.write(packed)
        self._rows_written += 1

    def __exit__(self, exc_type, exc, tb):
        if self._file is not None:
            self._file.close()
            self._file = None
        if exc_type is None and self._rows_written != self.height:
            raise ValueError("1-bit TIFF row count does not match image height")
        return False


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _gamma_correct_luma(luma: float, gamma: float) -> float:
    inv_gamma = 1.0 / max(float(gamma), 1e-6)
    return (max(0.0, min(255.0, luma)) / 255.0) ** inv_gamma


class StreamingHalftoner:
    def __init__(self, width: int, settings: HalftoneSettings, dpi: int):
        self.width = width
        self.settings = settings
        self.dpi = int(dpi)
        self._next_errors = [0.0 for _ in range(width)]
        self._method = (settings.method or "FM").upper()
        self._angle = math.radians(settings.angle_degrees)
        self._cos = math.cos(self._angle)
        self._sin = math.sin(self._angle)
        self._cell = max(2.0, float(self.dpi) / max(1.0, float(settings.lpi)))

    def process_rgb_row(self, y: int, row: bytes) -> List[bool]:
        if len(row) != self.width * 3:
            raise ValueError("Halftone row length does not match image width")
        if self._method == "AM":
            return self._process_am_row(y, row)
        return self._process_fm_row(row)

    def _row_luma_values(self, row: bytes) -> List[float]:
        values = []
        for x in range(self.width):
            offset = x * 3
            r = row[offset]
            g = row[offset + 1]
            b = row[offset + 2]
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            values.append(_gamma_correct_luma(luma, self.settings.gamma))
        return values

    def _process_fm_row(self, row: bytes) -> List[bool]:
        work = [value + err for value, err in zip(self._row_luma_values(row), self._next_errors)]
        next_errors = [0.0 for _ in range(self.width)]
        black = [False for _ in range(self.width)]

        for x in range(self.width):
            old = max(0.0, min(1.0, work[x]))
            new = 1.0 if old >= 0.5 else 0.0
            black[x] = new == 0.0
            error = old - new
            if x + 1 < self.width:
                work[x + 1] += error * 7.0 / 16.0
            if x > 0:
                next_errors[x - 1] += error * 3.0 / 16.0
            next_errors[x] += error * 5.0 / 16.0
            if x + 1 < self.width:
                next_errors[x + 1] += error * 1.0 / 16.0

        self._next_errors = next_errors
        return black

    def _process_am_row(self, y: int, row: bytes) -> List[bool]:
        shape = (self.settings.dot_shape or "ROUND").upper()
        black = [False for _ in range(self.width)]
        lumas = self._row_luma_values(row)
        for x, luma_norm in enumerate(lumas):
            xr = x * self._cos + y * self._sin
            yr = -x * self._sin + y * self._cos
            u = ((xr / self._cell) - math.floor(xr / self._cell)) * 2.0 - 1.0
            v = ((yr / self._cell) - math.floor(yr / self._cell)) * 2.0 - 1.0
            darkness = 1.0 - max(0.0, min(1.0, luma_norm))
            if darkness <= 0.0:
                continue
            if darkness >= 1.0:
                black[x] = True
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
            black[x] = metric <= threshold
        return black


class InterlaceRenderer:
    def __init__(self, source_paths: Sequence[str], settings: DeliverySettings, width_px: int, height_px: int):
        if len(source_paths) != settings.camera_count:
            raise DeliveryError("Source view count does not match camera count")
        self.sources = [read_png_rgb(path) for path in source_paths]
        self.settings = settings
        self.width_px = width_px
        self.height_px = height_px
        self.view_order = build_view_order(settings.camera_count, settings.interlace.reverse_views)
        self.angle_radians = math.radians(settings.interlace.angle_degrees)

    def _sample_channel(self, final_x: int, final_y: int, channel: int) -> int:
        view = interlace_view_index(
            final_x,
            final_y,
            channel,
            self.settings.camera_count,
            self.settings.interlace.pe,
            self.angle_radians,
            self.settings.interlace.offset,
        )
        source = self.sources[self.view_order[view]]
        if self.width_px <= 1:
            source_x = 0.0
        else:
            source_x = final_x * (source.width - 1) / float(self.width_px - 1)
        if self.height_px <= 1:
            source_y = 0.0
        else:
            source_y = final_y * (source.height - 1) / float(self.height_px - 1)
        return source.sample_bilinear(source_x, source_y)[channel]

    def generate_final_row(self, y: int) -> bytes:
        row = bytearray(self.width_px * 3)
        for x in range(self.width_px):
            target = x * 3
            for channel in range(3):
                row[target + channel] = self._sample_channel(x, y, channel)
        return bytes(row)

    def generate_preview_row(self, preview_y: int, preview_width: int, preview_height: int) -> bytes:
        row = bytearray(preview_width * 3)
        if preview_height <= 1:
            final_y = 0
        else:
            final_y = round_half_up(preview_y * (self.height_px - 1) / float(preview_height - 1))
        final_y = max(0, min(self.height_px - 1, final_y))
        for px in range(preview_width):
            if preview_width <= 1:
                final_x = 0
            else:
                final_x = round_half_up(px * (self.width_px - 1) / float(preview_width - 1))
            final_x = max(0, min(self.width_px - 1, final_x))
            target = px * 3
            for channel in range(3):
                row[target + channel] = self._sample_channel(final_x, final_y, channel)
        return bytes(row)


def make_delivery_paths(output_root: str, frame: int) -> DeliveryPaths:
    output_dir = os.path.join(output_root, "delivery", f"frame_{frame:04d}")
    return DeliveryPaths(
        output_dir=output_dir,
        interlaced_tiff=os.path.join(output_dir, "interlaced.tif"),
        preview_png=os.path.join(output_dir, "interlaced_preview.png"),
        film_1bit_tiff=os.path.join(output_dir, "film_1bit.tif"),
        manifest_json=os.path.join(output_dir, "delivery_manifest.json"),
        error_log=os.path.join(output_dir, "delivery_error.log"),
    )


def _tmp_path(path: str) -> str:
    return path + ".tmp"


def _remove_if_exists(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _check_stop(stop_callback: Optional[StopCallback]) -> None:
    if stop_callback and stop_callback():
        raise DeliveryCancelled("Delivery generation was stopped by user")


def _emit_progress(
    progress_callback: Optional[ProgressCallback],
    stage: str,
    current: int,
    total: int,
    info: str = "",
) -> None:
    if progress_callback:
        progress_callback(stage, current, total, info)


def generate_delivery_outputs(
    source_paths: Sequence[str],
    output_root: str,
    settings: DeliverySettings,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    stop_callback: Optional[StopCallback] = None,
) -> DeliveryResult:
    start_time = time.perf_counter()
    width_px, height_px = calculate_delivery_pixels(settings.width_mm, settings.height_mm, settings.ppi)
    large_warning = is_large_output(width_px, height_px, settings.large_output_pixels)
    if large_warning and not settings.confirm_large_output:
        raise DeliveryError("Final delivery image is larger than the configured safety threshold")
    upscale_warning = has_source_upscale_warning(
        width_px,
        height_px,
        settings.source_width,
        settings.source_height,
    )

    paths = make_delivery_paths(output_root, settings.frame)
    os.makedirs(paths.output_dir, exist_ok=True)
    tmp_files = [
        _tmp_path(paths.interlaced_tiff),
        _tmp_path(paths.preview_png),
        _tmp_path(paths.film_1bit_tiff),
        _tmp_path(paths.manifest_json),
    ]
    for tmp in tmp_files:
        _remove_if_exists(tmp)

    try:
        _emit_progress(progress_callback, "加载源视角", 0, settings.camera_count)
        renderer = InterlaceRenderer(source_paths, settings, width_px, height_px)
        halftoner = StreamingHalftoner(width_px, settings.halftone, settings.ppi)

        _emit_progress(progress_callback, "生成交织 TIFF 和 1-bit TIFF", 0, height_px)
        with RgbTiffWriter(_tmp_path(paths.interlaced_tiff), width_px, height_px, settings.ppi) as rgb_writer, \
                OneBitTiffWriter(_tmp_path(paths.film_1bit_tiff), width_px, height_px, settings.ppi) as bit_writer:
            progress_step = max(1, height_px // 100)
            for y in range(height_px):
                _check_stop(stop_callback)
                row = renderer.generate_final_row(y)
                rgb_writer.write_row(row)
                bit_writer.write_black_row(halftoner.process_rgb_row(y, row))
                if y % progress_step == 0 or y == height_px - 1:
                    _emit_progress(
                        progress_callback,
                        "生成交织 TIFF 和 1-bit TIFF",
                        y + 1,
                        height_px,
                        f"{y + 1}/{height_px}",
                    )

        preview_width, preview_height = preview_dimensions(width_px, height_px, settings.preview_max_edge)
        _emit_progress(progress_callback, "生成 PNG 预览", 0, preview_height)

        def preview_rows():
            progress_step = max(1, preview_height // 100)
            for y in range(preview_height):
                _check_stop(stop_callback)
                if y % progress_step == 0 or y == preview_height - 1:
                    _emit_progress(progress_callback, "生成 PNG 预览", y + 1, preview_height)
                yield renderer.generate_preview_row(y, preview_width, preview_height)

        write_rgb_png(_tmp_path(paths.preview_png), preview_width, preview_height, preview_rows())

        elapsed = time.perf_counter() - start_time
        result = DeliveryResult(
            width_px=width_px,
            height_px=height_px,
            preview_width=preview_width,
            preview_height=preview_height,
            elapsed_seconds=elapsed,
            paths=paths,
            large_output_warning=large_warning,
            source_upscale_warning=upscale_warning,
        )
        manifest = build_manifest(settings, result, source_paths)
        with open(_tmp_path(paths.manifest_json), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        os.replace(_tmp_path(paths.interlaced_tiff), paths.interlaced_tiff)
        os.replace(_tmp_path(paths.film_1bit_tiff), paths.film_1bit_tiff)
        os.replace(_tmp_path(paths.preview_png), paths.preview_png)
        os.replace(_tmp_path(paths.manifest_json), paths.manifest_json)
        _remove_if_exists(paths.error_log)
        _emit_progress(progress_callback, "完成", 1, 1, paths.output_dir)
        return result
    except Exception:
        for tmp in tmp_files:
            _remove_if_exists(tmp)
        raise


def build_manifest(settings: DeliverySettings, result: DeliveryResult, source_paths: Sequence[str]) -> dict:
    return {
        "plugin_version": settings.plugin_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frame": settings.frame,
        "delivery": {
            "width_mm": settings.width_mm,
            "height_mm": settings.height_mm,
            "ppi": settings.ppi,
            "width_px": result.width_px,
            "height_px": result.height_px,
            "preview_width_px": result.preview_width,
            "preview_height_px": result.preview_height,
        },
        "source_views": {
            "camera_count": settings.camera_count,
            "source_width_px": settings.source_width,
            "source_height_px": settings.source_height,
            "order": "reversed" if settings.interlace.reverse_views else "ascending",
            "files": [os.path.basename(path) for path in source_paths],
        },
        "interlace": asdict(settings.interlace),
        "halftone": {
            **asdict(settings.halftone),
            "ppi_as_tiff_dpi": settings.ppi,
        },
        "warnings": {
            "large_output": result.large_output_warning,
            "source_upscale": result.source_upscale_warning,
        },
        "files": {
            "interlaced_tiff": os.path.basename(result.paths.interlaced_tiff),
            "preview_png": os.path.basename(result.paths.preview_png),
            "film_1bit_tiff": os.path.basename(result.paths.film_1bit_tiff),
            "manifest_json": os.path.basename(result.paths.manifest_json),
        },
        "elapsed_seconds": result.elapsed_seconds,
    }


def write_error_log(path: str, stage: str, error: BaseException, settings: Optional[DeliverySettings] = None) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "error_type": type(error).__name__,
        "error": str(error),
        "settings": asdict(settings) if settings else None,
        "traceback": traceback.format_exc(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
