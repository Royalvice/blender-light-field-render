import importlib.util
import json
import os
import shutil
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
    magic = struct.unpack_from("<H", data, 2)[0]
    tags = {}
    if magic == 42:
        ifd_offset = struct.unpack_from("<I", data, 4)[0]
        count = struct.unpack_from("<H", data, ifd_offset)[0]
        cursor = ifd_offset + 2
        for _ in range(count):
            tag, field_type, value_count, value = struct.unpack_from("<HHII", data, cursor)
            tags[tag] = (field_type, value_count, value)
            cursor += 12
        return tags
    assert magic == 43
    assert struct.unpack_from("<HH", data, 4) == (8, 0)
    ifd_offset = struct.unpack_from("<Q", data, 8)[0]
    count = struct.unpack_from("<Q", data, ifd_offset)[0]
    cursor = ifd_offset + 8
    for _ in range(count):
        tag, field_type, value_count, value = struct.unpack_from("<HHQQ", data, cursor)
        tags[tag] = (field_type, value_count, value)
        cursor += 20
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
            rgb_info = self.delivery.read_uncompressed_rgb_tiff_info(rgb_tiff)
            self.assertEqual((rgb_info.width, rgb_info.height), (2, 2))
            self.assertEqual(list(self.delivery.iter_tiff_rows(rgb_info)), rows)

            bit_tiff = os.path.join(tmp, "bit.tif")
            with self.delivery.OneBitTiffWriter(bit_tiff, 2, 2, 300) as writer:
                writer.write_black_row([True, False])
                writer.write_black_row([False, True])
            tags = read_tiff_tags(bit_tiff)
            self.assertEqual(tags[258][2] & 0xFFFF, 1)
            self.assertEqual(tags[262][2] & 0xFFFF, 1)
            self.assertEqual(tags[277][2] & 0xFFFF, 1)
            payload = Path(bit_tiff).read_bytes()[-2:]
            self.assertEqual(payload, bytes([0x40, 0x80]))
            bit_info = self.delivery.read_uncompressed_one_bit_tiff_info(bit_tiff)
            self.assertEqual((bit_info.width, bit_info.height), (2, 2))

    def test_bigtiff_writers_use_64bit_offsets_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rgb_tiff = os.path.join(tmp, "rgb_big.tif")
            with self.delivery.RgbTiffWriter(rgb_tiff, 2, 1, 4000, force_bigtiff=True) as writer:
                self.assertTrue(writer.is_bigtiff)
                writer.write_row(bytes([255, 0, 0, 0, 255, 0]))
            data = Path(rgb_tiff).read_bytes()
            self.assertEqual(struct.unpack_from("<H", data, 2)[0], 43)
            tags = read_tiff_tags(rgb_tiff)
            self.assertEqual(tags[273][0], 16)
            self.assertEqual(tags[279][0], 16)
            self.assertEqual(tags[279][2], 6)
            rgb_info = self.delivery.read_uncompressed_rgb_tiff_info(rgb_tiff)
            self.assertEqual(rgb_info.bits_per_sample, (8, 8, 8))
            self.assertEqual(rgb_info.dpi_x, 4000.0)
            self.assertEqual(rgb_info.dpi_y, 4000.0)
            self.assertEqual(list(self.delivery.iter_tiff_rows(rgb_info)), [bytes([255, 0, 0, 0, 255, 0])])

            bit_tiff = os.path.join(tmp, "bit_big.tif")
            with self.delivery.OneBitTiffWriter(bit_tiff, 9, 1, 4000, force_bigtiff=True) as writer:
                self.assertTrue(writer.is_bigtiff)
                writer.write_black_row([True, False, True, False, True, False, True, False, True])
            self.assertEqual(struct.unpack_from("<H", Path(bit_tiff).read_bytes(), 2)[0], 43)
            tags = read_tiff_tags(bit_tiff)
            self.assertEqual(tags[273][0], 16)
            self.assertEqual(tags[279][0], 16)
            self.assertEqual(tags[279][2], 2)
            self.assertEqual(tags[262][2] & 0xFFFF, 1)
            bit_info = self.delivery.read_uncompressed_one_bit_tiff_info(bit_tiff)
            self.assertEqual(bit_info.bits_per_sample, (1,))
            self.assertEqual(bit_info.dpi_x, 4000.0)
            self.assertEqual(bit_info.dpi_y, 4000.0)

    def test_lby_halftoner_outputs_deterministic_row_threshold_screen(self):
        halftoner = self.delivery.StreamingHalftoner(
            96,
            self.delivery.HalftoneSettings(method="LBY", gamma=0.25, line_density=0.25),
            4000,
        )
        row = bytes([160, 160, 160] * 96)
        result = halftoner.process_rgb_row(0, row)
        repeat = halftoner.process_rgb_row(0, row)
        self.assertEqual(list(result), list(repeat))
        self.assertEqual(sum(bool(value) for value in result), 0)
        dark_phase = halftoner.process_rgb_row(17, row)
        self.assertEqual(sum(bool(value) for value in dark_phase), 96)

        extremes_halftoner = self.delivery.StreamingHalftoner(
            2,
            self.delivery.HalftoneSettings(method="LBY", gamma=0.25, line_density=0.25),
            4000,
        )
        extremes = extremes_halftoner.process_rgb_row(17, bytes([0, 0, 0, 255, 255, 255]))
        self.assertEqual(list(extremes), [True, False])

    def test_standard_am_defaults_and_density_are_monotonic(self):
        settings = self.delivery.HalftoneSettings()
        self.assertEqual(settings.method, "AM")
        self.assertEqual(settings.lpi, 200)
        self.assertEqual(settings.angle_degrees, 45.0)
        self.assertEqual(settings.dot_shape, "ROUND")
        self.assertEqual(settings.gamma, 1.0)

        halftoner = self.delivery.StreamingHalftoner(80, settings, 4000)
        counts = []
        for value in (255, 192, 128, 64, 0):
            total = 0
            row = bytes([value, value, value] * 80)
            for y in range(80):
                total += sum(bool(pixel) for pixel in halftoner.process_rgb_row(y, row))
            counts.append(total)
        self.assertEqual(counts, sorted(counts))

    def test_lby_v2_profile_scales_period_with_dpi(self):
        profile = self.delivery.get_halftone_profile("LBY_V2")
        self.assertEqual(profile.name, "LBY_RIP_v2")
        self.assertEqual(profile.family, "ROW_THRESHOLD_DPI_SCALED")
        self.assertEqual(self.delivery.effective_halftone_period_px(profile, 4000), 18.0)
        self.assertEqual(self.delivery.effective_halftone_period_px(profile, 8000), 36.0)

        settings = self.delivery.HalftoneSettings(method="LBY_V2")
        halftoner_4000 = self.delivery.StreamingHalftoner(4, settings, 4000)
        halftoner_8000 = self.delivery.StreamingHalftoner(4, settings, 8000)
        self.assertIs(halftoner_4000.profile, profile)
        self.assertIs(halftoner_8000.profile, profile)

    def test_halftone_interlaced_tiff_writes_report_and_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            interlaced = os.path.join(tmp, "interlaced.tif")
            rows = [
                bytes([0, 0, 0, 255, 255, 255, 128, 128, 128, 64, 64, 64]),
                bytes([255, 255, 255, 0, 0, 0, 128, 128, 128, 192, 192, 192]),
            ]
            with self.delivery.RgbTiffWriter(interlaced, 4, 2, 4000) as writer:
                for row in rows:
                    writer.write_row(row)

            film = os.path.join(tmp, "film_1bit.tif")
            report_path = os.path.join(tmp, "halftone_calibration_report.json")
            report = self.delivery.halftone_interlaced_tiff(
                interlaced,
                film,
                ppi=4000,
                calibration_report_json=report_path,
            )
            self.assertTrue(os.path.exists(film))
            self.assertTrue(os.path.exists(report_path))
            self.assertEqual(report["halftone_profile"]["profile_name"], "LBY_row_threshold_v1")
            self.assertEqual(report["halftone_profile"]["family"], "ROW_THRESHOLD")
            self.assertEqual(report["film_tiff"]["width_px"], 4)
            self.assertEqual(report["film_tiff"]["height_px"], 2)
            self.assertEqual(report["halftone_variants"], [])

            am_report = self.delivery.halftone_interlaced_tiff(
                interlaced,
                os.path.join(tmp, "film_am_1bit.tif"),
                halftone_settings=self.delivery.HalftoneSettings(method="AM", lpi=200, angle_degrees=45.0),
                ppi=4000,
                calibration_report_json=report_path,
                write_variants=True,
            )
            self.assertEqual(am_report["halftone_profile"]["family"], "AM_CLUSTERED_DOT")
            self.assertEqual(am_report["halftone_profile"]["screen_lpi"], 200)
            self.assertEqual(am_report["halftone_profile"]["luma_standard"], "Rec.709")
            self.assertEqual(am_report["halftone_variants"], [])

            target = os.path.join(tmp, "target.tif")
            shutil.copyfile(film, target)
            compared = self.delivery.halftone_interlaced_tiff(
                interlaced,
                film,
                ppi=4000,
                target_tiff=target,
                calibration_report_json=report_path,
            )
            self.assertTrue(compared["comparison"]["same_shape"])
            self.assertEqual(compared["comparison"]["mismatch_count"], 0)
            self.assertEqual(compared["comparison"]["mismatch_ratio"], 0.0)

            variant_report = self.delivery.halftone_interlaced_tiff(
                interlaced,
                film,
                ppi=4000,
                calibration_report_json=report_path,
                write_variants=True,
            )
            self.assertEqual(len(variant_report["halftone_variants"]), len(self.delivery.HALFTONE_PRINT_VARIANTS))
            for variant in self.delivery.HALFTONE_PRINT_VARIANTS:
                self.assertEqual(variant.family, "LBY_TUNED")
                variant_path = os.path.join(tmp, variant.filename)
                self.assertTrue(os.path.exists(variant_path), variant_path)
                tags = read_tiff_tags(variant_path)
                self.assertEqual(tags[256][2], 4)
                self.assertEqual(tags[257][2], 2)
                self.assertEqual(tags[258][2] & 0xFFFF, 1)

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
                plugin_version="0.1.10",
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
            self.assertEqual(manifest["plugin_version"], "0.1.10")
            self.assertEqual(manifest["delivery"]["width_px"], 10)
            self.assertEqual(manifest["delivery"]["height_px"], 5)
            self.assertFalse(manifest["delivery"]["write_halftone_variants"])
            self.assertEqual(manifest["source_views"]["camera_count"], 3)
            self.assertEqual(manifest["source_views"]["source_format"], "JPG")
            self.assertEqual(manifest["source_views"]["files"][0], "camera_000.jpg")
            self.assertEqual(manifest["files"]["film_1bit_variants"], [])

    def test_generate_delivery_outputs_can_write_halftone_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            for idx, color in enumerate(((255, 255, 255), (0, 0, 0))):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                row = bytes(color * 8)
                self.delivery.write_rgb_png(path, 8, 4, [row, row, row, row])
                source_paths.append(path)

            settings = self.delivery.DeliverySettings(
                width_mm=2.54,
                height_mm=2.54,
                ppi=100,
                frame=1,
                camera_count=2,
                source_width=8,
                source_height=4,
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                halftone=self.delivery.HalftoneSettings(method="LBY", gamma=0.25),
                write_halftone_variants=True,
                source_format="PNG",
            )

            result = self.delivery.generate_delivery_outputs(source_paths, tmp, settings)
            self.assertEqual(len(result.variant_film_tiffs), len(self.delivery.HALFTONE_PRINT_VARIANTS))
            for path in result.variant_film_tiffs:
                self.assertTrue(os.path.exists(path), path)
                tags = read_tiff_tags(path)
                self.assertEqual(tags[256][2], 10)
                self.assertEqual(tags[257][2], 10)
                self.assertEqual(tags[258][2] & 0xFFFF, 1)

            manifest = json.loads(Path(result.paths.manifest_json).read_text(encoding="utf-8"))
            self.assertTrue(manifest["delivery"]["write_halftone_variants"])
            self.assertEqual(manifest["source_views"]["source_format"], "PNG")
            self.assertEqual(manifest["source_views"]["files"][0], "camera_000.png")
            self.assertEqual(
                sorted(manifest["files"]["film_1bit_variants"]),
                sorted(variant.filename for variant in self.delivery.HALFTONE_PRINT_VARIANTS),
            )
            self.assertEqual(len(manifest["halftone_variants"]), len(self.delivery.HALFTONE_PRINT_VARIANTS))
            self.assertEqual(
                sorted(item["family"] for item in manifest["halftone_variants"]),
                ["LBY_TUNED"] * len(self.delivery.HALFTONE_PRINT_VARIANTS),
            )

    def test_standard_am_delivery_disables_lby_variants_and_reports_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            for idx, color in enumerate(((255, 255, 255), (0, 0, 0))):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                row = bytes(color * 8)
                self.delivery.write_rgb_png(path, 8, 4, [row, row, row, row])
                source_paths.append(path)

            settings = self.delivery.DeliverySettings(
                width_mm=2.54,
                height_mm=2.54,
                ppi=4000,
                frame=1,
                camera_count=2,
                source_width=8,
                source_height=4,
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                halftone=self.delivery.HalftoneSettings(method="AM", lpi=200, angle_degrees=45.0, dot_shape="ROUND"),
                write_halftone_variants=True,
                source_format="PNG",
                confirm_large_output=True,
            )
            stale_dir = os.path.join(tmp, "delivery", "frame_0001")
            os.makedirs(stale_dir, exist_ok=True)
            for variant in self.delivery.HALFTONE_PRINT_VARIANTS:
                Path(stale_dir, variant.filename).write_bytes(b"stale")

            result = self.delivery.generate_delivery_outputs(source_paths, tmp, settings)
            self.assertEqual(result.variant_film_tiffs, ())
            for variant in self.delivery.HALFTONE_PRINT_VARIANTS:
                self.assertFalse(os.path.exists(os.path.join(tmp, "delivery", "frame_0001", variant.filename)))

            manifest = json.loads(Path(result.paths.manifest_json).read_text(encoding="utf-8"))
            self.assertFalse(manifest["delivery"]["write_halftone_variants"])
            self.assertTrue(manifest["delivery"]["requested_halftone_variants"])
            self.assertEqual(manifest["halftone"]["family"], "AM_CLUSTERED_DOT")
            self.assertEqual(manifest["halftone"]["screen_lpi"], 200)
            self.assertEqual(manifest["halftone"]["screen_angle_degrees"], 45.0)
            self.assertEqual(manifest["halftone"]["screen_dot_shape"], "ROUND")
            self.assertEqual(manifest["halftone"]["luma_standard"], "Rec.709")
            self.assertEqual(manifest["files"]["film_1bit_variants"], [])
            self.assertEqual(manifest["halftone_variants"], [])

    def test_standard_am_native_matches_python_when_available(self):
        if not self.delivery.native_available():
            self.skipTest("native AM generator is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            for idx, color in enumerate(((255, 255, 255), (0, 0, 0))):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                rows = []
                for y in range(12):
                    value = max(0, min(255, color[0] - y * 8))
                    rows.append(bytes([value, value, value] * 12))
                self.delivery.write_rgb_png(path, 12, 12, rows)
                source_paths.append(path)

            base_settings = dict(
                width_mm=5.08,
                height_mm=5.08,
                ppi=100,
                frame=1,
                camera_count=2,
                source_width=12,
                source_height=12,
                halftone=self.delivery.HalftoneSettings(method="AM", lpi=50, angle_degrees=45.0, gamma=1.0),
                write_interlaced_tiff=False,
                source_format="PNG",
            )
            native_settings = self.delivery.DeliverySettings(
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                **base_settings,
            )
            native_result = self.delivery.generate_delivery_outputs(source_paths, os.path.join(tmp, "native"), native_settings)
            original_can_use = self.delivery.NativeAmBatchGenerator.can_use
            try:
                self.delivery.NativeAmBatchGenerator.can_use = classmethod(lambda cls, renderer, settings: False)
                python_result = self.delivery.generate_delivery_outputs(source_paths, os.path.join(tmp, "python"), native_settings)
            finally:
                self.delivery.NativeAmBatchGenerator.can_use = original_can_use
            self.assertEqual(Path(native_result.paths.film_1bit_tiff).read_bytes(), Path(python_result.paths.film_1bit_tiff).read_bytes())

    def test_generate_delivery_outputs_can_skip_interlaced_tiff(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            for idx, color in enumerate(((255, 255, 255), (0, 0, 0))):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                row = bytes(color * 3)
                self.delivery.write_rgb_png(path, 3, 2, [row, row])
                source_paths.append(path)

            settings = self.delivery.DeliverySettings(
                width_mm=2.54,
                height_mm=2.54,
                ppi=100,
                frame=1,
                camera_count=2,
                source_width=3,
                source_height=2,
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                halftone=self.delivery.HalftoneSettings(method="FM", lpi=100, gamma=1.0),
                write_interlaced_tiff=False,
            )

            result = self.delivery.generate_delivery_outputs(source_paths, tmp, settings)
            self.assertFalse(os.path.exists(result.paths.interlaced_tiff))
            self.assertTrue(os.path.exists(result.paths.preview_png))
            self.assertTrue(os.path.exists(result.paths.film_1bit_tiff))
            manifest = json.loads(Path(result.paths.manifest_json).read_text(encoding="utf-8"))
            self.assertFalse(manifest["delivery"]["write_interlaced_tiff"])
            self.assertIsNone(manifest["files"]["interlaced_tiff"])

    def test_generate_delivery_outputs_can_write_only_interlaced_tiff(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_paths = []
            for idx, color in enumerate(((255, 0, 0), (0, 255, 0))):
                path = os.path.join(tmp, f"camera_{idx:03d}.png")
                row = bytes(color * 3)
                self.delivery.write_rgb_png(path, 3, 2, [row, row])
                source_paths.append(path)

            settings = self.delivery.DeliverySettings(
                width_mm=2.54,
                height_mm=2.54,
                ppi=100,
                frame=1,
                camera_count=2,
                source_width=3,
                source_height=2,
                interlace=self.delivery.InterlaceSettings(pe=16.7240, angle_degrees=0.0, offset=0.0),
                halftone=self.delivery.HalftoneSettings(method="FM", lpi=100, gamma=1.0),
                write_interlaced_tiff=True,
                write_film_tiff=False,
            )

            result = self.delivery.generate_delivery_outputs(source_paths, tmp, settings)
            self.assertTrue(os.path.exists(result.paths.interlaced_tiff))
            self.assertTrue(os.path.exists(result.paths.preview_png))
            self.assertFalse(os.path.exists(result.paths.film_1bit_tiff))
            manifest = json.loads(Path(result.paths.manifest_json).read_text(encoding="utf-8"))
            self.assertTrue(manifest["delivery"]["write_interlaced_tiff"])
            self.assertFalse(manifest["delivery"]["write_film_tiff"])
            self.assertEqual(manifest["files"]["interlaced_tiff"], "interlaced.tif")
            self.assertIsNone(manifest["files"]["film_1bit_tiff"])

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
