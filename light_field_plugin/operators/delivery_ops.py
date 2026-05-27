# Final delivery output operators.

from __future__ import annotations

import os
import time

import bpy
from bpy.types import Operator

from .. import bl_info
from ..core.delivery import (
    DeliveryCancelled,
    DeliveryError,
    DeliverySettings,
    HalftoneSettings,
    InterlaceSettings,
    calculate_delivery_pixels,
    generate_delivery_outputs,
    is_large_output,
    make_delivery_paths,
    read_png_info,
    write_error_log,
)
from ..core.light_field_control import get_light_field_control
from .create_ops import apply_light_field_parameters, apply_output_settings
from .render_ops import (
    _capture_render_settings,
    _restore_render_settings,
    _safe_redraw,
    _set_image_settings,
    format_time,
)


def _plugin_version_string() -> str:
    return ".".join(str(part) for part in bl_info.get("version", (0, 0, 0)))


def _frame_dir(output_path: str, frame: int) -> str:
    return os.path.join(output_path, f"frame_{frame:04d}")


def _source_path(frame_dir: str, camera_index: int) -> str:
    return os.path.join(frame_dir, f"camera_{camera_index:03d}.png")


def _source_paths(frame_dir: str, camera_count: int) -> list[str]:
    return [_source_path(frame_dir, i) for i in range(camera_count)]


def _source_is_valid(path: str, width: int, height: int) -> bool:
    if not os.path.exists(path):
        return False
    try:
        img_width, img_height, bit_depth, color_type = read_png_info(path)
    except Exception:
        return False
    return img_width == width and img_height == height and bit_depth == 8 and color_type in {0, 2, 4, 6}


def _invalid_source_indices(paths: list[str], width: int, height: int) -> list[int]:
    return [i for i, path in enumerate(paths) if not _source_is_valid(path, width, height)]


def _build_delivery_settings(context) -> DeliverySettings:
    props = context.scene.light_field_props
    return DeliverySettings(
        width_mm=props.delivery_width_mm,
        height_mm=props.delivery_height_mm,
        ppi=props.delivery_ppi,
        frame=context.scene.frame_current,
        camera_count=props.camera_count,
        source_width=props.resolution_x,
        source_height=props.resolution_y,
        interlace=InterlaceSettings(
            pe=props.interlace_pe,
            angle_degrees=props.interlace_angle,
            offset=props.interlace_offset,
            reverse_views=props.interlace_reverse_views,
        ),
        halftone=HalftoneSettings(
            method=props.film_halftone_method,
            lpi=props.film_lpi,
            angle_degrees=props.film_angle,
            dot_shape=props.film_dot_shape,
            gamma=props.film_gamma,
        ),
        plugin_version=_plugin_version_string(),
        confirm_large_output=props.delivery_confirm_large_output,
    )


def _ensure_camera_system(context) -> bool:
    props = context.scene.light_field_props
    control = get_light_field_control()
    if control.is_created:
        if props.geometry_dirty:
            return apply_light_field_parameters(context.scene)
        apply_output_settings(context.scene)
        return True

    apply_output_settings(context.scene)
    success = control.create(
        camera_count=props.camera_count,
        focal_distance=props.focal_distance,
        opening_angle_deg=props.opening_angle,
        focal_length_mm=props.focal_length,
        sensor_width_mm=props.sensor_width,
        depth_range=props.depth_range,
    )
    if success:
        props.active_camera_index = props.camera_count // 2
        props.geometry_dirty = False
        props.render_settings_dirty = False
    return success


def _render_source_views(context, frame_dir: str, indices: list[int], progress_callback) -> None:
    if not indices:
        return

    scene = context.scene
    props = scene.light_field_props
    control = get_light_field_control()
    os.makedirs(frame_dir, exist_ok=True)
    _set_image_settings(scene, "PNG")
    total = len(indices)
    for completed, camera_index in enumerate(indices, start=1):
        if props.delivery_stop_requested:
            raise DeliveryCancelled("用户停止了交付生成")
        progress_callback("渲染源视角", completed, total, f"相机 {camera_index + 1}/{props.camera_count}")
        control.set_active_camera(camera_index)
        scene.render.filepath = _source_path(frame_dir, camera_index)
        bpy.ops.render.render(write_still=True)
        _safe_redraw()


