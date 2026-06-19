import ast
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PluginStaticTests(unittest.TestCase):
    def test_version_is_0_1_16(self):
        init_text = (REPO_ROOT / "light_field_plugin" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"version": (0, 1, 16)', init_text)

    def test_panel_labels_are_localized_in_chinese(self):
        text = (REPO_ROOT / "light_field_plugin" / "panels" / "main_panel.py").read_text(encoding="utf-8")
        for snippet in (
            'bl_category = "光场"',
            'bl_label = "输出设置"',
            'bl_label = "1-bit 菲林 TIFF"',
            'bl_label = "最终交付输出"',
            'text="输出格式"',
            'text="渲染当前帧"',
            'text="渲染动画"',
            'text="只生成连续调交织图"',
            'text="生成当前帧交付文件"',
            'text="停止交付生成"',
        ):
            self.assertIn(snippet, text)

    def test_property_labels_are_localized_in_chinese(self):
        text = (REPO_ROOT / "light_field_plugin" / "properties" / "light_field_props.py").read_text(encoding="utf-8")
        for snippet in (
            'name="输出格式"',
            '("JPG", "JPG"',
            'default="JPG"',
            'name="JPG 质量"',
            '"LBY-like近似"',
            'name="拖动结束后自动应用"',
            'name="当前相机"',
            'name="开始帧"',
            'name="结束帧"',
            'name="交付宽度"',
            'name="交付高度"',
            'name="PPI"',
        ):
            self.assertIn(snippet, text)

    def test_operator_labels_and_reports_are_localized_in_chinese(self):
        create_text = (REPO_ROOT / "light_field_plugin" / "operators" / "create_ops.py").read_text(encoding="utf-8")
        render_text = (REPO_ROOT / "light_field_plugin" / "operators" / "render_ops.py").read_text(encoding="utf-8")
        for snippet in (
            'bl_label = "创建光场相机"',
            'bl_label = "应用输出设置"',
            '"请先创建光场相机系统"',
        ):
            self.assertIn(snippet, create_text)
        for snippet in (
            'bl_label = "渲染当前帧"',
            'bl_label = "渲染动画"',
            'bl_label = "停止渲染"',
            '"请先创建光场相机系统"',
            '"已有渲染任务正在运行"',
        ):
            self.assertIn(snippet, render_text)

        delivery_text = (REPO_ROOT / "light_field_plugin" / "operators" / "delivery_ops.py").read_text(encoding="utf-8")
        for snippet in (
            'bl_label = "生成当前帧交付文件"',
            'bl_label = "生成连续调交织图"',
            'bl_label = "停止交付生成"',
            '"交付文件已生成:',
        ):
            self.assertIn(snippet, delivery_text)

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

    def test_delivery_does_not_force_rerender_for_halftone_changes(self):
        text = (REPO_ROOT / "light_field_plugin" / "operators" / "delivery_ops.py").read_text(encoding="utf-8")
        self.assertIn("force_rerender = self.props.geometry_dirty or not get_light_field_control().is_created", text)
        self.assertNotIn("force_rerender = self.props.geometry_dirty or self.props.render_settings_dirty", text)


if __name__ == "__main__":
    unittest.main()
