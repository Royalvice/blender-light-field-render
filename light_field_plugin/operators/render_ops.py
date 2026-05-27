# 渲染操作。

from __future__ import annotations

import os
import struct
import time
from array import array

import bpy
from bpy.types import Operator

from ..core.film_tiff import write_halftoned_1bit_tiff
from ..core.light_field_control import get_light_field_control
from ..properties.light_field_props import sync_render_resolution
from .create_ops import apply_light_field_parameters, apply_output_settings


FORMAT_EXTENSIONS = {
    "PNG": ".png",
    "TIFF": ".tif",
    "FILM_TIFF": ".tif",
}


def format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def final_extension(props) -> str:
    return FORMAT_EXTENSIONS.get(props.output_file_format, ".png")


def _is_1bit_tiff(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            data = f.read(4096)
        if len(data) < 10:
            return False
        byte_order = data[:2]
        if byte_order == b"II":
            endian = "<"
        elif byte_order == b"MM":
            endian = ">"
        else:
            return False
        if struct.unpack_from(endian + "H", data, 2)[0] != 42:
            return False
        ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
        if ifd_offset + 2 > len(data):
            return False
        count = struct.unpack_from(endian + "H", data, ifd_offset)[0]
        cursor = ifd_offset + 2
        for _ in range(count):
            if cursor + 12 > len(data):
                return False
            tag, field_type, value_count, value = struct.unpack_from(endian + "HHII", data, cursor)
            if tag == 258 and field_type == 3 and value_count == 1:
                return (value & 0xFFFF) == 1
            cursor += 12
    except Exception:
        return False
    return False


def _is_completed_render_file(path: str, props) -> bool:
    if not os.path.exists(path):
        return False
    if props.output_file_format == "FILM_TIFF":
        return _is_1bit_tiff(path)
    return True


def _set_image_settings(scene, file_format: str) -> None:
    settings = scene.render.image_settings
    settings.file_format = file_format
    if hasattr(settings, "color_mode"):
        settings.color_mode = "RGB"
    if hasattr(settings, "color_depth"):
        settings.color_depth = "8"
    if file_format == "TIFF" and hasattr(settings, "tiff_codec"):
        try:
            settings.tiff_codec = "NONE"
        except Exception:
            pass
    scene.render.use_file_extension = True


def _capture_render_settings(scene) -> dict:
    settings = scene.render.image_settings
    captured = {
        "filepath": scene.render.filepath,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "use_file_extension": scene.render.use_file_extension,
        "file_format": settings.file_format,
    }
    for key in ("color_mode", "color_depth", "tiff_codec"):
        if hasattr(settings, key):
            captured[key] = getattr(settings, key)
    return captured


def _restore_render_settings(scene, captured: dict) -> None:
    settings = scene.render.image_settings
    scene.render.filepath = captured["filepath"]
    scene.render.resolution_x = captured["resolution_x"]
    scene.render.resolution_y = captured["resolution_y"]
    scene.render.use_file_extension = captured["use_file_extension"]
    settings.file_format = captured["file_format"]
    for key in ("color_mode", "color_depth", "tiff_codec"):
        if key in captured and hasattr(settings, key):
            setattr(settings, key, captured[key])


def _safe_redraw() -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except RuntimeError:
        pass


def _blender_image_to_luma_rows(image, gamma: float = 1.0):
    width, height = image.size
    pixels = array("f", [0.0]) * (width * height * 4)
    image.pixels.foreach_get(pixels)

    rows = []
    for y in range(height):
        source_y = height - 1 - y
        row = []
        for x in range(width):
            offset = (source_y * width + x) * 4
            r = max(0.0, min(1.0, pixels[offset]))
            g = max(0.0, min(1.0, pixels[offset + 1]))
            b = max(0.0, min(1.0, pixels[offset + 2]))
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            row.append(int(round(luma * 255.0)))
        rows.append(row)
    return rows


def _export_film_tiff_from_source(source_path: str, output_path: str, props) -> None:
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Continuous source image was not written: {source_path}")

    image = bpy.data.images.load(source_path, check_existing=False)
    try:
        luma_rows = _blender_image_to_luma_rows(image, gamma=props.film_gamma)
    finally:
        bpy.data.images.remove(image)

    write_halftoned_1bit_tiff(
        output_path,
        luma_rows,
        method=props.film_halftone_method,
        dpi=props.film_dpi,
        lpi=props.film_lpi,
        angle_degrees=props.film_angle,
        dot_shape=props.film_dot_shape,
        gamma=props.film_gamma,
    )


def _render_still_to_path(context, props, output_base: str) -> str:
    scene = context.scene
    apply_output_settings(scene)

    if props.output_file_format == "FILM_TIFF":
        source_path = output_base + "_continuous.png"
        final_path = output_base + ".tif"
        _set_image_settings(scene, "PNG")
        scene.render.filepath = source_path
        bpy.ops.render.render(write_still=True)
        _export_film_tiff_from_source(source_path, final_path, props)
        if not props.keep_continuous_source and os.path.exists(source_path):
            os.remove(source_path)
        return final_path

    if props.output_file_format == "TIFF":
        final_path = output_base + ".tif"
        _set_image_settings(scene, "TIFF")
    else:
        final_path = output_base + ".png"
        _set_image_settings(scene, "PNG")

    scene.render.filepath = final_path
    bpy.ops.render.render(write_still=True)
    return final_path


def _render_animation_to_dir(context, props, camera_dir: str, frame_start: int, frame_end: int) -> None:
    scene = context.scene
    apply_output_settings(scene)

    if props.output_file_format == "FILM_TIFF":
        source_prefix = os.path.join(camera_dir, "frame_continuous_")
        _set_image_settings(scene, "PNG")
        scene.render.filepath = source_prefix
        bpy.ops.render.render(animation=True)

        for frame in range(frame_start, frame_end + 1):
            source_path = os.path.join(camera_dir, f"frame_continuous_{frame:04d}.png")
            final_path = os.path.join(camera_dir, f"frame_{frame:04d}.tif")
            _export_film_tiff_from_source(source_path, final_path, props)
            if not props.keep_continuous_source and os.path.exists(source_path):
                os.remove(source_path)
        return

    if props.output_file_format == "TIFF":
        _set_image_settings(scene, "TIFF")
    else:
        _set_image_settings(scene, "PNG")
    scene.render.filepath = os.path.join(camera_dir, "frame_")
    bpy.ops.render.render(animation=True)


class LIGHTFIELD_OT_render_frame(Operator):
    bl_idname = "lightfield.render_frame"
    bl_label = "渲染当前帧"
    bl_description = "使用每台光场相机渲染当前帧"
    bl_options = {"REGISTER"}

    _original_camera = None

    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            self.report({"ERROR"}, "请先创建光场相机系统")
            return {"CANCELLED"}
        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}

        if props.geometry_dirty:
            apply_light_field_parameters(context.scene)
        else:
            apply_output_settings(context.scene)

        output_path = bpy.path.abspath(props.output_path)
        if not output_path:
            self.report({"ERROR"}, "请先设置输出路径")
            return {"CANCELLED"}

        frame = context.scene.frame_current
        frame_dir = os.path.join(output_path, f"frame_{frame:04d}")
        os.makedirs(frame_dir, exist_ok=True)

        start_index = self._detect_render_progress(frame_dir, props.camera_count, props)
        if start_index >= props.camera_count:
            self.report({"INFO"}, "当前帧已全部渲染")
            return {"FINISHED"}

        captured = _capture_render_settings(context.scene)
        self._original_camera = context.scene.camera

        props.is_rendering = True
        props.render_progress = start_index
        start_time = time.perf_counter()
        props.render_start_time = 0.0
        props.render_elapsed_time = 0.0
        wm = context.window_manager
        wm.progress_begin(0, max(1, props.camera_count))

        total = props.camera_count
        try:
            for cam_idx in range(start_index, total):
                if not props.is_rendering:
                    break

                elapsed = time.perf_counter() - start_time
                props.render_elapsed_time = elapsed
                completed = cam_idx - start_index
                if completed > 0:
                    avg_time = elapsed / completed
                    remaining = avg_time * (total - cam_idx)
                    props.render_info = f"相机 {cam_idx + 1}/{total} | 剩余约 {format_time(remaining)}"
                else:
                    props.render_info = f"相机 {cam_idx + 1}/{total}"

                props.render_progress = cam_idx
                wm.progress_update(cam_idx)
                _safe_redraw()

                control.set_active_camera(cam_idx)
                output_base = os.path.join(frame_dir, f"camera_{cam_idx:03d}")
                _render_still_to_path(context, props, output_base)
        finally:
            total_time = time.perf_counter() - start_time
            props.is_rendering = False
            props.render_progress = total
            wm.progress_update(total)
            props.render_elapsed_time = total_time
            props.render_info = f"完成，用时 {format_time(total_time)}"
            if self._original_camera:
                context.scene.camera = self._original_camera
            _restore_render_settings(context.scene, captured)
            wm.progress_end()

        self.report({"INFO"}, f"已渲染 {total} 台相机，用时 {format_time(props.render_elapsed_time)}")
        return {"FINISHED"}

    def _detect_render_progress(self, output_dir: str, total_cameras: int, props) -> int:
        if not os.path.exists(output_dir):
            return 0
        extension = final_extension(props)
        for i in range(total_cameras):
            path = os.path.join(output_dir, f"camera_{i:03d}{extension}")
            if not _is_completed_render_file(path, props):
                return i
        return total_cameras


