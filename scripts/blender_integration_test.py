"""Blender-side integration test for the Light Field Render add-on.

Run from a machine with Blender installed:

    blender --background --python scripts/blender_integration_test.py

The script creates a small scene, registers the add-on, creates a camera array,
renders PNG, continuous TIFF, and 1-bit Film TIFF outputs, then validates that
the expected files exist. It is intentionally small so it can run in CI or on a
developer machine without manual UI interaction.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]


def configure_plugin_import_path():
    plugin_zip = os.environ.get("LIGHT_FIELD_PLUGIN_ZIP")
    if plugin_zip:
        extract_dir = Path(tempfile.mkdtemp(prefix="light_field_plugin_zip_"))
        with zipfile.ZipFile(plugin_zip) as zf:
            zf.extractall(extract_dir)
        sys.path.insert(0, str(extract_dir))
        return extract_dir

    sys.path.insert(0, str(REPO_ROOT))
    return REPO_ROOT


def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def create_test_scene():
    bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0, 0, 0))
    cube = bpy.context.object
    cube.name = "LightField_TestCube"
    bpy.ops.object.light_add(type="AREA", location=(0, -3, 4))
    light = bpy.context.object
    light.name = "LightField_TestLight"
    light.data.energy = 300
    light.data.size = 4
    return cube


def assert_blender_image_size(path: Path, width: int, height: int):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        assert tuple(image.size) == (width, height), f"{path} size={tuple(image.size)}"
    finally:
        bpy.data.images.remove(image)


def read_tiff_tags(path: Path):
    data = path.read_bytes()
    assert data[:2] in {b"II", b"MM"}, path
    endian = "<" if data[:2] == b"II" else ">"
    assert struct.unpack_from(endian + "H", data, 2)[0] == 42, path
    ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
    count = struct.unpack_from(endian + "H", data, ifd_offset)[0]
    tags = {}
    cursor = ifd_offset + 2
    for _ in range(count):
        tag, field_type, value_count, value = struct.unpack_from(endian + "HHII", data, cursor)
        tags[tag] = (field_type, value_count, value)
        cursor += 12
    return tags


def assert_1bit_tiff(path: Path, width: int, height: int):
    tags = read_tiff_tags(path)
    assert tags[256][2] == width, tags[256]
    assert tags[257][2] == height, tags[257]
    assert (tags[258][2] & 0xFFFF) == 1, tags[258]
    assert (tags[259][2] & 0xFFFF) == 1, tags[259]
    assert (tags[262][2] & 0xFFFF) == 0, tags[262]
    assert (tags[277][2] & 0xFFFF) == 1, tags[277]


def main():
    plugin_source = configure_plugin_import_path()
    import light_field_plugin

    reset_scene()
    create_test_scene()
    light_field_plugin.register()

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    props = scene.light_field_props
    props.camera_count = 3
    props.focal_distance = 5.0
    props.opening_angle = 8.0
    props.depth_range = 1.0
    props.focal_length = 35.0
    props.sensor_width = 36.0
    props.resolution_x = 64
    props.resolution_y = 48
    props.frame_start = 1
    props.frame_end = 2

    out_dir = tempfile.mkdtemp(prefix="light_field_blender_test_")
    props.output_path = out_dir

    result = bpy.ops.lightfield.create()
    assert result == {"FINISHED"}, result
    assert len([obj for obj in bpy.data.objects if obj.name.startswith("LF_Camera_")]) == 3

    for fmt, expected_ext in (("PNG", ".png"), ("TIFF", ".tif"), ("FILM_TIFF", ".tif")):
        props.output_file_format = fmt
        props.keep_continuous_source = False
        result = bpy.ops.lightfield.render_frame()
        assert result == {"FINISHED"}, (fmt, result)
        expected = Path(out_dir) / "frame_0001" / f"camera_000{expected_ext}"
        assert expected.exists(), f"Missing {expected}"
        if fmt == "PNG":
            assert_blender_image_size(expected, 64, 48)
        elif fmt == "TIFF":
            assert_blender_image_size(expected, 64, 48)
        else:
            assert_1bit_tiff(expected, 64, 48)
            assert not (Path(out_dir) / "frame_0001" / "camera_000_continuous.png").exists()

    props.output_file_format = "FILM_TIFF"
    result = bpy.ops.lightfield.render_animation()
    assert result == {"FINISHED"}, result
    expected = Path(out_dir) / "camera_000" / "frame_0001.tif"
    assert expected.exists(), f"Missing {expected}"
    assert_1bit_tiff(expected, 64, 48)
    assert not (Path(out_dir) / "camera_000" / "frame_continuous_0001.png").exists()

    print(f"BLENDER_INTEGRATION_OK plugin_source={plugin_source} output={out_dir}")


if __name__ == "__main__":
    main()
