import json
import math
import tempfile
import unittest
from pathlib import Path

from utils import generate_probe_dataset as probe


class ProbeDatasetTests(unittest.TestCase):
    def test_view_palette_is_unique_and_not_near_extreme_rgb(self):
        colors = [probe.view_color(index) for index in range(150)]
        self.assertEqual(len(set(colors)), 150)
        self.assertGreaterEqual(min(min(color) for color in colors), 16)
        self.assertLessEqual(max(max(color) for color in colors), 236)

        min_distance = min(
            math.sqrt(sum((left[channel] - right[channel]) ** 2 for channel in range(3)))
            for index, left in enumerate(colors)
            for right in colors[index + 1 :]
        )
        self.assertGreaterEqual(min_distance, 44.0)

    def test_continuous_decode_region_is_clean_uniform_view_color(self):
        image = probe.generate_view(probe.DEFAULT_WIDTH, probe.DEFAULT_HEIGHT, probe.DEFAULT_COUNT, 42)
        expected = probe.view_color(42)
        for y in (930, 1010, 1200, 1330):
            for x in (10, 540, 1080, 1620, 2150):
                self.assertEqual(tuple(int(value) for value in image[y, x]), expected)

    def test_binary_code_region_uses_repeated_local_tiles(self):
        image = probe.generate_view(probe.DEFAULT_WIDTH, probe.DEFAULT_HEIGHT, probe.DEFAULT_COUNT, 73)
        region = image[380:900, :, 0]
        unique_values = set(int(value) for value in region.reshape(-1))
        self.assertIn(0, unique_values)
        self.assertIn(127, unique_values)
        self.assertIn(255, unique_values)

        # The old design used full-width horizontal bands. The fixed design must
        # have many transitions on the same row, making local model fitting possible.
        row = region[30]
        transitions = sum(1 for left, right in zip(row, row[1:]) if int(left) != int(right))
        self.assertGreater(transitions, 50)

    def test_manifest_records_dynamic_count_and_palette(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            probe.write_manifest(out_dir, 2160, 3651, 12, 1)
            manifest = json.loads((out_dir / "probe_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["filename_pattern"], "images/probe_000.png ... images/probe_011.png")
        self.assertEqual(manifest["view_palette"]["levels"], list(probe.VIEW_COLOR_LEVELS))
        self.assertEqual(manifest["view_palette"]["minimum_rgb_distance"], 44.0)


if __name__ == "__main__":
    unittest.main()
