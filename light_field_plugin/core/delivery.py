# Final delivery interlace and TIFF output utilities.
#
# This module intentionally avoids Blender imports so the delivery pipeline can
# be unit-tested with regular Python. NumPy is optional at runtime; release
# builds can bundle it under light_field_plugin/_vendor for faster large output.

from __future__ import annotations

import json
import ctypes
import math
import os
import struct
import sys
import time
import traceback
import zlib
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO, Callable, Iterable, List, Optional, Sequence, Tuple


MM_PER_INCH = 25.4
LARGE_OUTPUT_PIXELS = 100_000_000
SOURCE_UPSCALE_WARNING_FACTOR = 2.0
PREVIEW_MAX_EDGE = 2048
UINT32_MAX = 0xFFFFFFFF
LBY_LIKE_BLACK_IS_ZERO = True
LBY_LINE_PERIOD_PX = 18.0
LBY_LINE_PHASE_Y = 0.0
LBY_LINE_GAMMA = 0.25
LBY_LINE_DENSITY = 0.25
LBY_LINE_BIAS = -0.05
LBY_LINE_THRESHOLDS = (
    0.95,
    0.1797855943441391,
    0.17851249873638153,
    0.17819088697433472,
    0.1600176990032196,
    0.1530652940273285,
    0.147835835814476,
    0.147835835814476,
    0.147835835814476,
    0.147835835814476,
    0.12555083632469177,
    0.11203472316265106,
    0.11203472316265106,
    0.10605093836784363,
    0.10605093836784363,
    0.10605093836784363,
    0.06879298388957977,
    0.038474876433610916,
)
LBY_LIKE_FIT_NOTE = (
    "whole-pixel PE interlace, reversed view order recommended by vendor pair, "
    "18 px horizontal row-threshold screen fitted on target-active regions from the 150 JPG -> TIFF sample; "
    "full-size target-active mismatch 3.7562% with a 32 px dilated target-black mask"
)


@dataclass(frozen=True)
class HalftonePrintVariant:
    name: str
    filename: str
    algorithm: str
    family: str
    gamma: float
    density: float
    bias: float
    threshold_offset: float
    seed: int
    description: str


HALFTONE_PRINT_VARIANTS: Tuple[HalftonePrintVariant, ...] = (
    HalftonePrintVariant(
        name="lby_low_fp",
        filename="film_1bit_lby_low_fp.tif",
        algorithm="LBY row-threshold screen with tuned global transfer",
        family="LBY_TUNED",
        gamma=0.25,
        density=0.25,
        bias=-0.025,
        threshold_offset=-0.02,
        seed=0,
        description="Lower false-positive LBY-tuned candidate; full-size overlap FPR 2.1782%, FNR 2.3465%.",
    ),
    HalftonePrintVariant(
        name="lby_balanced",
        filename="film_1bit_lby_balanced.tif",
        algorithm="LBY row-threshold screen with tuned global transfer",
        family="LBY_TUNED",
        gamma=0.36,
        density=0.22,
        bias=0.0,
        threshold_offset=0.0,
        seed=0,
        description="Balanced LBY-tuned candidate; full-size overlap FPR 2.3912%, FNR 1.9260%.",
    ),
    HalftonePrintVariant(
        name="lby_more_black",
        filename="film_1bit_lby_more_black.tif",
        algorithm="LBY row-threshold screen with tuned global transfer",
        family="LBY_TUNED",
        gamma=0.16,
        density=0.32,
        bias=-0.1,
        threshold_offset=-0.02,
        seed=0,
        description="More-black LBY-tuned candidate; full-size overlap FPR 2.6609%, FNR 1.5937%.",
    ),
)


_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

try:
    import numpy as _np
except Exception:
    _np = None


_NATIVE_DLL = None
_NATIVE_DLL_ATTEMPTED = False


def _load_native_dll():
    global _NATIVE_DLL, _NATIVE_DLL_ATTEMPTED
    if _NATIVE_DLL_ATTEMPTED:
        return _NATIVE_DLL
    _NATIVE_DLL_ATTEMPTED = True
    dll_path = os.path.join(os.path.dirname(__file__), "lightfield_native.dll")
    if not os.path.exists(dll_path):
        return None
    try:
        dll = ctypes.CDLL(dll_path)
        dll.lf_generate_am_batch.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.lf_generate_am_batch.restype = ctypes.c_int
        dll.lf_generate_lby_batch.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
        ]
        dll.lf_generate_lby_batch.restype = ctypes.c_int
        if hasattr(dll, "lf_decode_image_rgb"):
            dll.lf_decode_image_rgb.argtypes = [
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_int,
                ctypes.c_int,
            ]
            dll.lf_decode_image_rgb.restype = ctypes.c_int
        _NATIVE_DLL = dll
    except Exception:
        _NATIVE_DLL = None
    return _NATIVE_DLL


class DeliveryError(RuntimeError):
    """Base error for final delivery generation."""


class DeliveryCancelled(DeliveryError):
    """Raised when the user requests delivery generation to stop."""


@dataclass(frozen=True)
class HalftoneProfile:
    name: str
    algorithm: str
    family: str
    period_px: float
    phase_y: float
    gamma: float
    density: float
    bias: float
    thresholds: Tuple[float, ...]
    photometric_interpretation: int
    black_is_zero: bool
    fit_note: str


LBY_LIKE_PROFILE = HalftoneProfile(
    name="LBY_row_threshold_v1",
    algorithm="LBY-like 18 px horizontal row-threshold screen",
    family="ROW_THRESHOLD",
    period_px=LBY_LINE_PERIOD_PX,
    phase_y=LBY_LINE_PHASE_Y,
    gamma=LBY_LINE_GAMMA,
    density=LBY_LINE_DENSITY,
    bias=LBY_LINE_BIAS,
    thresholds=LBY_LINE_THRESHOLDS,
    photometric_interpretation=1,
    black_is_zero=LBY_LIKE_BLACK_IS_ZERO,
    fit_note=LBY_LIKE_FIT_NOTE,
)


def halftone_profile_to_manifest(profile: HalftoneProfile) -> dict:
    return {
        "profile_name": profile.name,
        "algorithm": profile.algorithm,
        "family": profile.family,
        "screen_period_px": profile.period_px,
        "screen_gamma": profile.gamma,
        "screen_density": profile.density,
        "screen_bias": profile.bias,
        "screen_phase_y": profile.phase_y,
        "screen_thresholds": list(profile.thresholds),
        "photometric_interpretation": profile.photometric_interpretation,
        "black_is_zero": profile.black_is_zero,
        "fit_note": profile.fit_note,
    }


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
    line_period_px: float = LBY_LINE_PERIOD_PX
    line_phase_y: float = LBY_LINE_PHASE_Y
    line_density: float = LBY_LINE_DENSITY
    tone_bias: float = 0.0
    threshold_offset: float = 0.0
    stochastic_seed: int = 0


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
    write_interlaced_tiff: bool = True
    write_film_tiff: bool = True
    write_halftone_variants: bool = False
    source_format: str = "JPG"


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
    variant_film_tiffs: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TiffImageInfo:
    path: str
    width: int
    height: int
    bits_per_sample: Tuple[int, ...]
    samples_per_pixel: int
    compression: int
    photometric_interpretation: int
    rows_per_strip: int
    strip_offset: int
    strip_byte_count: int
    dpi_x: Optional[float]
    dpi_y: Optional[float]
    is_bigtiff: bool
    image_offset: int
    row_bytes: int


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
    ppi: float = 0.0,
) -> int:
    if num_views <= 0:
        raise ValueError("num_views must be greater than 0")
    if pe <= 0:
        raise ValueError("PE must be greater than 0")
    period_px = float(ppi) / float(pe) if ppi and ppi > 1.0 else float(pe)
    if period_px <= 0:
        raise ValueError("Interlace period must be greater than 0")
    d_value = float(x) + float(y) * math.tan(angle_radians) + float(offset)
    a_value = d_value % period_px
    view = int(math.floor(a_value / (period_px / num_views))) % num_views
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


