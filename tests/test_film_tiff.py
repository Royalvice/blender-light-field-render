import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_film_tiff_module():
    path = REPO_ROOT / "light_field_plugin" / "core" / "film_tiff.py"
    spec = importlib.util.spec_from_file_location("film_tiff_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_tiff_tags(path):
    data = Path(path).read_bytes()
    assert data[:2] == b"II"
    assert struct.unpack_from("<H", data, 2)[0] == 42
    ifd_offset = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, ifd_offset)[0]
    tags = {}
    cursor = ifd_offset + 2
    for _ in range(count):
        tag, field_type, value_count, value = struct.unpack_from("<HHII", data, cursor)
        tags[tag] = (field_type, value_count, value)
        cursor += 12
    return tags


class FilmTiffTests(unittest.TestCase):
    def setUp(self):
        self.film_tiff = load_film_tiff_module()

    def test_fm_halftone_writes_valid_1bit_tiff(self):
        width, height = 64, 32
        luma = [[int(x / (width - 1) * 255) for x in range(width)] for _ in range(height)]

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "fm.tif")
            black = self.film_tiff.write_halftoned_1bit_tiff(
                output,
                luma,
                method="FM",
                dpi=2400,
                lpi=200,
                angle_degrees=45,
                dot_shape="ROUND",
            )

            self.assertTrue(os.path.exists(output))
            tags = read_tiff_tags(output)
            self.assertEqual(tags[256][2], width)
            self.assertEqual(tags[257][2], height)
            self.assertEqual(tags[258][2] & 0xFFFF, 1)
            self.assertEqual(tags[259][2] & 0xFFFF, 1)
            self.assertEqual(tags[262][2] & 0xFFFF, 0)
            self.assertEqual(tags[277][2] & 0xFFFF, 1)
            self.assertEqual(tags[279][2], ((width + 7) // 8) * height)

            left_black = sum(row[x] for row in black for x in range(0, width // 4))
            right_black = sum(row[x] for row in black for x in range(width * 3 // 4, width))
            self.assertGreater(left_black, right_black)

    def test_am_halftone_produces_clustered_bitmap(self):
        width, height = 96, 48
        luma = [[64 if x < width // 2 else 224 for x in range(width)] for _ in range(height)]

        black = self.film_tiff.halftone_luma(
            luma,
            method="AM",
            dpi=1200,
            lpi=150,
            angle_degrees=15,
            dot_shape="DIAMOND",
        )

        self.assertEqual(len(black), height)
        self.assertEqual(len(black[0]), width)
        dark_half = sum(row[x] for row in black for x in range(0, width // 2))
        light_half = sum(row[x] for row in black for x in range(width // 2, width))
        self.assertGreater(dark_half, light_half)

    def test_optional_pillow_can_read_generated_tiff(self):
        try:
            from PIL import Image
        except Exception:
            self.skipTest("Pillow is not installed")

        width, height = 17, 11
        luma = [[0 if (x + y) % 2 else 255 for x in range(width)] for y in range(height)]

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "checker.tif")
            self.film_tiff.write_halftoned_1bit_tiff(output, luma, method="FM")
            with Image.open(output) as img:
                self.assertEqual(img.size, (width, height))
                self.assertIn(img.mode, {"1", "L"})


if __name__ == "__main__":
    unittest.main()
