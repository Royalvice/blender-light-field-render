# Final delivery output operators.

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback
from array import array

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
    halftone_interlaced_tiff,
    is_large_output,
    make_delivery_paths,
    native_jpeg_available,
    read_jpeg_info,
    read_png_info,
    write_rgb_png,
    write_error_log,
)


_ACTIVE_DELIVERY_OPERATOR = None
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
    return os.path.join(frame_dir, f"camera_{camera_index:03d}.jpg")


def _source_paths(frame_dir: str, camera_count: int) -> list[str]:
    return [_source_path(frame_dir, i) for i in range(camera_count)]


def _source_is_valid(path: str, width: int, height: int) -> bool:
    if not os.path.exists(path):
        return False
    try:
        if path.lower().endswith((".jpg", ".jpeg")):
            img_width, img_height = read_jpeg_info(path)
            return img_width == width and img_height == height
        img_width, img_height, bit_depth, color_type = read_png_info(path)
    except Exception:
        return False
    return img_width == width and img_height == height and bit_depth == 8 and color_type in {0, 2, 4, 6}


def _invalid_source_indices(paths: list[str], width: int, height: int) -> list[int]:
    return [i for i, path in enumerate(paths) if not _source_is_valid(path, width, height)]


def _build_delivery_settings(
    context,
    *,
    write_interlaced_tiff: bool | None = None,
    write_film_tiff: bool = True,
) -> DeliverySettings:
    props = context.scene.light_field_props
    if write_interlaced_tiff is None:
        write_interlaced_tiff = props.delivery_write_interlaced_tiff
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
            line_period_px=props.film_line_period_px,
            line_phase_y=props.film_line_phase_y,
            line_density=props.film_line_density,
        ),
        plugin_version=_plugin_version_string(),
        confirm_large_output=props.delivery_confirm_large_output,
        write_interlaced_tiff=write_interlaced_tiff,
        write_film_tiff=write_film_tiff,
        source_format="JPG",
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
    _set_image_settings(scene, "JPEG")
    total = len(indices)
    for completed, camera_index in enumerate(indices, start=1):
        if props.delivery_stop_requested:
            raise DeliveryCancelled("用户停止了交付生成")
        progress_callback("渲染源视角", completed, total, f"相机 {camera_index + 1}/{props.camera_count}")
        control.set_active_camera(camera_index)
        scene.render.filepath = _source_path(frame_dir, camera_index)
        bpy.ops.render.render(write_still=True)
        _safe_redraw()


def _render_single_source_view(context, frame_dir: str, camera_index: int) -> None:
    scene = context.scene
    control = get_light_field_control()
    os.makedirs(frame_dir, exist_ok=True)
    _set_image_settings(scene, "JPEG")
    control.set_active_camera(camera_index)
    scene.render.filepath = _source_path(frame_dir, camera_index)
    bpy.ops.render.render(write_still=True)
    _safe_redraw()


def _linear_to_srgb_byte(value: float) -> int:
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0031308:
        srgb = 12.92 * value
    else:
        srgb = 1.055 * (value ** (1.0 / 2.4)) - 0.055
    return max(0, min(255, int(round(srgb * 255.0))))


def _blender_image_rgb_rows(image):
    width, height = int(image.size[0]), int(image.size[1])
    pixels = array("f", [0.0]) * (width * height * 4)
    image.pixels.foreach_get(pixels)
    rows = []
    for y in range(height):
        source_y = height - 1 - y
        row = bytearray(width * 3)
        for x in range(width):
            src = (source_y * width + x) * 4
            dst = x * 3
            row[dst] = _linear_to_srgb_byte(pixels[src])
            row[dst + 1] = _linear_to_srgb_byte(pixels[src + 1])
            row[dst + 2] = _linear_to_srgb_byte(pixels[src + 2])
        rows.append(bytes(row))
    return width, height, rows