def read_jpeg_info(path: str) -> Tuple[int, int]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 4 or data[:2] != b"\xFF\xD8":
        raise DeliveryError(f"Not a JPEG file: {path}")
    cursor = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while cursor + 4 <= len(data):
        while cursor < len(data) and data[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(data):
            break
        marker = data[cursor]
        cursor += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(data):
            break
        length = struct.unpack(">H", data[cursor:cursor + 2])[0]
        if length < 2 or cursor + length > len(data):
            break
        if marker in sof_markers:
            if length < 7:
                raise DeliveryError(f"Invalid JPEG SOF segment: {path}")
            height = struct.unpack(">H", data[cursor + 3:cursor + 5])[0]
            width = struct.unpack(">H", data[cursor + 5:cursor + 7])[0]
            return width, height
        cursor += length
    raise DeliveryError(f"JPEG missing SOF segment: {path}")


def read_jpeg_rgb(path: str) -> PngImage:
    width, height = read_jpeg_info(path)
    dll = _load_native_dll()
    if dll is None or not hasattr(dll, "lf_decode_image_rgb"):
        raise DeliveryError("Native JPEG decoder is not available")
    buffer = (_ctypes_array_type(width * height * 3))()
    result = dll.lf_decode_image_rgb(os.path.abspath(path), buffer, int(width), int(height))
    if result != 0:
        raise DeliveryError(f"Native JPEG decode failed with code {result}: {path}")
    return PngImage(width, height, bytes(buffer))


def _ctypes_array_type(length: int):
    return ctypes.c_uint8 * int(length)


def read_source_rgb(path: str) -> PngImage:
    lower = path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return read_jpeg_rgb(path)
    return read_png_rgb(path)


def _read_exact(file: BinaryIO, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise DeliveryError("TIFF data is truncated")
    return data


def _parse_tiff_ifd(path: str) -> tuple[bool, dict[int, tuple[int, int, int]], bytes]:
    with open(path, "rb") as f:
        header = _read_exact(f, 16)
        if header[:2] != b"II":
            raise DeliveryError(f"Only little-endian TIFF is supported: {path}")
        magic = struct.unpack_from("<H", header, 2)[0]
        if magic == 42:
            ifd_offset = struct.unpack_from("<I", header, 4)[0]
            f.seek(ifd_offset)
            count = struct.unpack("<H", _read_exact(f, 2))[0]
            entries = {}
            for _ in range(count):
                tag, field_type, value_count, value = struct.unpack("<HHII", _read_exact(f, 12))
                entries[tag] = (field_type, value_count, value)
            return False, entries, header
        if magic == 43:
            bytesize, zero = struct.unpack_from("<HH", header, 4)
            if bytesize != 8 or zero != 0:
                raise DeliveryError(f"Unsupported BigTIFF header: {path}")
            ifd_offset = struct.unpack_from("<Q", header, 8)[0]
            f.seek(ifd_offset)
            count = struct.unpack("<Q", _read_exact(f, 8))[0]
            entries = {}
            for _ in range(count):
                tag, field_type, value_count, value = struct.unpack("<HHQQ", _read_exact(f, 20))
                entries[tag] = (field_type, int(value_count), int(value))
            return True, entries, header
    raise DeliveryError(f"Unsupported TIFF magic {magic}: {path}")


def _tag_required(tags: dict[int, tuple[int, int, int]], tag: int, path: str) -> tuple[int, int, int]:
    if tag not in tags:
        raise DeliveryError(f"TIFF missing required tag {tag}: {path}")
    return tags[tag]


def _tiff_short_values(path: str, tags: dict[int, tuple[int, int, int]], tag: int) -> Tuple[int, ...]:
    field_type, count, value = _tag_required(tags, tag, path)
    if field_type != 3:
        raise DeliveryError(f"TIFF tag {tag} must be SHORT: {path}")
    if count == 1:
        return (value & 0xFFFF,)
    byte_count = count * 2
    with open(path, "rb") as f:
        f.seek(value)
        return struct.unpack("<" + "H" * count, _read_exact(f, byte_count))


def _tiff_single_int(path: str, tags: dict[int, tuple[int, int, int]], tag: int) -> int:
    field_type, count, value = _tag_required(tags, tag, path)
    if count != 1:
        raise DeliveryError(f"TIFF tag {tag} must contain one value: {path}")
    if field_type == 3:
        return value & 0xFFFF
    if field_type in {4, 16}:
        return int(value)
    raise DeliveryError(f"Unsupported TIFF integer tag type {field_type} for tag {tag}: {path}")


def _tiff_rational(path: str, tags: dict[int, tuple[int, int, int]], tag: int) -> Optional[float]:
    entry = tags.get(tag)
    if entry is None:
        return None
    field_type, count, value = entry
    if field_type != 5 or count != 1:
        return None
    with open(path, "rb") as f:
        f.seek(value)
        numerator, denominator = struct.unpack("<II", _read_exact(f, 8))
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def read_tiff_image_info(path: str) -> TiffImageInfo:
    is_bigtiff, tags, _header = _parse_tiff_ifd(path)
    width = _tiff_single_int(path, tags, 256)
    height = _tiff_single_int(path, tags, 257)
    bits_per_sample = _tiff_short_values(path, tags, 258)
    compression = _tiff_single_int(path, tags, 259)
    photometric = _tiff_single_int(path, tags, 262)
    strip_offset = _tiff_single_int(path, tags, 273)
    samples_per_pixel = _tiff_single_int(path, tags, 277)
    rows_per_strip = _tiff_single_int(path, tags, 278) if 278 in tags else height
    strip_byte_count = _tiff_single_int(path, tags, 279)
    if compression != 1:
        raise DeliveryError(f"Only uncompressed TIFF is supported: {path}")
    if rows_per_strip != height:
        raise DeliveryError(f"Only one-strip TIFF is supported: {path}")
    if samples_per_pixel == 3 and bits_per_sample == (8, 8, 8):
        row_bytes = width * 3
    elif samples_per_pixel == 1 and bits_per_sample == (1,):
        row_bytes = (width + 7) // 8
    else:
        raise DeliveryError(f"Unsupported TIFF pixel layout: {path}")
    expected_bytes = row_bytes * height
    if strip_byte_count != expected_bytes:
        raise DeliveryError(f"TIFF byte count does not match dimensions: {path}")
    return TiffImageInfo(
        path=path,
        width=width,
        height=height,
        bits_per_sample=bits_per_sample,
        samples_per_pixel=samples_per_pixel,
        compression=compression,
        photometric_interpretation=photometric,
        rows_per_strip=rows_per_strip,
        strip_offset=strip_offset,
        strip_byte_count=strip_byte_count,
        dpi_x=_tiff_rational(path, tags, 282),
        dpi_y=_tiff_rational(path, tags, 283),
        is_bigtiff=is_bigtiff,
        image_offset=strip_offset,
        row_bytes=row_bytes,
    )


def read_uncompressed_rgb_tiff_info(path: str) -> TiffImageInfo:
    info = read_tiff_image_info(path)
    if info.samples_per_pixel != 3 or info.bits_per_sample != (8, 8, 8):
        raise DeliveryError(f"Expected uncompressed 8-bit RGB TIFF: {path}")
    if info.photometric_interpretation != 2:
        raise DeliveryError(f"Expected RGB TIFF PhotometricInterpretation=2: {path}")
    return info


def read_uncompressed_one_bit_tiff_info(path: str) -> TiffImageInfo:
    info = read_tiff_image_info(path)
    if info.samples_per_pixel != 1 or info.bits_per_sample != (1,):
        raise DeliveryError(f"Expected uncompressed 1-bit TIFF: {path}")
    if info.photometric_interpretation != 1:
        raise DeliveryError(f"Expected BlackIsZero-compatible TIFF PhotometricInterpretation=1: {path}")
    return info


def iter_tiff_rows(info: TiffImageInfo) -> Iterable[bytes]:
    with open(info.path, "rb") as f:
        f.seek(info.image_offset)
        for _y in range(info.height):
            yield _read_exact(f, info.row_bytes)


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

        if filter_type != 0:
            for i in range(scanline_len):
                left = row[i - bpp] if i >= bpp else 0
                up = previous[i]
                upper_left = previous[i - bpp] if i >= bpp else 0
                if filter_type == 1:
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

    if color_type == 2:
        return PngImage(width, height, b"".join(rows))

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


def _bigtiff_ifd_entry(tag: int, field_type: int, count: int, value_or_offset: int) -> bytes:
    return struct.pack("<HHQQ", tag, field_type, count, value_or_offset)


def _short_value(value: int) -> int:
    return value & 0xFFFF


def numpy_available() -> bool:
    return _np is not None


def native_available() -> bool:
    return _np is not None and _load_native_dll() is not None


def native_jpeg_available() -> bool:
    dll = _load_native_dll()
    return dll is not None and hasattr(dll, "lf_decode_image_rgb")


class RgbTiffWriter:
    def __init__(self, path: str, width: int, height: int, dpi: int, force_bigtiff: bool = False):
        self.path = path
        self.width = width
        self.height = height
        self.dpi = int(dpi)
        self.force_bigtiff = force_bigtiff
        self._file = None
        self._rows_written = 0
        self.is_bigtiff = False

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
        if self.force_bigtiff or image_offset > UINT32_MAX or image_bytes > UINT32_MAX:
            return self._enter_bigtiff(software)

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

    def _enter_bigtiff(self, software: bytes):
        self.is_bigtiff = True
        entry_count = 13
        ifd_offset = 16
        ifd_size = 8 + entry_count * 20 + 8
        bits_offset = ifd_offset + ifd_size
        xres_offset = bits_offset + 6
        yres_offset = xres_offset + 8
        software_offset = yres_offset + 8
        image_offset = software_offset + len(software)
        image_bytes = self.width * self.height * 3

        entries = [
            _bigtiff_ifd_entry(256, 4, 1, self.width),
            _bigtiff_ifd_entry(257, 4, 1, self.height),
            _bigtiff_ifd_entry(258, 3, 3, bits_offset),
            _bigtiff_ifd_entry(259, 3, 1, _short_value(1)),
            _bigtiff_ifd_entry(262, 3, 1, _short_value(2)),
            _bigtiff_ifd_entry(273, 16, 1, image_offset),
            _bigtiff_ifd_entry(277, 3, 1, _short_value(3)),
            _bigtiff_ifd_entry(278, 4, 1, self.height),
            _bigtiff_ifd_entry(279, 16, 1, image_bytes),
            _bigtiff_ifd_entry(282, 5, 1, xres_offset),
            _bigtiff_ifd_entry(283, 5, 1, yres_offset),
            _bigtiff_ifd_entry(296, 3, 1, _short_value(2)),
            _bigtiff_ifd_entry(305, 2, len(software), software_offset),
        ]
        entries.sort(key=lambda item: struct.unpack("<H", item[:2])[0])

        f = self._file
        f.write(b"II")
        f.write(struct.pack("<H", 43))
        f.write(struct.pack("<HH", 8, 0))
        f.write(struct.pack("<Q", ifd_offset))
        f.write(struct.pack("<Q", entry_count))
        for entry in entries:
            f.write(entry)
        f.write(struct.pack("<Q", 0))
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

    def write_rows(self, rows, row_count: int) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if row_count < 0:
            raise ValueError("TIFF row count cannot be negative")
        view = memoryview(rows)
        if view.nbytes != self.width * 3 * row_count:
            raise ValueError("TIFF RGB batch length does not match image width")
        self._file.write(view)
        self._rows_written += row_count

    def __exit__(self, exc_type, exc, tb):
        if self._file is not None:
            self._file.close()
            self._file = None
        if exc_type is None and self._rows_written != self.height:
            raise ValueError("TIFF row count does not match image height")
        return False


class OneBitTiffWriter:
    def __init__(self, path: str, width: int, height: int, dpi: int, force_bigtiff: bool = False):
        self.path = path
        self.width = width
        self.height = height
        self.dpi = int(dpi)
        self.force_bigtiff = force_bigtiff
        self.row_bytes = (width + 7) // 8
        self._file = None
        self._rows_written = 0
        self.is_bigtiff = False

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
        if self.force_bigtiff or image_offset > UINT32_MAX or image_bytes > UINT32_MAX:
            return self._enter_bigtiff(software)

        entries = [
            _ifd_entry(256, 4, 1, self.width),
            _ifd_entry(257, 4, 1, self.height),
            _ifd_entry(258, 3, 1, _short_value(1)),
            _ifd_entry(259, 3, 1, _short_value(1)),
            _ifd_entry(262, 3, 1, _short_value(1)),
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

    def _enter_bigtiff(self, software: bytes):
        self.is_bigtiff = True
        entry_count = 13
        ifd_offset = 16
        ifd_size = 8 + entry_count * 20 + 8
        xres_offset = ifd_offset + ifd_size
        yres_offset = xres_offset + 8
        software_offset = yres_offset + 8
        image_offset = software_offset + len(software)
        image_bytes = self.row_bytes * self.height

        entries = [
            _bigtiff_ifd_entry(256, 4, 1, self.width),
            _bigtiff_ifd_entry(257, 4, 1, self.height),
            _bigtiff_ifd_entry(258, 3, 1, _short_value(1)),
            _bigtiff_ifd_entry(259, 3, 1, _short_value(1)),
            _bigtiff_ifd_entry(262, 3, 1, _short_value(1)),
            _bigtiff_ifd_entry(273, 16, 1, image_offset),
            _bigtiff_ifd_entry(277, 3, 1, _short_value(1)),
            _bigtiff_ifd_entry(278, 4, 1, self.height),
            _bigtiff_ifd_entry(279, 16, 1, image_bytes),
            _bigtiff_ifd_entry(282, 5, 1, xres_offset),
            _bigtiff_ifd_entry(283, 5, 1, yres_offset),
            _bigtiff_ifd_entry(296, 3, 1, _short_value(2)),
            _bigtiff_ifd_entry(305, 2, len(software), software_offset),
        ]
        entries.sort(key=lambda item: struct.unpack("<H", item[:2])[0])

        f = self._file
        f.write(b"II")
        f.write(struct.pack("<H", 43))
        f.write(struct.pack("<HH", 8, 0))
        f.write(struct.pack("<Q", ifd_offset))
        f.write(struct.pack("<Q", entry_count))
        for entry in entries:
            f.write(entry)
        f.write(struct.pack("<Q", 0))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(struct.pack("<II", self.dpi, 1))
        f.write(software)
        return self

    def write_black_row(self, black_row: Sequence[bool]) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if len(black_row) != self.width:
            raise ValueError("1-bit TIFF row length does not match image width")
        if _np is not None and hasattr(black_row, "dtype"):
            black_bits = black_row.astype(_np.uint8)
            packed = _np.packbits(1 - black_bits, bitorder="big").tobytes()
            self._file.write(packed)
            self._rows_written += 1
            return
        packed = bytearray()
        byte = 0
        bit_count = 0
        for is_black in black_row:
            byte = (byte << 1) | (0 if is_black else 1)
            bit_count += 1
            if bit_count == 8:
                packed.append(byte)
                byte = 0
                bit_count = 0
        if bit_count:
            packed.append(byte << (8 - bit_count))
        self._file.write(packed)
        self._rows_written += 1

    def write_packed_row(self, packed_row: bytes) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if len(packed_row) != self.row_bytes:
            raise ValueError("Packed 1-bit TIFF row length does not match image width")
        self._file.write(packed_row)
        self._rows_written += 1

    def write_packed_rows(self, packed_rows, row_count: int) -> None:
        if self._file is None:
            raise RuntimeError("TIFF writer is not open")
        if row_count < 0:
            raise ValueError("1-bit TIFF row count cannot be negative")
        view = memoryview(packed_rows)
        if view.nbytes != self.row_bytes * row_count:
            raise ValueError("Packed 1-bit TIFF batch length does not match image width")
        self._file.write(view)
        self._rows_written += row_count

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


def _hash_u32(value: int) -> int:
    value &= 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value & 0xFFFFFFFF


def halftone_variant_to_manifest(variant: HalftonePrintVariant) -> dict:
    return {
        "name": variant.name,
        "filename": variant.filename,
        "algorithm": variant.algorithm,
        "family": variant.family,
        "gamma": variant.gamma,
        "density": variant.density,
        "bias": variant.bias,
        "threshold_offset": variant.threshold_offset,
        "seed": variant.seed,
        "description": variant.description,
        "photometric_interpretation": 1,
        "black_is_zero": True,
    }


def halftone_variant_output_paths(film_tiff: str) -> Tuple[str, ...]:
    directory = os.path.dirname(os.path.abspath(film_tiff))
    return tuple(os.path.join(directory, variant.filename) for variant in HALFTONE_PRINT_VARIANTS)


def _variant_settings(variant: HalftonePrintVariant) -> HalftoneSettings:
    return HalftoneSettings(
        method=variant.family,
        gamma=variant.gamma,
        line_density=variant.density,
        tone_bias=variant.bias,
        threshold_offset=variant.threshold_offset,
        stochastic_seed=variant.seed,
    )


def _stochastic_threshold(value: int) -> float:
    return ((_hash_u32(value) & 0x00FFFFFF) + 0.5) / float(1 << 24)


def _pack_black_batch(black):
    return _np.packbits(1 - black.astype(_np.uint8), axis=1, bitorder="big")


def _rip_fm_batch(rgb_batch, y_start: int, variant: HalftonePrintVariant):
    if _np is None:
        raise DeliveryError("RIP_FM batch generation requires NumPy")
    rows, width, _channels = rgb_batch.shape
    rgb = rgb_batch.astype(_np.float32, copy=False)
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    darkness = 1.0 - _np.clip(luma / 255.0, 0.0, 1.0)
    tone = _np.clip(
        float(variant.density) * _np.power(darkness, max(1.0e-6, float(variant.gamma))) + float(variant.bias),
        0.0,
        1.0,
    )
    x = _np.arange(width, dtype=_np.uint32)[None, :]
    y = (_np.arange(y_start, y_start + rows, dtype=_np.uint32)[:, None] * _np.uint32(0x9E3779B1))
    seed = _np.uint32((int(variant.seed) * 0x85EBCA6B) & 0xFFFFFFFF)
    values = x ^ y ^ seed
    values ^= values >> _np.uint32(16)
    values *= _np.uint32(0x7FEB352D)
    values ^= values >> _np.uint32(15)
    values *= _np.uint32(0x846CA68B)
    values ^= values >> _np.uint32(16)
    thresholds = ((values & _np.uint32(0x00FFFFFF)).astype(_np.float32) + 0.5) / float(1 << 24)
    return _pack_black_batch(tone >= thresholds)


def _lby_tuned_batch(rgb_batch, y_start: int, variant: HalftonePrintVariant):
    if _np is None:
        raise DeliveryError("LBY_TUNED batch generation requires NumPy")
    rows, _width, _channels = rgb_batch.shape
    rgb = rgb_batch.astype(_np.float32, copy=False)
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    darkness = 1.0 - _np.clip(luma / 255.0, 0.0, 1.0)
    adjusted = _np.clip(
        float(variant.density) * _np.power(darkness, max(1.0e-6, float(variant.gamma))) + float(variant.bias),
        0.0,
        1.0,
    )
    phase_rows = _np.arange(y_start, y_start + rows, dtype=_np.float64)
    period = max(1.0, float(LBY_LIKE_PROFILE.period_px))
    phases = _np.floor((phase_rows + float(LBY_LIKE_PROFILE.phase_y)) % period).astype(_np.int64)
    thresholds = _np.asarray(LBY_LIKE_PROFILE.thresholds, dtype=_np.float32)
    row_thresholds = thresholds[_np.mod(phases, len(LBY_LIKE_PROFILE.thresholds))][:, None]
    tuned_thresholds = _np.clip(row_thresholds - float(variant.threshold_offset), 0.0, 1.0)
    return _pack_black_batch(adjusted >= tuned_thresholds)


def _halftone_variant_batch(rgb_batch, y_start: int, variant: HalftonePrintVariant):
    family = (variant.family or "RIP_FM").upper()
    if family == "LBY_TUNED":
        return _lby_tuned_batch(rgb_batch, y_start, variant)
    if family == "RIP_FM":
        return _rip_fm_batch(rgb_batch, y_start, variant)
    raise DeliveryError(f"Unsupported halftone variant family: {variant.family}")


class StreamingHalftoner:
    def __init__(
        self,
        width: int,
        settings: HalftoneSettings,
        dpi: int,
        profile: HalftoneProfile = LBY_LIKE_PROFILE,
    ):
        self.width = width
        self.settings = settings
        self.dpi = int(dpi)
        self.profile = profile
        self._next_errors = [0.0 for _ in range(width)]
        self._method = (settings.method or "FM").upper()
        self._angle = math.radians(settings.angle_degrees)
        self._cos = math.cos(self._angle)
        self._sin = math.sin(self._angle)
        self._cell = max(2.0, float(self.dpi) / max(1.0, float(settings.lpi)))
        self._x_np = _np.arange(width, dtype=_np.float64) if _np is not None else None
        self._x_u32_np = _np.arange(width, dtype=_np.uint32) if _np is not None else None

    def process_rgb_row(self, y: int, row: bytes) -> List[bool]:
        if len(row) != self.width * 3:
            raise ValueError("Halftone row length does not match image width")
        if self._method == "LBY":
            if _np is not None:
                return self._process_lby_row_numpy(y, row)
            return self._process_lby_row(y, row)
        if self._method == "LBY_TUNED":
            if _np is not None:
                return self._process_lby_tuned_row_numpy(y, row)
            return self._process_lby_tuned_row(y, row)
        if self._method == "AM":
            if _np is not None:
                return self._process_am_row_numpy(y, row)
            return self._process_am_row(y, row)
        if self._method == "RIP_FM":
            if _np is not None:
                return self._process_rip_fm_row_numpy(y, row)
            return self._process_rip_fm_row(y, row)
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

    def _process_lby_row(self, y: int, row: bytes) -> List[bool]:
        profile = self.profile
        period = max(1.0, float(self.settings.line_period_px or profile.period_px))
        phase_y = float(self.settings.line_phase_y)
        gamma = max(1.0e-6, float(self.settings.gamma or profile.gamma))
        density = float(self.settings.line_density or profile.density)
        bias = float(profile.bias)
        thresholds = profile.thresholds
        black = [False for _ in range(self.width)]
        screen_phase = int(math.floor((float(y) + phase_y) % period)) % len(thresholds)
        threshold = float(thresholds[screen_phase])
        for x in range(self.width):
            offset = x * 3
            r = row[offset]
            g = row[offset + 1]
            b = row[offset + 2]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            luma_norm = max(0.0, min(1.0, luma / 255.0))
            darkness = 1.0 - luma_norm
            adjusted = density * (darkness ** gamma) + bias
            black[x] = max(0.0, min(1.0, adjusted)) >= threshold
        return black

    def _process_lby_row_numpy(self, y: int, row: bytes):
        profile = self.profile
        rgb = _np.frombuffer(row, dtype=_np.uint8).reshape(self.width, 3).astype(_np.float64)
        luma = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
        darkness = 1.0 - _np.clip(luma / 255.0, 0.0, 1.0)
        gamma = max(1.0e-6, float(self.settings.gamma or profile.gamma))
        density = float(self.settings.line_density or profile.density)
        adjusted = _np.clip(density * _np.power(darkness, gamma) + float(profile.bias), 0.0, 1.0)
        period = max(1.0, float(self.settings.line_period_px or profile.period_px))
        screen_phase = int(math.floor((float(y) + float(self.settings.line_phase_y)) % period)) % len(profile.thresholds)
        return adjusted >= float(profile.thresholds[screen_phase])

    def _process_lby_tuned_row(self, y: int, row: bytes) -> List[bool]:
        profile = self.profile
        period = max(1.0, float(self.settings.line_period_px or profile.period_px))
        screen_phase = int(math.floor((float(y) + float(self.settings.line_phase_y)) % period)) % len(profile.thresholds)
        threshold = max(0.0, min(1.0, float(profile.thresholds[screen_phase]) - float(self.settings.threshold_offset)))
        gamma = max(1.0e-6, float(self.settings.gamma or profile.gamma))
        density = float(self.settings.line_density)
        bias = float(self.settings.tone_bias)
        black = [False for _ in range(self.width)]
        for x in range(self.width):
            offset = x * 3
            r = row[offset]
            g = row[offset + 1]
            b = row[offset + 2]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            darkness = 1.0 - max(0.0, min(1.0, luma / 255.0))
            adjusted = max(0.0, min(1.0, density * (darkness ** gamma) + bias))
            black[x] = adjusted >= threshold
        return black

    def _process_lby_tuned_row_numpy(self, y: int, row: bytes):
        profile = self.profile
        rgb = _np.frombuffer(row, dtype=_np.uint8).reshape(self.width, 3).astype(_np.float32)
        luma = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
        darkness = 1.0 - _np.clip(luma / 255.0, 0.0, 1.0)
        gamma = max(1.0e-6, float(self.settings.gamma or profile.gamma))
        adjusted = _np.clip(
            float(self.settings.line_density) * _np.power(darkness, gamma) + float(self.settings.tone_bias),
            0.0,
            1.0,
        )
        period = max(1.0, float(self.settings.line_period_px or profile.period_px))
        screen_phase = int(math.floor((float(y) + float(self.settings.line_phase_y)) % period)) % len(profile.thresholds)
        threshold = max(0.0, min(1.0, float(profile.thresholds[screen_phase]) - float(self.settings.threshold_offset)))
        return adjusted >= threshold

    def _rip_fm_tone(self, darkness: float) -> float:
        return max(
            0.0,
            min(
                1.0,
                float(self.settings.line_density) * (max(0.0, min(1.0, darkness)) ** max(1.0e-6, float(self.settings.gamma)))
                + float(self.settings.tone_bias),
            ),
        )

    def _process_rip_fm_row(self, y: int, row: bytes) -> List[bool]:
        black = [False for _ in range(self.width)]
        seed = int(self.settings.stochastic_seed)
        for x in range(self.width):
            offset = x * 3
            r = row[offset]
            g = row[offset + 1]
            b = row[offset + 2]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            darkness = 1.0 - max(0.0, min(1.0, luma / 255.0))
            value = x ^ (y * 0x9E3779B1) ^ (seed * 0x85EBCA6B)
            black[x] = self._rip_fm_tone(darkness) >= _stochastic_threshold(value)
        return black

    def _process_rip_fm_row_numpy(self, y: int, row: bytes):
        rgb = _np.frombuffer(row, dtype=_np.uint8).reshape(self.width, 3).astype(_np.float32)
        luma = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
        darkness = 1.0 - _np.clip(luma / 255.0, 0.0, 1.0)
        tone = _np.clip(
            float(self.settings.line_density) * _np.power(darkness, max(1.0e-6, float(self.settings.gamma)))
            + float(self.settings.tone_bias),
            0.0,
            1.0,
        )
        values = (
            self._x_u32_np
            ^ _np.uint32((int(y) * 0x9E3779B1) & 0xFFFFFFFF)
            ^ _np.uint32((int(self.settings.stochastic_seed) * 0x85EBCA6B) & 0xFFFFFFFF)
        )
        values ^= values >> _np.uint32(16)
        values *= _np.uint32(0x7FEB352D)
        values ^= values >> _np.uint32(15)
        values *= _np.uint32(0x846CA68B)
        values ^= values >> _np.uint32(16)
        thresholds = ((values & _np.uint32(0x00FFFFFF)).astype(_np.float32) + 0.5) / float(1 << 24)
        return tone >= thresholds

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

    def _process_am_row_numpy(self, y: int, row: bytes):
        shape = (self.settings.dot_shape or "ROUND").upper()
        rgb = _np.frombuffer(row, dtype=_np.uint8).reshape(self.width, 3).astype(_np.float32)
        luma = 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]
        inv_gamma = 1.0 / max(float(self.settings.gamma), 1e-6)
        luma_norm = _np.power(_np.clip(luma, 0.0, 255.0) / 255.0, inv_gamma)
        darkness = 1.0 - _np.clip(luma_norm, 0.0, 1.0)

        x = self._x_np
        xr = x * self._cos + float(y) * self._sin
        yr = -x * self._sin + float(y) * self._cos
        u = ((xr / self._cell) - _np.floor(xr / self._cell)) * 2.0 - 1.0
        v = ((yr / self._cell) - _np.floor(yr / self._cell)) * 2.0 - 1.0

        if shape == "DIAMOND":
            metric = (_np.abs(u) + _np.abs(v)) / 2.0
            threshold = darkness
        elif shape == "ELLIPSE":
            metric = _np.sqrt(u * u + (v / 0.65) * (v / 0.65))
            threshold = _np.sqrt(darkness)
        else:
            metric = _np.sqrt(u * u + v * v)
            threshold = _np.sqrt(darkness)
        return (darkness >= 1.0) | ((darkness > 0.0) & (metric <= threshold))


class NumpyPngSource:
    def __init__(self, image: PngImage):
        self.width = image.width
        self.height = image.height
        self.array = _np.frombuffer(image.data, dtype=_np.uint8).reshape(image.height, image.width, 3)


class InterlaceRenderer:
    def __init__(
        self,
        source_paths: Sequence[str],
        settings: DeliverySettings,
        width_px: int,
        height_px: int,
        progress_callback: Optional[ProgressCallback] = None,
        stop_callback: Optional[StopCallback] = None,
    ):
        if len(source_paths) != settings.camera_count:
            raise DeliveryError("Source view count does not match camera count")
        self.sources = []
        for index, path in enumerate(source_paths, start=1):
            _check_stop(stop_callback)
            _emit_progress(progress_callback, "加载源视角图", index, len(source_paths), os.path.basename(path))
            self.sources.append(read_source_rgb(path))
        self.settings = settings
        self.width_px = width_px
        self.height_px = height_px
        self.view_order = build_view_order(settings.camera_count, settings.interlace.reverse_views)
        self.angle_radians = math.radians(settings.interlace.angle_degrees)
        self._use_numpy = _np is not None and bool(self.sources)
        self._np_sources = []
        self._view_order_np = None
        self._x_final_np = None
        self._source_x_cache = {}
        self._same_source_dimensions = False
        if self._use_numpy:
            self._np_sources = [NumpyPngSource(source) for source in self.sources]
            self._view_order_np = _np.array(self.view_order, dtype=_np.int16)
            self._x_final_np = _np.arange(self.width_px, dtype=_np.float64)
            first = self._np_sources[0]
            self._same_source_dimensions = all(
                source.width == first.width and source.height == first.height
                for source in self._np_sources
            )

    def _sample_channel(self, final_x: int, final_y: int, channel: int) -> int:
        view = interlace_view_index(
            final_x,
            final_y,
            channel,
            self.settings.camera_count,
            self.settings.interlace.pe,
            self.angle_radians,
            self.settings.interlace.offset,
            self.settings.ppi,
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
        if self._use_numpy:
            return self._generate_final_row_numpy(y)
        row = bytearray(self.width_px * 3)
        for x in range(self.width_px):
            target = x * 3
            view = interlace_view_index(
                x,
                y,
                0,
                self.settings.camera_count,
                self.settings.interlace.pe,
                self.angle_radians,
                self.settings.interlace.offset,
                self.settings.ppi,
            )
            source = self.sources[self.view_order[view]]
            if self.width_px <= 1:
                source_x = 0.0
            else:
                source_x = x * (source.width - 1) / float(self.width_px - 1)
            if self.height_px <= 1:
                source_y = 0.0
            else:
                source_y = y * (source.height - 1) / float(self.height_px - 1)
            r, g, b = source.sample_bilinear(source_x, source_y)
            row[target] = r
            row[target + 1] = g
            row[target + 2] = b
        return bytes(row)

    def _source_x_arrays(self, source: NumpyPngSource):
        key = source.width
        cached = self._source_x_cache.get(key)
        if cached is not None:
            return cached
        if self.width_px <= 1:
            x_float = _np.zeros(self.width_px, dtype=_np.float32)
        else:
            x_float = self._x_final_np * ((source.width - 1) / float(self.width_px - 1))
        x0 = _np.floor(x_float).astype(_np.int64)
        x1 = _np.minimum(source.width - 1, x0 + 1)
        tx = (x_float - x0).astype(_np.float32)
        cached = (x0, x1, tx)
        self._source_x_cache[key] = cached
        return cached

    def _source_y_values(self, source: NumpyPngSource, final_y: int):
        if self.height_px <= 1:
            y_float = 0.0
        else:
            y_float = final_y * (source.height - 1) / float(self.height_px - 1)
        y0 = int(math.floor(y_float))
        y1 = min(source.height - 1, y0 + 1)
        ty = float(y_float - y0)
        return y0, y1, ty

    def _view_indices_numpy(self, y: int, channel: int):
        period_px = float(self.settings.ppi) / float(self.settings.interlace.pe)
        d_value = (
            self._x_final_np
            + float(y) * math.tan(self.angle_radians)
            + float(self.settings.interlace.offset)
        )
        a_value = _np.mod(d_value, period_px)
        view = _np.floor(a_value / (period_px / self.settings.camera_count)).astype(_np.int16)
        return self._view_order_np[_np.mod(view, self.settings.camera_count)]

    def _generate_final_row_numpy(self, y: int) -> bytes:
        if self._same_source_dimensions:
            return self._generate_final_row_numpy_common_sources(y)
        row = _np.empty((self.width_px, 3), dtype=_np.uint8)
        source_indices = self._view_indices_numpy(y, 0)
        for channel in range(3):
            output_channel = row[:, channel]
            for source_index in _np.unique(source_indices):
                mask = source_indices == source_index
                if not bool(mask.any()):
                    continue
                source = self._np_sources[int(source_index)]
                x0, x1, tx = self._source_x_arrays(source)
                y0, y1, ty = self._source_y_values(source, y)
                idx = _np.nonzero(mask)[0]
                tx_masked = tx[idx]
                top = (
                    source.array[y0, x0[idx], channel].astype(_np.float32) * (1.0 - tx_masked)
                    + source.array[y0, x1[idx], channel].astype(_np.float32) * tx_masked
                )
                bottom = (
                    source.array[y1, x0[idx], channel].astype(_np.float32) * (1.0 - tx_masked)
                    + source.array[y1, x1[idx], channel].astype(_np.float32) * tx_masked
                )
                values = _np.rint(top * (1.0 - ty) + bottom * ty).clip(0, 255).astype(_np.uint8)
                output_channel[idx] = values
        return row.tobytes()

    def _generate_final_row_numpy_common_sources(self, y: int) -> bytes:
        source = self._np_sources[0]
        x0, x1, tx = self._source_x_arrays(source)
        y0, y1, ty = self._source_y_values(source, y)
        row = _np.empty((self.width_px, 3), dtype=_np.uint8)

        source_indices = self._view_indices_numpy(y, 0).astype(_np.int64, copy=False)
        for channel in range(3):
            top_rows = _np.stack([src.array[y0, :, channel] for src in self._np_sources], axis=0)
            bottom_rows = _np.stack([src.array[y1, :, channel] for src in self._np_sources], axis=0)
            top = (
                top_rows[source_indices, x0].astype(_np.float32) * (1.0 - tx)
                + top_rows[source_indices, x1].astype(_np.float32) * tx
            )
            bottom = (
                bottom_rows[source_indices, x0].astype(_np.float32) * (1.0 - tx)
                + bottom_rows[source_indices, x1].astype(_np.float32) * tx
            )
            row[:, channel] = _np.rint(top * (1.0 - ty) + bottom * ty).clip(0, 255).astype(_np.uint8)
        return row.tobytes()

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
            view = interlace_view_index(
                final_x,
                final_y,
                0,
                self.settings.camera_count,
                self.settings.interlace.pe,
                self.angle_radians,
                self.settings.interlace.offset,
                self.settings.ppi,
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
            r, g, b = source.sample_bilinear(source_x, source_y)
            row[target] = r
            row[target + 1] = g
            row[target + 2] = b
        return bytes(row)


class NativeAmBatchGenerator:
    def __init__(self, renderer: InterlaceRenderer, settings: DeliverySettings):
        if _np is None:
            raise DeliveryError("Native batch generation requires NumPy")
        self.dll = _load_native_dll()
        if self.dll is None:
            raise DeliveryError("Native batch generation DLL is not available")
        if not renderer._same_source_dimensions:
            raise DeliveryError("Native batch generation requires equal source dimensions")
        self.method = (settings.halftone.method or "FM").upper()
        if settings.write_film_tiff and self.method not in {"AM", "LBY"}:
            raise DeliveryError("Native batch generation currently supports AM and LBY halftone only")

        self.settings = settings
        self.width = renderer.width_px
        self.height = renderer.height_px
        self.source_width = renderer._np_sources[0].width
        self.source_height = renderer._np_sources[0].height
        self.source_count = len(renderer._np_sources)
        self.row_bytes = (self.width + 7) // 8
        self.y_scale = 0.0 if self.height <= 1 else (self.source_height - 1) / float(self.height - 1)

        self.source_stack = _np.empty(
            (self.source_count, self.source_height, self.source_width, 3),
            dtype=_np.uint8,
        )
        for index, source in enumerate(renderer._np_sources):
            self.source_stack[index] = source.array
        renderer.sources = []
        renderer._np_sources = []
        self._preview_cache = {}

        x = _np.arange(self.width, dtype=_np.float64)
        if self.width <= 1:
            source_x = _np.zeros(self.width, dtype=_np.float64)
        else:
            source_x = x * ((self.source_width - 1) / float(self.width - 1))
        self.x0 = _np.floor(source_x).astype(_np.int32)
        self.x1 = _np.minimum(self.source_width - 1, self.x0 + 1).astype(_np.int32)
        self.tx = (source_x - self.x0).astype(_np.float32)

        view_map = _np.empty((3, self.width), dtype=_np.int16)
        period_px = float(settings.ppi) / float(settings.interlace.pe)
        d_value = x + float(settings.interlace.offset)
        a_value = _np.mod(d_value, period_px)
        view = _np.floor(a_value / (period_px / settings.camera_count)).astype(_np.int16)
        whole_pixel_map = renderer._view_order_np[_np.mod(view, settings.camera_count)]
        for channel in range(3):
            view_map[channel] = whole_pixel_map
        self.view_map = _np.ascontiguousarray(view_map)

        angle = math.radians(settings.halftone.angle_degrees)
        self.screen_cos = math.cos(angle)
        self.screen_sin = math.sin(angle)
        self.cell_size = max(2.0, float(settings.ppi) / max(1.0, float(settings.halftone.lpi)))
        shape = (settings.halftone.dot_shape or "ROUND").upper()
        self.dot_shape = {"ROUND": 0, "DIAMOND": 1, "ELLIPSE": 2}.get(shape, 0)
        self.lby_thresholds = _np.ascontiguousarray(_np.asarray(LBY_LIKE_PROFILE.thresholds, dtype=_np.float64))

    @classmethod
    def can_use(cls, renderer: InterlaceRenderer, settings: DeliverySettings) -> bool:
        return (
            native_available()
            and renderer._same_source_dimensions
            and abs(float(settings.interlace.angle_degrees)) < 1.0e-9
            and (not settings.write_film_tiff or (settings.halftone.method or "FM").upper() in {"AM", "LBY"})
        )

    @staticmethod
    def _ptr(array, ctype):
        if array is None:
            return ctypes.POINTER(ctype)()
        return array.ctypes.data_as(ctypes.POINTER(ctype))

    def generate(self, y_start: int, rows: int, include_rgb: bool = True, include_bits: bool = True):
        rgb = _np.empty((rows, self.width, 3), dtype=_np.uint8) if include_rgb else None
        bits = _np.empty((rows, self.row_bytes), dtype=_np.uint8) if include_bits else None
        if self.method == "LBY":
            result = self.dll.lf_generate_lby_batch(
                self._ptr(self.source_stack, ctypes.c_uint8),
                self.source_count,
                self.source_width,
                self.source_height,
                self.width,
                self.height,
                int(y_start),
                int(rows),
                self._ptr(self.view_map, ctypes.c_int16),
                self._ptr(self.x0, ctypes.c_int32),
                self._ptr(self.x1, ctypes.c_int32),
                self._ptr(self.tx, ctypes.c_float),
                float(self.y_scale),
                float(self.settings.halftone.line_period_px or LBY_LIKE_PROFILE.period_px),
                float(self.settings.halftone.line_phase_y),
                float(self.settings.halftone.gamma or LBY_LIKE_PROFILE.gamma),
                float(self.settings.halftone.line_density or LBY_LIKE_PROFILE.density),
                float(LBY_LIKE_PROFILE.bias),
                self._ptr(self.lby_thresholds, ctypes.c_double),
                int(self.lby_thresholds.size),
                self._ptr(rgb, ctypes.c_uint8),
                self._ptr(bits, ctypes.c_uint8),
                self.row_bytes,
            )
        else:
            result = self.dll.lf_generate_am_batch(
                self._ptr(self.source_stack, ctypes.c_uint8),
                self.source_count,
                self.source_width,
                self.source_height,
                self.width,
                self.height,
                int(y_start),
                int(rows),
                self._ptr(self.view_map, ctypes.c_int16),
                self._ptr(self.x0, ctypes.c_int32),
                self._ptr(self.x1, ctypes.c_int32),
                self._ptr(self.tx, ctypes.c_float),
                float(self.y_scale),
                float(self.screen_cos),
                float(self.screen_sin),
                float(self.cell_size),
                float(self.settings.halftone.gamma),
                int(self.dot_shape),
                self._ptr(rgb, ctypes.c_uint8),
                self._ptr(bits, ctypes.c_uint8),
                self.row_bytes,
            )
        if result != 0:
            raise DeliveryError(f"Native batch generation failed with code {result}")
        return rgb, bits

    def _preview_maps(self, preview_width: int):
        cached = self._preview_cache.get(preview_width)
        if cached is not None:
            return cached

        if preview_width <= 1:
            final_x = _np.zeros(preview_width, dtype=_np.float64)
        else:
            final_x = _np.rint(
                _np.arange(preview_width, dtype=_np.float64)
                * ((self.width - 1) / float(preview_width - 1))
            )
        source_x = final_x * ((self.source_width - 1) / float(self.width - 1)) if self.width > 1 else final_x
        x0 = _np.floor(source_x).astype(_np.int64)
        x1 = _np.minimum(self.source_width - 1, x0 + 1)
        tx = (source_x - x0).astype(_np.float32)

        period_px = float(self.settings.ppi) / float(self.settings.interlace.pe)
        d_value = final_x + float(self.settings.interlace.offset)
        a_value = _np.mod(d_value, period_px)
        view = _np.floor(a_value / (period_px / self.settings.camera_count)).astype(_np.int64)
        whole_pixel_map = _np.mod(view, self.settings.camera_count)
        if self.settings.interlace.reverse_views:
            whole_pixel_map = self.settings.camera_count - 1 - whole_pixel_map
        view_map = _np.empty((3, preview_width), dtype=_np.int64)
        for channel in range(3):
            view_map[channel] = whole_pixel_map

        cached = (x0, x1, tx, view_map)
        self._preview_cache[preview_width] = cached
        return cached

    def generate_preview_row(self, preview_y: int, preview_width: int, preview_height: int) -> bytes:
        if preview_height <= 1:
            final_y = 0
        else:
            final_y = round_half_up(preview_y * (self.height - 1) / float(preview_height - 1))
        final_y = max(0, min(self.height - 1, final_y))
        source_y = final_y * self.y_scale
        y0 = int(math.floor(source_y))
        y1 = min(self.source_height - 1, y0 + 1)
        ty = float(source_y - y0)
        x0, x1, tx, view_map = self._preview_maps(preview_width)

        row = _np.empty((preview_width, 3), dtype=_np.uint8)
        for channel in range(3):
            views = view_map[channel]
            top = (
                self.source_stack[views, y0, x0, channel].astype(_np.float32) * (1.0 - tx)
                + self.source_stack[views, y0, x1, channel].astype(_np.float32) * tx
            )
            bottom = (
                self.source_stack[views, y1, x0, channel].astype(_np.float32) * (1.0 - tx)
                + self.source_stack[views, y1, x1, channel].astype(_np.float32) * tx
            )
            row[:, channel] = _np.rint(top * (1.0 - ty) + bottom * ty).clip(0, 255).astype(_np.uint8)
        return row.tobytes()


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
    if not settings.write_interlaced_tiff and not settings.write_film_tiff:
        raise DeliveryError("At least one delivery output must be enabled")
    tmp_files = [
        _tmp_path(paths.preview_png),
        _tmp_path(paths.manifest_json),
    ]
    if settings.write_film_tiff:
        tmp_files.append(_tmp_path(paths.film_1bit_tiff))
        if settings.write_halftone_variants:
            tmp_files.extend(_tmp_path(path) for path in halftone_variant_output_paths(paths.film_1bit_tiff))
    else:
        _remove_if_exists(paths.film_1bit_tiff)
        _remove_if_exists(_tmp_path(paths.film_1bit_tiff))
        for path in halftone_variant_output_paths(paths.film_1bit_tiff):
            _remove_if_exists(path)
            _remove_if_exists(_tmp_path(path))
    if settings.write_interlaced_tiff:
        tmp_files.append(_tmp_path(paths.interlaced_tiff))
    else:
        _remove_if_exists(paths.interlaced_tiff)
        _remove_if_exists(_tmp_path(paths.interlaced_tiff))
    for tmp in tmp_files:
        _remove_if_exists(tmp)

    try:
        _emit_progress(progress_callback, "加载源视角", 0, settings.camera_count)
        renderer = InterlaceRenderer(
            source_paths,
            settings,
            width_px,
            height_px,
            progress_callback=progress_callback,
            stop_callback=stop_callback,
        )
        halftoner = StreamingHalftoner(width_px, settings.halftone, settings.ppi) if settings.write_film_tiff else None
        if settings.write_interlaced_tiff and settings.write_film_tiff:
            stage = "生成交织 TIFF 和 1-bit TIFF"
        elif settings.write_interlaced_tiff:
            stage = "生成交织 TIFF"
        else:
            stage = "生成 1-bit TIFF"

        _emit_progress(progress_callback, stage, 0, height_px)
        native_used = False
        native_generator = None
        variant_paths = halftone_variant_output_paths(paths.film_1bit_tiff) if settings.write_film_tiff and settings.write_halftone_variants else ()
        if NativeAmBatchGenerator.can_use(renderer, settings):
            native_generator = NativeAmBatchGenerator(renderer, settings)
            batch_rows = 256 if variant_paths else 1024
            with ExitStack() as stack:
                rgb_writer = None
                if settings.write_interlaced_tiff:
                    rgb_writer = stack.enter_context(
                        RgbTiffWriter(_tmp_path(paths.interlaced_tiff), width_px, height_px, settings.ppi)
                    )
                bit_writer = None
                if settings.write_film_tiff:
                    bit_writer = stack.enter_context(
                        OneBitTiffWriter(_tmp_path(paths.film_1bit_tiff), width_px, height_px, settings.ppi)
                    )
                variant_writers = []
                for variant, variant_path in zip(HALFTONE_PRINT_VARIANTS, variant_paths):
                    writer = stack.enter_context(OneBitTiffWriter(_tmp_path(variant_path), width_px, height_px, settings.ppi))
                    variant_writers.append((variant, variant_path, writer))
                last_progress_time = 0.0
                for y in range(0, height_px, batch_rows):
                    _check_stop(stop_callback)
                    rows = min(batch_rows, height_px - y)
                    rgb_batch, bit_batch = native_generator.generate(
                        y,
                        rows,
                        include_rgb=settings.write_interlaced_tiff or bool(variant_writers),
                        include_bits=settings.write_film_tiff,
                    )
                    if rgb_writer is not None:
                        rgb_writer.write_rows(rgb_batch, rows)
                    if bit_writer is not None:
                        if native_generator.method == "AM":
                            bit_writer.write_packed_rows(_np.bitwise_xor(bit_batch, 0xFF), rows)
                        else:
                            bit_writer.write_packed_rows(bit_batch, rows)
                    for variant, _variant_path, variant_writer in variant_writers:
                        variant_writer.write_packed_rows(_halftone_variant_batch(rgb_batch, y, variant), rows)
                    now = time.perf_counter()
                    if y == 0 or y + rows >= height_px or now - last_progress_time >= 0.5:
                        last_progress_time = now
                        _emit_progress(
                            progress_callback,
                            stage,
                            y + rows,
                            height_px,
                            f"{y + rows}/{height_px} | Native {native_generator.method}"
                            + (f" + {len(variant_writers)} LBY调参候选" if variant_writers else ""),
                        )
            native_used = True
        if not native_used:
            with ExitStack() as stack:
                rgb_writer = None
                if settings.write_interlaced_tiff:
                    rgb_writer = stack.enter_context(
                        RgbTiffWriter(_tmp_path(paths.interlaced_tiff), width_px, height_px, settings.ppi)
                    )
                bit_writer = None
                if settings.write_film_tiff:
                    bit_writer = stack.enter_context(
                        OneBitTiffWriter(_tmp_path(paths.film_1bit_tiff), width_px, height_px, settings.ppi)
                    )
                variant_halftoners = []
                for variant, variant_path in zip(HALFTONE_PRINT_VARIANTS, variant_paths):
                    writer = stack.enter_context(OneBitTiffWriter(_tmp_path(variant_path), width_px, height_px, settings.ppi))
                    variant_halftoners.append(
                        (variant, variant_path, writer, StreamingHalftoner(width_px, _variant_settings(variant), settings.ppi))
                    )
                progress_step = max(1, height_px // 1000)
                last_progress_time = 0.0
                for y in range(height_px):
                    _check_stop(stop_callback)
                    row = renderer.generate_final_row(y)
                    if rgb_writer is not None:
                        rgb_writer.write_row(row)
                    if bit_writer is not None:
                        bit_writer.write_black_row(halftoner.process_rgb_row(y, row))
                    for _variant, _variant_path, variant_writer, variant_halftoner in variant_halftoners:
                        variant_writer.write_black_row(variant_halftoner.process_rgb_row(y, row))
                    now = time.perf_counter()
                    if y % progress_step == 0 or y == height_px - 1 or now - last_progress_time >= 0.5:
                        last_progress_time = now
                        _emit_progress(
                            progress_callback,
                            stage,
                            y + 1,
                            height_px,
                            f"{y + 1}/{height_px} | Python {(settings.halftone.method or 'FM').upper() if settings.write_film_tiff else 'RGB'}"
                            + (f" + {len(variant_halftoners)} LBY调参候选" if variant_halftoners else ""),
                        )
        preview_width, preview_height = preview_dimensions(width_px, height_px, settings.preview_max_edge)
        _emit_progress(progress_callback, "生成 PNG 预览", 0, preview_height)

        def preview_rows():
            progress_step = max(1, preview_height // 100)
            for y in range(preview_height):
                _check_stop(stop_callback)
                if y % progress_step == 0 or y == preview_height - 1:
                    _emit_progress(progress_callback, "生成 PNG 预览", y + 1, preview_height)
                if native_generator is not None:
                    yield native_generator.generate_preview_row(y, preview_width, preview_height)
                else:
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
            variant_film_tiffs=tuple(variant_paths),
        )
        manifest = build_manifest(settings, result, source_paths)
        with open(_tmp_path(paths.manifest_json), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        if settings.write_interlaced_tiff:
            os.replace(_tmp_path(paths.interlaced_tiff), paths.interlaced_tiff)
        if settings.write_film_tiff:
            os.replace(_tmp_path(paths.film_1bit_tiff), paths.film_1bit_tiff)
            for variant_path in variant_paths:
                os.replace(_tmp_path(variant_path), variant_path)
        os.replace(_tmp_path(paths.preview_png), paths.preview_png)
        os.replace(_tmp_path(paths.manifest_json), paths.manifest_json)
        _remove_if_exists(paths.error_log)
        _emit_progress(progress_callback, "完成", 1, 1, paths.output_dir)
        return result
    except Exception:
        for tmp in tmp_files:
            _remove_if_exists(tmp)
        raise


def _valid_bits_mask(width: int) -> int:
    remainder = width % 8
    if remainder == 0:
        return 0xFF
    return (0xFF << (8 - remainder)) & 0xFF


def _count_black_in_packed_row(row: bytes, width: int) -> int:
    if not row:
        return 0
    if width % 8 == 0:
        white = sum(byte.bit_count() for byte in row)
        return width - white
    valid_mask = _valid_bits_mask(width)
    white = sum(byte.bit_count() for byte in row[:-1])
    white += (row[-1] & valid_mask).bit_count()
    return width - white


def _count_mismatch_in_packed_rows(left: bytes, right: bytes, width: int) -> int:
    if len(left) != len(right):
        raise DeliveryError("1-bit TIFF row byte lengths do not match")
    if not left:
        return 0
    total = 0
    limit = len(left)
    if width % 8 != 0:
        limit -= 1
    for index in range(limit):
        total += (left[index] ^ right[index]).bit_count()
    if width % 8 != 0:
        total += ((left[-1] ^ right[-1]) & _valid_bits_mask(width)).bit_count()
    return total


def compare_one_bit_tiffs(generated_tiff: str, target_tiff: str) -> dict:
    generated = read_uncompressed_one_bit_tiff_info(generated_tiff)
    target = read_uncompressed_one_bit_tiff_info(target_tiff)
    if generated.width != target.width or generated.height != target.height:
        return {
            "same_shape": False,
            "generated_shape": [generated.height, generated.width],
            "target_shape": [target.height, target.width],
        }
    mismatch_count = 0
    generated_black = 0
    target_black = 0
    total_pixels = generated.width * generated.height
    for generated_row, target_row in zip(iter_tiff_rows(generated), iter_tiff_rows(target)):
        mismatch_count += _count_mismatch_in_packed_rows(generated_row, target_row, generated.width)
        generated_black += _count_black_in_packed_row(generated_row, generated.width)
        target_black += _count_black_in_packed_row(target_row, target.width)
    return {
        "same_shape": True,
        "generated_shape": [generated.height, generated.width],
        "target_shape": [target.height, target.width],
        "mismatch_count": mismatch_count,
        "total_pixels": total_pixels,
        "mismatch_ratio": mismatch_count / total_pixels if total_pixels else 0.0,
        "black_ratio_generated": generated_black / total_pixels if total_pixels else 0.0,
        "black_ratio_target": target_black / total_pixels if total_pixels else 0.0,
        "generated_black_pixels": generated_black,
        "target_black_pixels": target_black,
    }


def halftone_interlaced_tiff(
    interlaced_tiff: str,
    film_tiff: str,
    *,
    profile: HalftoneProfile = LBY_LIKE_PROFILE,
    ppi: Optional[int] = None,
    manifest_json: Optional[str] = None,
    target_tiff: Optional[str] = None,
    calibration_report_json: Optional[str] = None,
    write_variants: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    stop_callback: Optional[StopCallback] = None,
) -> dict:
    start_time = time.perf_counter()
    info = read_uncompressed_rgb_tiff_info(interlaced_tiff)
    output_ppi = int(ppi or info.dpi_x or 4000)
    tmp_film = _tmp_path(film_tiff)
    variant_paths = halftone_variant_output_paths(film_tiff) if write_variants else ()
    tmp_variants = [_tmp_path(path) for path in variant_paths]
    tmp_manifest = _tmp_path(manifest_json) if manifest_json else None
    tmp_report = _tmp_path(calibration_report_json) if calibration_report_json else None
    for path in (tmp_film, *tmp_variants, tmp_manifest, tmp_report):
        if path:
            _remove_if_exists(path)

    halftoner = StreamingHalftoner(
        info.width,
        HalftoneSettings(
            method="LBY",
            gamma=profile.gamma,
            line_period_px=profile.period_px,
            line_phase_y=profile.phase_y,
            line_density=profile.density,
        ),
        output_ppi,
        profile=profile,
    )
    total_black = 0
    variant_reports = []
    try:
        _emit_progress(progress_callback, "读取交织 TIFF 并挂网", 0, info.height, os.path.basename(interlaced_tiff))
        with ExitStack() as stack:
            writer = stack.enter_context(OneBitTiffWriter(tmp_film, info.width, info.height, output_ppi))
            variant_writers = []
            for variant, variant_path in zip(HALFTONE_PRINT_VARIANTS, variant_paths):
                variant_writer = stack.enter_context(
                    OneBitTiffWriter(_tmp_path(variant_path), info.width, info.height, output_ppi)
                )
                variant_writers.append(
                    (variant, variant_path, variant_writer, StreamingHalftoner(info.width, _variant_settings(variant), output_ppi))
                )
            for y, row in enumerate(iter_tiff_rows(info)):
                _check_stop(stop_callback)
                black_row = halftoner.process_rgb_row(y, row)
                if _np is not None and hasattr(black_row, "dtype"):
                    total_black += int(_np.count_nonzero(black_row))
                else:
                    total_black += sum(1 for value in black_row if value)
                writer.write_black_row(black_row)
                for _variant, _variant_path, variant_writer, variant_halftoner in variant_writers:
                    variant_writer.write_black_row(variant_halftoner.process_rgb_row(y, row))
                if y == 0 or y == info.height - 1 or (y + 1) % max(1, info.height // 100) == 0:
                    _emit_progress(progress_callback, "读取交织 TIFF 并挂网", y + 1, info.height)

        for variant, variant_path, _writer, _halftoner in variant_writers:
            variant_reports.append(
                {
                    **halftone_variant_to_manifest(variant),
                    "path": variant_path,
                }
            )

        elapsed = time.perf_counter() - start_time
        total_pixels = info.width * info.height
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "interlaced_tiff_to_profiled_1bit_tiff",
            "interlaced_tiff": {
                "path": interlaced_tiff,
                "width_px": info.width,
                "height_px": info.height,
                "dpi_x": info.dpi_x,
                "dpi_y": info.dpi_y,
                "is_bigtiff": info.is_bigtiff,
                "compression": "none",
                "photometric_interpretation": info.photometric_interpretation,
            },
            "film_tiff": {
                "path": film_tiff,
                "width_px": info.width,
                "height_px": info.height,
                "ppi": output_ppi,
                "bits_per_sample": 1,
                "photometric_interpretation": 1,
                "black_is_zero": True,
                "black_pixels": total_black,
                "black_ratio": total_black / total_pixels if total_pixels else 0.0,
            },
            "halftone_profile": halftone_profile_to_manifest(profile),
            "halftone_variants": variant_reports,
            "elapsed_seconds": elapsed,
        }
        if target_tiff:
            _emit_progress(progress_callback, "生成校准报告", 0, 1, os.path.basename(target_tiff))
            comparison = compare_one_bit_tiffs(tmp_film, target_tiff)
            report["target_tiff"] = {
                "path": target_tiff,
            }
            report["comparison"] = comparison
            _emit_progress(progress_callback, "生成校准报告", 1, 1)

        if manifest_json:
            os.makedirs(os.path.dirname(os.path.abspath(manifest_json)), exist_ok=True)
            with open(tmp_manifest, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        if calibration_report_json:
            os.makedirs(os.path.dirname(os.path.abspath(calibration_report_json)), exist_ok=True)
            with open(tmp_report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

        os.replace(tmp_film, film_tiff)
        for variant_path in variant_paths:
            os.replace(_tmp_path(variant_path), variant_path)
        if manifest_json:
            os.replace(tmp_manifest, manifest_json)
        if calibration_report_json:
            os.replace(tmp_report, calibration_report_json)
        _emit_progress(progress_callback, "完成", 1, 1, os.path.dirname(os.path.abspath(film_tiff)))
        return report
    except Exception:
        for path in (tmp_film, *tmp_variants, tmp_manifest, tmp_report):
            if path:
                _remove_if_exists(path)
        raise


def build_manifest(settings: DeliverySettings, result: DeliveryResult, source_paths: Sequence[str]) -> dict:
    if settings.source_format:
        source_ext = "jpg" if settings.source_format.upper() in {"JPG", "JPEG"} else settings.source_format.lower()
        source_files = [f"camera_{idx:03d}.{source_ext}" for idx in range(settings.camera_count)]
    else:
        source_files = [os.path.basename(path) for path in source_paths]
    halftone = {
        **asdict(settings.halftone),
        "ppi_as_tiff_dpi": settings.ppi,
    }
    if (settings.halftone.method or "").upper() == "LBY":
        halftone.update(
            {
                **halftone_profile_to_manifest(LBY_LIKE_PROFILE),
            }
        )
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
            "write_interlaced_tiff": settings.write_interlaced_tiff,
            "write_film_tiff": settings.write_film_tiff,
            "write_halftone_variants": settings.write_halftone_variants,
        },
        "source_views": {
            "camera_count": settings.camera_count,
            "source_format": settings.source_format,
            "source_width_px": settings.source_width,
            "source_height_px": settings.source_height,
            "order": "reversed" if settings.interlace.reverse_views else "ascending",
            "files": source_files,
        },
        "interlace": asdict(settings.interlace),
        "halftone": halftone,
        "warnings": {
            "large_output": result.large_output_warning,
            "source_upscale": result.source_upscale_warning,
        },
        "files": {
            "interlaced_tiff": os.path.basename(result.paths.interlaced_tiff) if settings.write_interlaced_tiff else None,
            "preview_png": os.path.basename(result.paths.preview_png),
            "film_1bit_tiff": os.path.basename(result.paths.film_1bit_tiff) if settings.write_film_tiff else None,
            "film_1bit_variants": [
                os.path.basename(path) for path in result.variant_film_tiffs
            ] if settings.write_film_tiff and settings.write_halftone_variants else [],
            "manifest_json": os.path.basename(result.paths.manifest_json),
        },
        "halftone_variants": [
            halftone_variant_to_manifest(variant)
            for variant in HALFTONE_PRINT_VARIANTS
        ] if settings.write_film_tiff and settings.write_halftone_variants else [],
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
