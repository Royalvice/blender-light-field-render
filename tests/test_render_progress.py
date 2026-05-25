import importlib.util
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_render_ops_module():
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Operator = object
    bpy.types = bpy_types
    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy_types

    package = types.ModuleType("light_field_plugin")
    package.__path__ = [str(REPO_ROOT / "light_field_plugin")]
    sys.modules["light_field_plugin"] = package

    operators_package = types.ModuleType("light_field_plugin.operators")
    operators_package.__path__ = [str(REPO_ROOT / "light_field_plugin" / "operators")]
    sys.modules["light_field_plugin.operators"] = operators_package

    core_package = types.ModuleType("light_field_plugin.core")
    core_package.__path__ = [str(REPO_ROOT / "light_field_plugin" / "core")]
    sys.modules["light_field_plugin.core"] = core_package

    control_module = types.ModuleType("light_field_plugin.core.light_field_control")
    control_module.get_light_field_control = lambda: None
    sys.modules["light_field_plugin.core.light_field_control"] = control_module

    props_module = types.ModuleType("light_field_plugin.properties.light_field_props")
    props_module.sync_render_resolution = lambda scene: None
    sys.modules["light_field_plugin.properties.light_field_props"] = props_module

    create_ops_module = types.ModuleType("light_field_plugin.operators.create_ops")
    create_ops_module.apply_light_field_parameters = lambda scene: True
    sys.modules["light_field_plugin.operators.create_ops"] = create_ops_module

    path = REPO_ROOT / "light_field_plugin" / "operators" / "render_ops.py"
    spec = importlib.util.spec_from_file_location("light_field_plugin.operators.render_ops", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RenderProgressTests(unittest.TestCase):
    def setUp(self):
        self.render_ops = load_render_ops_module()

    def test_single_frame_progress_detects_first_missing_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "camera_000.tif").write_bytes(b"x")
            Path(tmp, "camera_001.tif").write_bytes(b"x")
            op = self.render_ops.LIGHTFIELD_OT_render_frame()
            props = SimpleNamespace(output_file_format="TIFF")
            self.assertEqual(op._detect_render_progress(tmp, 4, props), 2)

    def test_single_frame_progress_is_format_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "camera_000.png").write_bytes(b"x")
            op = self.render_ops.LIGHTFIELD_OT_render_frame()
            props = SimpleNamespace(output_file_format="TIFF")
            self.assertEqual(op._detect_render_progress(tmp, 2, props), 0)

    def test_film_tiff_progress_requires_1bit_tiff(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "camera_000.tif").write_bytes(b"not a 1-bit tiff")
            op = self.render_ops.LIGHTFIELD_OT_render_frame()
            props = SimpleNamespace(output_file_format="FILM_TIFF")
            self.assertEqual(op._detect_render_progress(tmp, 2, props), 0)

    def test_animation_progress_detects_incomplete_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera_0 = Path(tmp, "camera_000")
            camera_1 = Path(tmp, "camera_001")
            camera_0.mkdir()
            camera_1.mkdir()
            for frame in (1, 2, 3):
                Path(camera_0, f"frame_{frame:04d}.tif").write_bytes(b"x")
            Path(camera_1, "frame_0001.tif").write_bytes(b"x")

            op = self.render_ops.LIGHTFIELD_OT_render_animation()
            props = SimpleNamespace(output_file_format="TIFF")
            self.assertEqual(op._detect_animation_progress(tmp, 3, 1, 3, props), 1)


if __name__ == "__main__":
    unittest.main()