def _cache_jpg_sources_as_png(context, source_paths: list[str], progress_callback, stop_callback) -> tuple[str, list[str]]:
    cache_dir = tempfile.mkdtemp(prefix="light_field_delivery_sources_")
    cached_paths = []
    total = len(source_paths)
    for index, source_path in enumerate(source_paths, start=1):
        if stop_callback():
            raise DeliveryCancelled("用户停止了交付生成")
        progress_callback("解码 JPG 源视角", index, total, os.path.basename(source_path))
        image = bpy.data.images.load(source_path, check_existing=False)
        try:
            width, height, rows = _blender_image_rgb_rows(image)
        finally:
            bpy.data.images.remove(image)
        cached_path = os.path.join(cache_dir, f"camera_{index - 1:03d}.png")
        write_rgb_png(cached_path, width, height, rows)
        cached_paths.append(cached_path)
        _safe_redraw()
    return cache_dir, cached_paths


def _use_direct_jpg_sources() -> bool:
    return native_jpeg_available()


class _DeliveryRunnerMixin:
    def execute(self, context):
        if bpy.app.background or context.window is None:
            return self._execute_blocking(context)
        return self._execute_modal(context)

    def _init_common(self, context):
        self.scene = context.scene
        self.props = context.scene.light_field_props
        self.output_root = bpy.path.abspath(self.props.output_path)
        self.settings = self._build_settings(context)
        self.paths = make_delivery_paths(self.output_root, self.scene.frame_current)
        self.current_stage = "准备参数"
        self.captured = None
        self.original_camera = self.scene.camera
        self.start_time = time.perf_counter()
        self.wm = context.window_manager
        self.wm_progress_active = False
        self.source_cache_dir = None
        self.worker_source_paths = None

    def _build_settings(self, context) -> DeliverySettings:
        return _build_delivery_settings(context)

    def _progress(self, stage: str, current: int, total: int, info: str = "") -> None:
        self.current_stage = stage
        props = self.props
        props.delivery_stage = stage
        props.delivery_progress = int(current)
        props.delivery_progress_total = max(1, int(total))
        props.delivery_info = info
        props.delivery_elapsed_time = time.perf_counter() - self.start_time
        if not self.wm_progress_active:
            self.wm.progress_begin(0, 1000)
            self.wm_progress_active = True
        percent = 0.0 if total <= 0 else max(0.0, min(1.0, float(current) / float(total)))
        self.wm.progress_update(int(percent * 1000))
        _safe_redraw()

    def _validate_settings(self) -> None:
        final_width, final_height = calculate_delivery_pixels(
            self.settings.width_mm,
            self.settings.height_mm,
            self.settings.ppi,
        )
        if is_large_output(final_width, final_height, self.settings.large_output_pixels) and not self.settings.confirm_large_output:
            raise DeliveryError("最终像素超过 100MP，请勾选确认生成大图")
        if self.settings.camera_count <= 0:
            raise DeliveryError("相机数量必须大于 0")
        if self.settings.interlace.pe <= 0:
            raise DeliveryError("PE 必须大于 0")

    def _begin_props(self) -> None:
        props = self.props
        props.is_delivery_generating = True
        props.delivery_stop_requested = False
        props.delivery_progress = 0
        props.delivery_progress_total = 1
        props.delivery_stage = "准备参数"
        props.delivery_info = ""
        props.delivery_last_output_dir = ""
        props.delivery_elapsed_time = 0.0

    def _prepare_sources(self, context) -> None:
        self.captured = _capture_render_settings(self.scene)
        force_rerender = self.props.geometry_dirty or not get_light_field_control().is_created

        self._progress("创建/更新相机", 0, 1)
        if not _ensure_camera_system(context):
            raise DeliveryError("创建光场相机系统失败")
        if self.props.render_settings_dirty:
            apply_output_settings(self.scene)
        if self.props.delivery_stop_requested:
            raise DeliveryCancelled("用户停止了交付生成")

        frame = self.scene.frame_current
        self.frame_dir = _frame_dir(self.output_root, frame)
        self.source_paths = _source_paths(self.frame_dir, self.props.camera_count)
        if force_rerender:
            self.invalid_indices = list(range(self.props.camera_count))
        else:
            self.invalid_indices = _invalid_source_indices(
                self.source_paths,
                self.props.resolution_x,
                self.props.resolution_y,
            )

    def _verify_sources(self) -> None:
        self._progress("校验源视角", 0, self.props.camera_count)
        invalid_after_render = _invalid_source_indices(
            self.source_paths,
            self.props.resolution_x,
            self.props.resolution_y,
        )
        if invalid_after_render:
            missing = ", ".join(f"camera_{idx:03d}.jpg" for idx in invalid_after_render[:5])
            raise DeliveryError(f"源视角 JPG 不完整或尺寸不匹配: {missing}")

    def _prepare_worker_sources(self, context) -> None:
        if _use_direct_jpg_sources():
            self.source_cache_dir = None
            self.worker_source_paths = self.source_paths
            self._progress("准备 JPG 源视角", self.props.camera_count, self.props.camera_count, "使用 Native 直接解码")
            return
        self.source_cache_dir, self.worker_source_paths = _cache_jpg_sources_as_png(
            context,
            self.source_paths,
            self._progress,
            lambda: bool(self.props.delivery_stop_requested),
        )

    def _finish_success(self, result):
        props = self.props
        props.delivery_last_output_dir = result.paths.output_dir
        props.delivery_elapsed_time = result.elapsed_seconds
        props.delivery_info = f"{result.width_px} x {result.height_px} @ {self.settings.ppi} PPI"
        self.report(
            {"INFO"},
            (
                f"交付文件已生成: {result.paths.output_dir} | "
                f"{result.width_px} x {result.height_px} @ {self.settings.ppi} PPI | "
                f"用时 {format_time(result.elapsed_seconds)}"
            ),
        )

    def _cleanup(self, context, *, reset_stop: bool = True) -> None:
        props = self.props
        props.is_delivery_generating = False
        if reset_stop:
            props.delivery_stop_requested = False
        props.delivery_elapsed_time = time.perf_counter() - self.start_time
        if self.original_camera:
            self.scene.camera = self.original_camera
        if self.captured:
            _restore_render_settings(self.scene, self.captured)
        if self.source_cache_dir:
            shutil.rmtree(self.source_cache_dir, ignore_errors=True)
            self.source_cache_dir = None
        if self.wm_progress_active:
            self.wm.progress_end()
            self.wm_progress_active = False

    def _execute_blocking(self, context):
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

        self._init_common(context)

        try:
            self._validate_settings()
            self._begin_props()
            self._prepare_sources(context)
            _render_source_views(context, self.frame_dir, self.invalid_indices, self._progress)
            self._verify_sources()
            self._prepare_worker_sources(context)

            result = generate_delivery_outputs(
                self.worker_source_paths,
                self.output_root,
                self.settings,
                progress_callback=self._progress,
                stop_callback=lambda: bool(props.delivery_stop_requested),
            )

            self._finish_success(result)
            return {"FINISHED"}
        except DeliveryCancelled as exc:
            write_error_log(self.paths.error_log, self.current_stage, exc, self.settings)
            self.report({"WARNING"}, "已停止交付生成")
            return {"CANCELLED"}
        except Exception as exc:
            write_error_log(self.paths.error_log, self.current_stage, exc, self.settings)
            self.report({"ERROR"}, f"交付生成失败: {exc}")
            return {"CANCELLED"}
        finally:
            self._cleanup(context)

    def _execute_modal(self, context):
        global _ACTIVE_DELIVERY_OPERATOR
        props = context.scene.light_field_props

        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}
        if props.is_delivery_generating:
            self.report({"WARNING"}, "已有交付生成任务正在运行")
            return {"CANCELLED"}
        if not bpy.path.abspath(props.output_path):
            self.report({"ERROR"}, "请先设置输出路径")
            return {"CANCELLED"}

        self._init_common(context)
        try:
            self._validate_settings()
            self._begin_props()
            self._prepare_sources(context)
        except Exception as exc:
            self._handle_modal_exception(context, exc)
            return {"CANCELLED"}

        self._state = "render_sources"
        self._render_cursor = 0
        self._worker = None
        self._worker_result = None
        self._worker_error = None
        self._worker_traceback = None
        self._worker_progress = None
        self._worker_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        _ACTIVE_DELIVERY_OPERATOR = self
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        try:
            if self.props.delivery_stop_requested and self._state != "finish_worker":
                self._request_stop()
                if self._state in {"render_sources", "verify_sources"}:
                    raise DeliveryCancelled("用户停止了交付生成")

            if self._state == "render_sources":
                return self._modal_render_sources(context)
            if self._state == "verify_sources":
                self._verify_sources()
                self._prepare_worker_sources(context)
                self._start_worker()
                self._state = "wait_worker"
                return {"RUNNING_MODAL"}
            if self._state == "wait_worker":
                return self._modal_wait_worker(context)
            if self._state == "finish_worker":
                return self._modal_wait_worker(context)
        except Exception as exc:
            return self._handle_modal_exception(context, exc)
        return {"RUNNING_MODAL"}

    def _modal_render_sources(self, context):
        if self._render_cursor >= len(self.invalid_indices):
            self._state = "verify_sources"
            return {"RUNNING_MODAL"}

        camera_index = self.invalid_indices[self._render_cursor]
        completed = self._render_cursor + 1
        total = len(self.invalid_indices)
        self._progress("渲染源视角", completed, total, f"相机 {camera_index + 1}/{self.props.camera_count}")
        _render_single_source_view(context, self.frame_dir, camera_index)
        self._render_cursor += 1
        return {"RUNNING_MODAL"}

    def _request_stop(self):
        self.props.delivery_stop_requested = True
        self.props.delivery_stage = "正在停止交付生成"
        self.props.delivery_info = "正在等待当前步骤安全退出并清理临时文件"
        if hasattr(self, "_stop_event"):
            self._stop_event.set()

    def _start_worker(self):
        worker_stage = "准备后台交织/挂网" if self.settings.write_film_tiff else "准备后台交织"
        self._progress(worker_stage, 0, 1, "UI 可继续响应，可点停止")

        def progress(stage: str, current: int, total: int, info: str = "") -> None:
            with self._worker_lock:
                self._worker_progress = (stage, int(current), max(1, int(total)), info)

        def worker():
            try:
                self._worker_result = generate_delivery_outputs(
                    self.worker_source_paths,
                    self.output_root,
                    self.settings,
                    progress_callback=progress,
                    stop_callback=self._stop_event.is_set,
                )
            except Exception as exc:
                self._worker_error = exc
                self._worker_traceback = traceback.format_exc()

        self._worker = threading.Thread(target=worker, name="LightFieldDeliveryWorker", daemon=True)
        self._worker.start()

    def _modal_wait_worker(self, context):
        with self._worker_lock:
            progress = self._worker_progress
            self._worker_progress = None
        if progress is not None:
            self._progress(*progress)

        if self._worker.is_alive():
            return {"RUNNING_MODAL"}

        if self._worker_error is not None:
            if self._worker_traceback:
                write_error_log(self.paths.error_log, self.current_stage, self._worker_error, self.settings)
            return self._handle_modal_exception(context, self._worker_error)

        self._finish_success(self._worker_result)
        self._finish_modal(context, reset_stop=True)
        return {"FINISHED"}

    def _handle_modal_exception(self, context, exc):
        if isinstance(exc, DeliveryCancelled):
            write_error_log(self.paths.error_log, self.current_stage, exc, self.settings)
            self.report({"WARNING"}, "已停止交付生成")
        else:
            write_error_log(self.paths.error_log, self.current_stage, exc, self.settings)
            self.report({"ERROR"}, f"交付生成失败: {exc}")
        self._finish_modal(context, reset_stop=True)
        return {"CANCELLED"}

    def _finish_modal(self, context, *, reset_stop: bool):
        global _ACTIVE_DELIVERY_OPERATOR
        if hasattr(self, "_timer") and self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if _ACTIVE_DELIVERY_OPERATOR is self:
            _ACTIVE_DELIVERY_OPERATOR = None
        self._cleanup(context, reset_stop=reset_stop)


