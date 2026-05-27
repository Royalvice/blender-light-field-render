import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_delivery_module():
    path = REPO_ROOT / "light_field_plugin" / "core" / "delivery.py"
    spec = importlib.util.spec_from_file_location("delivery_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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


class DeliveryCoreTests(unittest.TestCase):
    def setUp(self):
        self.delivery = load_delivery_module()

    def test_delivery_pixel_size_uses_mm_ppi_and_half_up_rounding(self):
        self.assertEqual(self.delivery.calculate_delivery_pixels(210.0, 297.0, 300), (2480, 3508))
        self.assertEqual(self.delivery.round_half_up(10.5), 11)

    def test_interlace_view_index_and_reverse_order(self):
        view = self.delivery.interlace_view_index(
            x=0,
            y=0,
            channel=0,
            num_views=4,
            pe=16.0,
            angle_radians=0.0,
            offset=0.0,
        )
        self.assertEqual(view, 0)
        self.assertEqual(self.delivery.build_view_order(4, reverse=True), [3, 2, 1, 0])

    def test_png_roundtrip_and_tiff_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            png_path = os.path.join(tmp, "source.png")
            rows = [
                bytes([255, 0, 0, 0, 255, 0]),
                bytes([0, 0, 255, 255, 255, 255]),
            ]
            self.delivery.write_rgb_png(png_path, 2, 2, rows)
            image = self.delivery.read_png_rgb(png_path)
            self.assertEqual((image.width, image.height), (2, 2))
            self.assertEqual(image.pixel_rgb(0, 0), (255, 0, 0))

            rgb_tiff = os.path.join(tmp, "rgb.tif")
            with self.delivery.RgbTiffWriter(rgb_tiff, 2, 2, 300) as writer:
                for row in rows:
                    writer.write_row(row)
            tags = read_tiff_tags(rgb_tiff)
            self.assertEqual(tags[256][2], 2)
            self.assertEqual(tags[257][2], 2)
            self.assertEqual(tags[277][2] & 0xFFFF, 3)

            bit_tiff = os.path.join(tmp, "bit.tif")
            with self.delivery.OneBitTiffWriter(bit_tiff, 2, 2, 300) as writer:
                writer.write_black_row([True, False])
                writer.write_black_row([False, True])
            tags = read_tiff_tags(bit_tiff)
            self.assertEqual(tags[258][2] & 0xFFFF, 1)
            self.assertEqual(tags[277][2] & 0xFFFF, 1)

    def test_generate_delivery_outputs_writes_expected_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            colors = [
                (255, 0, 0),
                (0, 255, 0),
                (0, 0, 255),
            ]
            for idx, color in enumerate(colors):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                row = bytes(color * 4)
                self.delivery.write_rgb_png(path, 4, 3, [row, row, row])
                source_paths.append(path)

            settings = self.delivery.DeliverySettings(
                width_mm=2.54,
                height_mm=1.27,
                ppi=100,
                frame=1,
                camera_count=3,
                source_width=4,
                source_height=3,
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                halftone=self.delivery.HalftoneSettings(method="FM", lpi=100, gamma=1.0),
                plugin_version="0.1.8",
            )

            result = self.delivery.generate_delivery_outputs(source_paths, tmp, settings)
            self.assertEqual((result.width_px, result.height_px), (10, 5))
            self.assertTrue(os.path.exists(result.paths.interlaced_tiff))
            self.assertTrue(os.path.exists(result.paths.preview_png))
            self.assertTrue(os.path.exists(result.paths.film_1bit_tiff))
            self.assertTrue(os.path.exists(result.paths.manifest_json))
            self.assertFalse(os.path.exists(result.paths.error_log))

            rgb_tags = read_tiff_tags(result.paths.interlaced_tiff)
            self.assertEqual(rgb_tags[256][2], 10)
            self.assertEqual(rgb_tags[257][2], 5)
            self.assertEqual(rgb_tags[277][2] & 0xFFFF, 3)

            bit_tags = read_tiff_tags(result.paths.film_1bit_tiff)
            self.assertEqual(bit_tags[256][2], 10)
            self.assertEqual(bit_tags[257][2], 5)
            self.assertEqual(bit_tags[258][2] & 0xFFFF, 1)

            manifest = json.loads(Path(result.paths.manifest_json).read_text(encoding="utf-8"))
            self.assertEqual(manifest["plugin_version"], "0.1.8")
            self.assertEqual(manifest["delivery"]["width_px"], 10)
            self.assertEqual(manifest["delivery"]["height_px"], 5)
            self.assertEqual(manifest["source_views"]["camera_count"], 3)

    def test_large_output_requires_confirmation(self):
        source = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "camera_000.png")
            self.delivery.write_rgb_png(path, 1, 1, [bytes([0, 0, 0])])
            source.append(path)
            settings = self.delivery.DeliverySettings(
                width_mm=1000,
                height_mm=1000,
                ppi=1000,
                frame=1,
                camera_count=1,
                source_width=1,
                source_height=1,
                interlace=self.delivery.InterlaceSettings(),
                halftone=self.delivery.HalftoneSettings(),
                large_output_pixels=10,
                confirm_large_output=False,
            )
            with self.assertRaises(self.delivery.DeliveryError):
                self.delivery.generate_delivery_outputs(source, tmp, settings)


if __name__ == "__main__":
    unittest.main()