class LIGHTFIELD_OT_render_animation(Operator):
    bl_idname = "lightfield.render_animation"
    bl_label = "渲染动画"
    bl_description = "使用每台光场相机渲染选定帧范围"
    bl_options = {"REGISTER"}

    _original_camera = None

    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            self.report({"ERROR"}, "请先创建光场相机系统")
            return {"CANCELLED"}
        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}

        if props.geometry_dirty:
            apply_light_field_parameters(context.scene)
        else:
            apply_output_settings(context.scene)

        output_path = bpy.path.abspath(props.output_path)
        if not output_path:
            self.report({"ERROR"}, "请先设置输出路径")
            return {"CANCELLED"}

        os.makedirs(output_path, exist_ok=True)
        frame_start = props.frame_start
        frame_end = props.frame_end
        if frame_end < frame_start:
            self.report({"ERROR"}, "结束帧必须大于或等于开始帧")
            return {"CANCELLED"}

        start_camera = self._detect_animation_progress(
            output_path,
            props.camera_count,
            frame_start,
            frame_end,
            props,
        )
        if start_camera >= props.camera_count:
            self.report({"INFO"}, "动画已全部渲染")
            return {"FINISHED"}

        captured = _capture_render_settings(context.scene)
        self._original_camera = context.scene.camera
        original_frame_start = context.scene.frame_start
        original_frame_end = context.scene.frame_end

        props.is_rendering = True
        props.render_progress = start_camera
        start_time = time.perf_counter()
        props.render_start_time = 0.0
        props.render_elapsed_time = 0.0
        wm = context.window_manager
        wm.progress_begin(0, max(1, props.camera_count))

        context.scene.frame_start = frame_start
        context.scene.frame_end = frame_end
        total_cameras = props.camera_count

        try:
            for cam_idx in range(start_camera, total_cameras):
                if not props.is_rendering:
                    break

                elapsed = time.perf_counter() - start_time
                props.render_elapsed_time = elapsed
                completed = cam_idx - start_camera
                if completed > 0:
                    avg_time = elapsed / completed
                    remaining = avg_time * (total_cameras - cam_idx)
                    props.render_info = (
                        f"相机 {cam_idx + 1}/{total_cameras} | "
                        f"帧 {frame_start}-{frame_end} | 剩余约 {format_time(remaining)}"
                    )
                else:
                    props.render_info = f"相机 {cam_idx + 1}/{total_cameras} | 帧 {frame_start}-{frame_end}"

                props.render_progress = cam_idx
                wm.progress_update(cam_idx)
                _safe_redraw()
                control.set_active_camera(cam_idx)

                camera_dir = os.path.join(output_path, f"camera_{cam_idx:03d}")
                os.makedirs(camera_dir, exist_ok=True)
                _render_animation_to_dir(context, props, camera_dir, frame_start, frame_end)
        finally:
            total_time = time.perf_counter() - start_time
            props.is_rendering = False
            props.render_progress = total_cameras
            wm.progress_update(total_cameras)
            props.render_elapsed_time = total_time
            props.render_info = f"完成，用时 {format_time(total_time)}"

            context.scene.frame_start = original_frame_start
            context.scene.frame_end = original_frame_end
            if self._original_camera:
                context.scene.camera = self._original_camera
            _restore_render_settings(context.scene, captured)
            wm.progress_end()

        self.report({"INFO"}, f"已渲染 {total_cameras} 台相机，用时 {format_time(props.render_elapsed_time)}")
        return {"FINISHED"}

    def _detect_animation_progress(
        self,
        output_path: str,
        total_cameras: int,
        frame_start: int,
        frame_end: int,
        props,
    ) -> int:
        if not os.path.exists(output_path):
            return 0
        extension = final_extension(props)
        for i in range(total_cameras):
            camera_dir = os.path.join(output_path, f"camera_{i:03d}")
            if not os.path.exists(camera_dir):
                return i
            for frame in range(frame_start, frame_end + 1):
                path = os.path.join(camera_dir, f"frame_{frame:04d}{extension}")
                if not _is_completed_render_file(path, props):
                    return i
        return total_cameras


class LIGHTFIELD_OT_stop_render(Operator):
    bl_idname = "lightfield.stop_render"
    bl_label = "停止渲染"
    bl_description = "当前相机渲染完成后停止渲染任务"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.light_field_props
        if not props.is_rendering:
            self.report({"WARNING"}, "当前没有正在运行的渲染任务")
            return {"CANCELLED"}
        props.is_rendering = False
        self.report({"INFO"}, "正在停止渲染任务")
        return {"FINISHED"}
