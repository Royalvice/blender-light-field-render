# 光场插件属性定义。

import math
import time

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from ..core.math_utils import (
    calculate_array_width,
    calculate_focal_plane_size,
    calculate_fov_from_focal_length,
    radians_to_degrees,
)
from ..core.delivery import (
    LARGE_OUTPUT_PIXELS,
    SOURCE_UPSCALE_WARNING_FACTOR,
    calculate_delivery_pixels,
    has_source_upscale_warning,
    is_large_output,
)


DEFERRED_APPLY_SECONDS = 0.35
_deferred_apply_scheduled = False
_last_dirty_time = 0.0


def _get_control():
    from ..core.light_field_control import get_light_field_control

    return get_light_field_control()


def sync_render_resolution(scene):
    props = scene.light_field_props
    scene.render.resolution_x = int(props.resolution_x)
    scene.render.resolution_y = int(props.resolution_y)
    props.render_settings_dirty = False


def _schedule_deferred_apply():
    global _deferred_apply_scheduled, _last_dirty_time

    _last_dirty_time = time.monotonic()
    if _deferred_apply_scheduled:
        return

    _deferred_apply_scheduled = True
    bpy.app.timers.register(_deferred_apply, first_interval=DEFERRED_APPLY_SECONDS)


def _deferred_apply():
    global _deferred_apply_scheduled

    if time.monotonic() - _last_dirty_time < DEFERRED_APPLY_SECONDS:
        return DEFERRED_APPLY_SECONDS

    _deferred_apply_scheduled = False
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not hasattr(scene, "light_field_props"):
        return None

    props = scene.light_field_props
    if not props.auto_apply_parameters:
        return None

    if props.render_settings_dirty:
        sync_render_resolution(scene)

    if props.geometry_dirty:
        from ..operators.create_ops import apply_light_field_parameters

        apply_light_field_parameters(scene)

    return None


def mark_geometry_dirty(self, context):
    self.geometry_dirty = True
    max_index = max(0, self.camera_count - 1)
    if self.active_camera_index > max_index:
        self["active_camera_index"] = max_index

    if self.auto_apply_parameters:
        _schedule_deferred_apply()


def mark_render_settings_dirty(self, context):
    self.render_settings_dirty = True
    if self.auto_apply_parameters:
        _schedule_deferred_apply()


def update_active_camera(self, context):
    max_index = max(0, self.camera_count - 1)
    if self.active_camera_index > max_index:
        self["active_camera_index"] = max_index
        return

    control = _get_control()
    if control.is_created:
        control.set_active_camera(self.active_camera_index)


