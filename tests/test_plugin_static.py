import ast
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PluginStaticTests(unittest.TestCase):
    def test_version_is_0_1_6(self):
        init_text = (REPO_ROOT / "light_field_plugin" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"version": (0, 1, 6)', init_text)

    def test_slider_callbacks_do_not_directly_update_camera_system(self):
        text = (REPO_ROOT / "light_field_plugin" / "properties" / "light_field_props.py").read_text(encoding="utf-8")
        tree = ast.parse(text)

        callback_names = {"mark_geometry_dirty", "mark_render_settings_dirty"}
        callback_sources = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in callback_names:
                callback_sources[node.name] = ast.get_source_segment(text, node)

        self.assertEqual(set(callback_sources), callback_names)
        for source in callback_sources.values():
            self.assertNotIn("control.update(", source)
            self.assertNotIn("control.update_visuals(", source)
            self.assertNotIn("control.update_depth_box(", source)

    def test_properties_use_lightweight_dirty_callbacks(self):
        text = (REPO_ROOT / "light_field_plugin" / "properties" / "light_field_props.py").read_text(encoding="utf-8")
        for prop in ("camera_count", "focal_distance", "opening_angle", "depth_range", "focal_length", "sensor_width"):
            pattern = rf"{prop}: .*?update=mark_geometry_dirty"
            self.assertIsNotNone(re.search(pattern, text, re.S), prop)
        for prop in ("resolution_x", "resolution_y", "output_file_format"):
            pattern = rf"{prop}: .*?update=mark_render_settings_dirty"
            self.assertIsNotNone(re.search(pattern, text, re.S), prop)

    def test_old_live_update_callbacks_removed(self):
        text = (REPO_ROOT / "light_field_plugin" / "properties" / "light_field_props.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"def update_(camera_count|focal_distance|opening_angle|focal_length|sensor_width|resolution)")

    def test_apply_output_settings_refreshes_visual_helpers(self):
        text = (REPO_ROOT / "light_field_plugin" / "operators" / "create_ops.py").read_text(encoding="utf-8")
        self.assertIn("def apply_output_settings", text)
        self.assertIn("control.update_visuals()", text)


if __name__ == "__main__":
    unittest.main()