class LIGHTFIELD_OT_generate_delivery(_DeliveryRunnerMixin, Operator):
    bl_idname = "lightfield.generate_delivery"
    bl_label = "生成当前帧交付文件"
    bl_description = "渲染或复用当前帧多视角源图，并生成最终交织图与 1-bit 菲林 TIFF"
    bl_options = {"REGISTER"}


class LIGHTFIELD_OT_generate_interlaced(_DeliveryRunnerMixin, Operator):
    bl_idname = "lightfield.generate_interlaced"
    bl_label = "生成连续调交织图"
    bl_description = "只生成连续调 interlaced.tif、预览和 manifest，不执行挂网，不输出 film_1bit.tif"
    bl_options = {"REGISTER"}

    def _build_settings(self, context) -> DeliverySettings:
        return _build_delivery_settings(
            context,
            write_interlaced_tiff=True,
            write_film_tiff=False,
        )


class LIGHTFIELD_OT_halftone_interlaced(Operator):
    bl_idname = "lightfield.halftone_interlaced"
    bl_label = "从交织图生成菲林 TIFF"
    bl_description = "读取当前交付目录的 interlaced.tif，使用固定挂网 Profile 生成 film_1bit.tif 和校准报告"
    bl_options = {"REGISTER"}

    def _init_common(self, context):
        self.scene = context.scene
        self.props = context.scene.light_field_props
        self.output_root = bpy.path.abspath(self.props.output_path)
        self.paths = make_delivery_paths(self.output_root, self.scene.frame_current)
        self.current_stage = "准备交织图挂网"
        self.start_time = time.perf_counter()
        self.wm = context.window_manager
        self.wm_progress_active = False
        self._stop_event = threading.Event()
        self._worker = None
        self._worker_error = None
        self._worker_result = None
        self._worker_progress = None
        self._worker_lock = threading.Lock()

    def _target_tiff(self) -> str | None:
        raw_target = self.props.delivery_calibration_target_tiff.strip()
        if not raw_target:
            return None
        target = bpy.path.abspath(raw_target)
        return target if target else None

    def _progress(self, stage: str, current: int, total: int, info: str = "") -> None:
        self.current_stage = stage
        props = self.props
        props.delivery_stage = stage
        props.delivery_progress = int(current)
        props.delivery_progress_total = max(1, int(total))
        props.delivery_info = info
        props.delivery_elapsed_time = time.perf_counter() - self.start_time
        if not self.wm_progress_active:
            self.wm.progress_begin(0, 1000)
            self.wm_progress_active = True
        percent = 0.0 if total <= 0 else max(0.0, min(1.0, float(current) / float(total)))
        self.wm.progress_update(int(percent * 1000))
        _safe_redraw()

    def _begin_props(self) -> None:
        props = self.props
        props.is_delivery_generating = True
        props.delivery_stop_requested = False
        props.delivery_progress = 0
        props.delivery_progress_total = 1
        props.delivery_stage = "准备交织图挂网"
        props.delivery_info = ""
        props.delivery_last_output_dir = ""
        props.delivery_elapsed_time = 0.0

    def _validate(self) -> None:
        if not self.output_root:
            raise DeliveryError("请先设置输出路径")
        if not os.path.exists(self.paths.interlaced_tiff):
            raise DeliveryError(f"缺少连续调交织图: {self.paths.interlaced_tiff}")
        target = self._target_tiff()
        if target and not os.path.exists(target):
            raise DeliveryError(f"校准目标 TIFF 不存在: {target}")
        if self.props.delivery_ppi <= 0:
            raise DeliveryError("PPI 必须大于 0")

    def _run_halftone(self, progress_callback):
        report_path = os.path.join(self.paths.output_dir, "halftone_calibration_report.json")
        return halftone_interlaced_tiff(
            self.paths.interlaced_tiff,
            self.paths.film_1bit_tiff,
            ppi=self.props.delivery_ppi,
            target_tiff=self._target_tiff(),
            calibration_report_json=report_path,
            progress_callback=progress_callback,
            stop_callback=self._stop_event.is_set,
        )

    def _finish_success(self, report: dict):
        self.props.delivery_last_output_dir = self.paths.output_dir
        self.props.delivery_elapsed_time = report.get("elapsed_seconds", 0.0)
        comparison = report.get("comparison")
        if comparison and comparison.get("same_shape"):
            self.props.delivery_info = f"mismatch {comparison['mismatch_ratio'] * 100:.4f}%"
        else:
            self.props.delivery_info = "已生成 film_1bit.tif"
        self.report(
            {"INFO"},
            (
                f"挂网完成: {self.paths.film_1bit_tiff} | "
                f"用时 {format_time(self.props.delivery_elapsed_time)}"
            ),
        )

    def _cleanup(self, context, *, reset_stop: bool = True):
        global _ACTIVE_DELIVERY_OPERATOR
        if hasattr(self, "_timer") and self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if _ACTIVE_DELIVERY_OPERATOR is self:
            _ACTIVE_DELIVERY_OPERATOR = None
        if self.wm_progress_active:
            self.wm.progress_end()
            self.wm_progress_active = False
        self.props.is_delivery_generating = False
        if reset_stop:
            self.props.delivery_stop_requested = False
        self.props.delivery_elapsed_time = time.perf_counter() - self.start_time

    def _request_stop(self):
        self.props.delivery_stop_requested = True
        self.props.delivery_stage = "正在停止交付生成"
        self.props.delivery_info = "正在等待挂网线程安全退出并清理临时文件"
        self._stop_event.set()

    def execute(self, context):
        if bpy.app.background or context.window is None:
            return self._execute_blocking(context)
        return self._execute_modal(context)

    def _execute_blocking(self, context):
        props = context.scene.light_field_props
        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}
        if props.is_delivery_generating:
            self.report({"WARNING"}, "已有交付生成任务正在运行")
            return {"CANCELLED"}
        self._init_common(context)
        try:
            self._validate()
            self._begin_props()
            report = self._run_halftone(self._progress)
            self._finish_success(report)
            return {"FINISHED"}
        except DeliveryCancelled as exc:
            write_error_log(self.paths.error_log, self.current_stage, exc, None)
            self.report({"WARNING"}, "已停止交付生成")
            return {"CANCELLED"}
        except Exception as exc:
            write_error_log(self.paths.error_log, self.current_stage, exc, None)
            self.report({"ERROR"}, f"挂网失败: {exc}")
            return {"CANCELLED"}
        finally:
            self._cleanup(context)

    def _execute_modal(self, context):
        global _ACTIVE_DELIVERY_OPERATOR
        props = context.scene.light_field_props
        if props.is_rendering:
            self.report({"WARNING"}, "已有渲染任务正在运行")
            return {"CANCELLED"}
        if props.is_delivery_generating:
            self.report({"WARNING"}, "已有交付生成任务正在运行")
            return {"CANCELLED"}
        self._init_common(context)
        try:
            self._validate()
            self._begin_props()
        except Exception as exc:
            write_error_log(self.paths.error_log, self.current_stage, exc, None)
            self.report({"ERROR"}, f"挂网失败: {exc}")
            self._cleanup(context)
            return {"CANCELLED"}

        def progress(stage: str, current: int, total: int, info: str = "") -> None:
            with self._worker_lock:
                self._worker_progress = (stage, int(current), max(1, int(total)), info)

        def worker():
            try:
                self._worker_result = self._run_halftone(progress)
            except Exception as exc:
                self._worker_error = exc

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        self._worker = threading.Thread(target=worker, name="LightFieldHalftoneWorker", daemon=True)
        self._worker.start()
        context.window_manager.modal_handler_add(self)
        _ACTIVE_DELIVERY_OPERATOR = self
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        if self.props.delivery_stop_requested:
            self._request_stop()
        with self._worker_lock:
            progress = self._worker_progress
            self._worker_progress = None
        if progress is not None:
            self._progress(*progress)
        if self._worker.is_alive():
            return {"RUNNING_MODAL"}
        if self._worker_error is not None:
            if isinstance(self._worker_error, DeliveryCancelled):
                write_error_log(self.paths.error_log, self.current_stage, self._worker_error, None)
                self.report({"WARNING"}, "已停止交付生成")
            else:
                write_error_log(self.paths.error_log, self.current_stage, self._worker_error, None)
                self.report({"ERROR"}, f"挂网失败: {self._worker_error}")
            self._cleanup(context)
            return {"CANCELLED"}
        self._finish_success(self._worker_result)
        self._cleanup(context)
        return {"FINISHED"}


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
        if _ACTIVE_DELIVERY_OPERATOR is not None:
            _ACTIVE_DELIVERY_OPERATOR._request_stop()
        self.report({"INFO"}, "正在停止交付生成")
        return {"FINISHED"}