class LightFieldProperties(PropertyGroup):
    """Properties for the light-field camera-array add-on."""

    camera_count: IntProperty(
        name="相机数量",
        description="光场阵列中的相机/视角总数",
        default=60,
        min=2,
        max=200,
        update=mark_geometry_dirty,
    )

    focal_distance: FloatProperty(
        name="焦平面距离",
        description="相机阵列到焦平面的距离，单位为米",
        default=10.0,
        min=0.1,
        max=1000.0,
        unit="LENGTH",
        update=mark_geometry_dirty,
    )

    opening_angle: FloatProperty(
        name="阵列张角",
        description="相机阵列的有效水平角覆盖范围，单位为度",
        default=11.4,
        min=0.1,
        max=120.0,
        update=mark_geometry_dirty,
    )

    depth_range: FloatProperty(
        name="景深范围",
        description="焦平面前后的显示深度体范围，单位为米",
        default=3.0,
        min=0.1,
        max=100.0,
        unit="LENGTH",
        update=mark_geometry_dirty,
    )

    focal_length: FloatProperty(
        name="焦距",
        description="相机镜头焦距 f_L，单位为毫米",
        default=50.0,
        min=1.0,
        max=500.0,
        update=mark_geometry_dirty,
    )

    sensor_width: FloatProperty(
        name="传感器宽度",
        description="相机传感器宽度 S_w，单位为毫米",
        default=36.0,
        min=1.0,
        max=100.0,
        update=mark_geometry_dirty,
    )

    resolution_x: IntProperty(
        name="宽度",
        description="输出图像宽度，单位为像素",
        default=1920,
        min=1,
        max=16384,
        update=mark_render_settings_dirty,
    )

    resolution_y: IntProperty(
        name="高度",
        description="输出图像高度，单位为像素",
        default=1080,
        min=1,
        max=16384,
        update=mark_render_settings_dirty,
    )

    output_path: StringProperty(
        name="输出路径",
        description="渲染输出目录",
        default="//light_field_output/",
        subtype="DIR_PATH",
    )

    output_file_format: EnumProperty(
        name="输出格式",
        description="光场渲染输出的图像格式",
        items=[
            ("PNG", "PNG", "连续调 PNG 输出"),
            ("TIFF", "TIFF", "Blender 输出的连续调 TIFF"),
            (
                "FILM_TIFF",
                "1-bit 菲林 TIFF",
                "先渲染连续调源图，再导出挂网后的 1-bit TIFF",
            ),
        ],
        default="PNG",
        update=mark_render_settings_dirty,
    )

    keep_continuous_source: BoolProperty(
        name="保留连续调源图",
        description="保留用于生成 1-bit 菲林 TIFF 的临时连续调 PNG 源图",
        default=False,
        update=mark_render_settings_dirty,
    )

    film_halftone_method: EnumProperty(
        name="挂网方式",
        description="1-bit 菲林 TIFF 导出的挂网策略",
        items=[
            ("FM", "FM / 误差扩散", "固定大小网点按密度分布，通常更适合光栅/光场流程"),
            ("AM", "AM / 聚集网点", "传统调幅挂网，由 LPI、网角和网点形状控制"),
        ],
        default="AM",
        update=mark_render_settings_dirty,
    )

    film_lpi: IntProperty(
        name="LPI",
        description="AM 挂网线数，单位为每英寸线数",
        default=200,
        min=30,
        max=600,
        update=mark_render_settings_dirty,
    )

    film_dpi: IntProperty(
        name="DPI",
        description="菲林输出分辨率元数据，也是 AM 网点单元尺寸的计算基础",
        default=2400,
        min=300,
        max=9600,
        update=mark_render_settings_dirty,
    )

    film_angle: FloatProperty(
        name="网角",
        description="AM 挂网角度，单位为度",
        default=45.0,
        min=-90.0,
        max=90.0,
        update=mark_render_settings_dirty,
    )

    film_dot_shape: EnumProperty(
        name="网点形状",
        description="AM 挂网的网点形状",
        items=[
            ("ROUND", "圆形", "圆形聚集网点"),
            ("DIAMOND", "菱形", "菱形聚集网点"),
            ("ELLIPSE", "椭圆", "椭圆形聚集网点"),
        ],
        default="ROUND",
        update=mark_render_settings_dirty,
    )

    film_gamma: FloatProperty(
        name="Gamma",
        description="1-bit 挂网转换前应用的亮度 Gamma 校正",
        default=1.0,
        min=0.1,
        max=5.0,
        update=mark_render_settings_dirty,
    )

    auto_apply_parameters: BoolProperty(
        name="拖动结束后自动应用",
        description="滑条拖动停止后自动应用相机阵列参数；默认关闭以避免界面卡顿",
        default=False,
    )

    active_camera_index: IntProperty(
        name="当前相机",
        description="当前激活的相机/视角序号",
        default=30,
        min=0,
        soft_max=199,
        update=update_active_camera,
    )

    is_rendering: BoolProperty(name="正在渲染", default=False)
    render_progress: IntProperty(name="渲染进度", default=0, min=0)
    render_info: StringProperty(name="渲染信息", default="")
    render_elapsed_time: FloatProperty(name="已用时间", default=0.0, min=0.0)
    render_start_time: FloatProperty(name="开始时间", default=0.0)

    geometry_dirty: BoolProperty(
        name="几何参数待更新",
        default=False,
        options={"HIDDEN"},
    )

    render_settings_dirty: BoolProperty(
        name="输出设置待更新",
        default=False,
        options={"HIDDEN"},
    )

    frame_start: IntProperty(
        name="开始帧",
        description="动画渲染的第一帧",
        default=1,
        min=0,
    )

    frame_end: IntProperty(
        name="结束帧",
        description="动画渲染的最后一帧",
        default=250,
        min=0,
    )

    delivery_width_mm: FloatProperty(
        name="交付宽度",
        description="最终交织/菲林交付文件的物理宽度，单位为毫米",
        default=0.0,
        min=0.0,
        precision=3,
        subtype="DISTANCE",
    )

    delivery_height_mm: FloatProperty(
        name="交付高度",
        description="最终交织/菲林交付文件的物理高度，单位为毫米",
        default=0.0,
        min=0.0,
        precision=3,
        subtype="DISTANCE",
    )

    delivery_ppi: IntProperty(
        name="PPI",
        description="最终交付文件的像素密度；也会写入 TIFF DPI 元数据",
        default=0,
        min=0,
        max=9600,
    )

    delivery_confirm_large_output: BoolProperty(
        name="确认生成大图",
        description="最终像素超过 100MP 时需要勾选此项才能生成",
        default=False,
    )

    delivery_write_interlaced_tiff: BoolProperty(
        name="输出连续调交织 TIFF",
        description="同时写出完整 RGB interlaced.tif；大图会额外写入数 GB 数据，关闭后只输出菲林 TIFF、预览和 manifest",
        default=False,
    )

    interlace_pe: FloatProperty(
        name="PE",
        description="交织公式原始 PE 参数，沿用现有 interlace_taichi.py 口径",
        default=16.7240,
        min=0.0001,
        precision=4,
    )

    interlace_angle: FloatProperty(
        name="Angle",
        description="交织公式倾角，界面使用度，内部换算为弧度",
        default=math.degrees(0.106395),
        min=-89.0,
        max=89.0,
        precision=4,
    )

    interlace_offset: FloatProperty(
        name="Offset",
        description="交织公式原始 Offset 参数",
        default=12.5,
        precision=4,
    )

    interlace_reverse_views: BoolProperty(
        name="反转视角顺序",
        description="把 view 0..N-1 映射为 camera_N-1..camera_000",
        default=False,
    )

    is_delivery_generating: BoolProperty(name="正在生成交付文件", default=False)
    delivery_progress: IntProperty(name="交付进度", default=0, min=0)
    delivery_progress_total: IntProperty(name="交付总量", default=0, min=0)
    delivery_stage: StringProperty(name="交付阶段", default="")
    delivery_info: StringProperty(name="交付信息", default="")
    delivery_elapsed_time: FloatProperty(name="交付用时", default=0.0, min=0.0)
    delivery_stop_requested: BoolProperty(
        name="停止交付请求",
        default=False,
        options={"HIDDEN"},
    )
    delivery_last_output_dir: StringProperty(name="最近交付目录", default="")

    def get_array_width(self) -> float:
        return calculate_array_width(self.opening_angle, self.focal_distance)

    def get_camera_spacing(self) -> float:
        if self.camera_count <= 1:
            return 0.0
        return self.get_array_width() / (self.camera_count - 1)

    def get_fov_x_deg(self) -> float:
        fov_rad = calculate_fov_from_focal_length(self.focal_length, self.sensor_width)
        return radians_to_degrees(fov_rad)

    def get_focal_plane_size(self) -> tuple:
        fov_rad = calculate_fov_from_focal_length(self.focal_length, self.sensor_width)
        aspect = self.resolution_x / self.resolution_y if self.resolution_y > 0 else 16 / 9
        return calculate_focal_plane_size(self.focal_distance, fov_rad, aspect)

    def get_delivery_pixel_size(self) -> tuple:
        try:
            return calculate_delivery_pixels(self.delivery_width_mm, self.delivery_height_mm, self.delivery_ppi)
        except ValueError:
            return 0, 0

    def is_delivery_large_output(self) -> bool:
        width, height = self.get_delivery_pixel_size()
        if width <= 0 or height <= 0:
            return False
        return is_large_output(width, height, LARGE_OUTPUT_PIXELS)

    def has_delivery_source_upscale_warning(self) -> bool:
        width, height = self.get_delivery_pixel_size()
        if width <= 0 or height <= 0:
            return False
        return has_source_upscale_warning(
            width,
            height,
            self.resolution_x,
            self.resolution_y,
            SOURCE_UPSCALE_WARNING_FACTOR,
        )


def register():
    bpy.utils.register_class(LightFieldProperties)
    bpy.types.Scene.light_field_props = bpy.props.PointerProperty(type=LightFieldProperties)


def unregister():
    del bpy.types.Scene.light_field_props
    bpy.utils.unregister_class(LightFieldProperties)