class LIGHTFIELD_OT_generate_delivery(Operator):
    bl_idname = "lightfield.generate_delivery"
    bl_label = "生成当前帧交付文件"
    bl_description = "渲染或复用当前帧多视角源图，并生成最终交织图与 1-bit 菲林 TIFF"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        props = scene.light_field_props

        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}
        if props.is_delivery_generating:
            self.report({"WARNING"}, "已有交付生成任务正在运行")
            return {"CANCELLED"}

        output_root = bpy.path.abspath(props.output_path)
        if not output_root:
            self.report({"ERROR"}, "请先设置输出路径")
            return {"CANCELLED"}

        settings = _build_delivery_settings(context)
        paths = make_delivery_paths(output_root, scene.frame_current)
        current_stage = {"name": "准备参数"}
        captured = None
        original_camera = scene.camera
        start_time = time.perf_counter()

        def progress(stage: str, current: int, total: int, info: str = "") -> None:
            current_stage["name"] = stage
            props.delivery_stage = stage
            props.delivery_progress = int(current)
            props.delivery_progress_total = max(1, int(total))
            props.delivery_info = info
            props.delivery_elapsed_time = time.perf_counter() - start_time
            _safe_redraw()

        try:
            final_width, final_height = calculate_delivery_pixels(
                settings.width_mm,
                settings.height_mm,
                settings.ppi,
            )
            if is_large_output(final_width, final_height, settings.large_output_pixels) and not settings.confirm_large_output:
                raise DeliveryError("最终像素超过 100MP，请勾选确认生成大图")
            if settings.camera_count <= 0:
                raise DeliveryError("相机数量必须大于 0")
            if settings.interlace.pe <= 0:
                raise DeliveryError("PE 必须大于 0")

            props.is_delivery_generating = True
            props.delivery_stop_requested = False
            props.delivery_progress = 0
            props.delivery_progress_total = 1
            props.delivery_stage = "准备参数"
            props.delivery_info = ""
            props.delivery_last_output_dir = ""

            captured = _capture_render_settings(scene)
            force_rerender = props.geometry_dirty or props.render_settings_dirty or not get_light_field_control().is_created

            progress("创建/更新相机", 0, 1)
            if not _ensure_camera_system(context):
                raise DeliveryError("创建光场相机系统失败")
            if props.render_settings_dirty:
                apply_output_settings(scene)
            if props.delivery_stop_requested:
                raise DeliveryCancelled("用户停止了交付生成")

            frame = scene.frame_current
            frame_dir = _frame_dir(output_root, frame)
            source_paths = _source_paths(frame_dir, props.camera_count)
            if force_rerender:
                invalid_indices = list(range(props.camera_count))
            else:
                invalid_indices = _invalid_source_indices(source_paths, props.resolution_x, props.resolution_y)

            _render_source_views(context, frame_dir, invalid_indices, progress)

            progress("校验源视角", 0, props.camera_count)
            invalid_after_render = _invalid_source_indices(source_paths, props.resolution_x, props.resolution_y)
            if invalid_after_render:
                missing = ", ".join(f"camera_{idx:03d}.png" for idx in invalid_after_render[:5])
                raise DeliveryError(f"源视角 PNG 不完整或尺寸不匹配: {missing}")

            result = generate_delivery_outputs(
                source_paths,
                output_root,
                settings,
                progress_callback=progress,
                stop_callback=lambda: bool(props.delivery_stop_requested),
            )

            props.delivery_last_output_dir = result.paths.output_dir
            props.delivery_elapsed_time = result.elapsed_seconds
            props.delivery_info = f"{result.width_px} x {result.height_px} @ {settings.ppi} PPI"
            self.report(
                {"INFO"},
                (
                    f"交付文件已生成: {result.paths.output_dir} | "
                    f"{result.width_px} x {result.height_px} @ {settings.ppi} PPI | "
                    f"用时 {format_time(result.elapsed_seconds)}"
                ),
            )
            return {"FINISHED"}
        except DeliveryCancelled as exc:
            write_error_log(paths.error_log, current_stage["name"], exc, settings)
            self.report({"WARNING"}, "已停止交付生成")
            return {"CANCELLED"}
        except Exception as exc:
            write_error_log(paths.error_log, current_stage["name"], exc, settings)
            self.report({"ERROR"}, f"交付生成失败: {exc}")
            return {"CANCELLED"}
        finally:
            props.is_delivery_generating = False
            props.delivery_stop_requested = False
            props.delivery_elapsed_time = time.perf_counter() - start_time
            if original_camera:
                scene.camera = original_camera
            if captured:
                _restore_render_settings(scene, captured)


class LIGHTFIELD_OT_stop_delivery(Operator):
    bl_idname = "lightfield.stop_delivery"
    bl_label = "停止交付生成"
    bl_description = "请求停止当前交付生成任务"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.light_field_props
        if not props.is_delivery_generating:
            self.report({"WARNING"}, "当前没有正在运行的交付生成任务")
            return {"CANCELLED"}
        props.delivery_stop_requested = True
        self.report({"INFO"}, "正在停止交付生成")
        return {"FINISHED"}
