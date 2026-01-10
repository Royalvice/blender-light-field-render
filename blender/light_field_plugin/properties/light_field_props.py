# Light Field Properties
# 光场属性定义 (Revised v2.2)
#
# 物理几何参数：N, d_f, θ_array (输入), D_cube
# 相机内参：f_L, S_w
# 计算值：W_array, Δx, FOV_x, W_f, H_f

import bpy
from bpy.props import (
    IntProperty,
    FloatProperty,
    StringProperty,
    BoolProperty,
)
from bpy.types import PropertyGroup

from ..core.math_utils import (
    calculate_array_width,
    calculate_focal_plane_size,
    calculate_fov_from_focal_length,
    degrees_to_radians,
    radians_to_degrees,
)


def _get_control():
    from ..core.light_field_control import get_light_field_control
    return get_light_field_control()


def update_camera_count(self, context):
    control = _get_control()
    if control.is_created:
        control.update(camera_count=self.camera_count)
    max_index = max(0, self.camera_count - 1)
    if self.active_camera_index > max_index:
        self["active_camera_index"] = max_index


def update_focal_distance(self, context):
    control = _get_control()
    if control.is_created:
        control.update(focal_distance=self.focal_distance)
        control.update_visuals()


def update_opening_angle(self, context):
    """阵列张角更新回调"""
    control = _get_control()
    if control.is_created:
        control.update(opening_angle_deg=self.opening_angle)


def update_focal_length(self, context):
    control = _get_control()
    if control.is_created:
        control.update(focal_length_mm=self.focal_length)
        control.update_visuals()


def update_sensor_width(self, context):
    control = _get_control()
    if control.is_created:
        control.update(sensor_width_mm=self.sensor_width)
        control.update_visuals()


def update_depth_range(self, context):
    control = _get_control()
    if control.is_created:
        control.update_depth_box()


def update_resolution(self, context):
    context.scene.render.resolution_x = self.resolution_x
    context.scene.render.resolution_y = self.resolution_y
    control = _get_control()
    if control.is_created:
        control.update_visuals()


def update_active_camera(self, context):
    max_index = max(0, self.camera_count - 1)
    if self.active_camera_index > max_index:
        self["active_camera_index"] = max_index
        return
    control = _get_control()
    if control.is_created:
        control.set_active_camera(self.active_camera_index)


class LightFieldProperties(PropertyGroup):
    """光场相机阵列属性组"""
    
    # ========== 物理几何参数 ==========
    camera_count: IntProperty(
        name="相机数量",
        description="光场相机阵列中的相机总数 N",
        default=60,
        min=2,
        max=200,
        update=update_camera_count
    )
    
    focal_distance: FloatProperty(
        name="焦平面距离",
        description="相机阵列到焦平面的距离 d_f（米）",
        default=10.0,
        min=0.1,
        max=1000.0,
        unit='LENGTH',
        update=update_focal_distance
    )
    
    opening_angle: FloatProperty(
        name="阵列张角",
        description="阵列覆盖的有效角度 θ_array（度）",
        default=11.4,
        min=0.1,
        max=120.0,
        update=update_opening_angle
    )
    
    depth_range: FloatProperty(
        name="景深范围",
        description="显示立方体的深度 D_cube（米）",
        default=3.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
        update=update_depth_range
    )
    
    # ========== 相机内参 ==========
    focal_length: FloatProperty(
        name="焦距",
        description="镜头焦距 f_L（毫米）",
        default=50.0,
        min=1.0,
        max=500.0,
        update=update_focal_length
    )
    
    sensor_width: FloatProperty(
        name="传感器宽度",
        description="传感器物理宽度 S_w（毫米）",
        default=36.0,
        min=1.0,
        max=100.0,
        update=update_sensor_width
    )
    
    # ========== 渲染设置 ==========
    resolution_x: IntProperty(
        name="分辨率 X",
        description="渲染输出的水平分辨率",
        default=1920,
        min=1,
        max=16384,
        update=update_resolution
    )
    
    resolution_y: IntProperty(
        name="分辨率 Y",
        description="渲染输出的垂直分辨率",
        default=1080,
        min=1,
        max=16384,
        update=update_resolution
    )
    
    output_path: StringProperty(
        name="输出路径",
        description="渲染结果保存路径",
        default="//light_field_output/",
        subtype='DIR_PATH'
    )
    
    # ========== 预览控制 ==========
    active_camera_index: IntProperty(
        name="当前相机",
        description="当前激活的相机索引",
        default=30,
        min=0,
        soft_max=199,
        update=update_active_camera
    )
    
    # ========== 渲染状态 ==========
    is_rendering: BoolProperty(name="正在渲染", default=False)
    render_progress: IntProperty(name="渲染进度", default=0, min=0)
    render_info: StringProperty(name="渲染信息", default="")
    render_elapsed_time: FloatProperty(name="已用时间", default=0.0, min=0.0)
    render_start_time: FloatProperty(name="开始时间", default=0.0)
    
    # ========== 动画帧范围 ==========
    frame_start: IntProperty(
        name="开始帧",
        description="动画渲染的开始帧",
        default=1,
        min=0
    )
    
    frame_end: IntProperty(
        name="结束帧",
        description="动画渲染的结束帧",
        default=250,
        min=0
    )
    
    # ========== 计算属性 ==========
    def get_array_width(self) -> float:
        """获取阵列宽度（米）- 计算值"""
        return calculate_array_width(self.opening_angle, self.focal_distance)
    
    def get_camera_spacing(self) -> float:
        """获取相机间距（米）- 计算值"""
        if self.camera_count <= 1:
            return 0.0
        array_width = self.get_array_width()
        return array_width / (self.camera_count - 1)
    
    def get_fov_x_deg(self) -> float:
        """获取水平视场角（度）- 计算值"""
        fov_rad = calculate_fov_from_focal_length(self.focal_length, self.sensor_width)
        return radians_to_degrees(fov_rad)
    
    def get_focal_plane_size(self) -> tuple:
        """获取焦平面尺寸（米）- 计算值"""
        fov_rad = calculate_fov_from_focal_length(self.focal_length, self.sensor_width)
        aspect = self.resolution_x / self.resolution_y if self.resolution_y > 0 else 16/9
        return calculate_focal_plane_size(self.focal_distance, fov_rad, aspect)


def register():
    bpy.utils.register_class(LightFieldProperties)
    bpy.types.Scene.light_field_props = bpy.props.PointerProperty(type=LightFieldProperties)


def unregister():
    del bpy.types.Scene.light_field_props
    bpy.utils.unregister_class(LightFieldProperties)
