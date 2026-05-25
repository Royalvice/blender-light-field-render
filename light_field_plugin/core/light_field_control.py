# Light Field Control (Revised v2.4)
# 使用 Empty 对象进行可视化，避免渲染问题

import bpy
from typing import Optional, Tuple

from .camera_array import CameraArrayManager
from .math_utils import (
    calculate_array_width,
    calculate_focal_plane_size,
    calculate_fov_from_focal_length,
)


class LightFieldControl:
    """光场相机阵列控制器"""
    
    CONTROL_NAME = "LightField_Control"
    FOCAL_PLANE_NAME = "LightField_FocalPlane"
    DEPTH_BBOX_NAME = "LightField_DepthBBox"
    
    def __init__(self):
        self._control_object: Optional[bpy.types.Object] = None
        self._focal_plane: Optional[bpy.types.Object] = None
        self._depth_bbox: Optional[bpy.types.Object] = None
        self._camera_manager: Optional[CameraArrayManager] = None
    
    @property
    def control_object(self):
        return self._control_object
    
    @property
    def focal_plane(self):
        return self._focal_plane
    
    @property
    def depth_bbox(self):
        return self._depth_bbox
    
    @property
    def camera_manager(self):
        return self._camera_manager
    
    @property
    def is_created(self) -> bool:
        if self._control_object is None:
            return False
        try:
            _ = self._control_object.name
            return True
        except ReferenceError:
            self._control_object = None
            return False
    
    def create(
        self,
        camera_count: int = 60,
        focal_distance: float = 10.0,
        opening_angle_deg: float = 11.4,
        focal_length_mm: float = 50.0,
        sensor_width_mm: float = 36.0,
        depth_range: float = 3.0,
        location: Tuple[float, float, float] = (0, 0, 0)
    ) -> bool:
        """创建光场相机系统"""
        if self.is_created:
            return False
        
        props = bpy.context.scene.light_field_props
        
        # 同步分辨率：从场景获取当前分辨率
        scene = bpy.context.scene
        res_x = scene.render.resolution_x
        res_y = scene.render.resolution_y
        props.resolution_x = res_x
        props.resolution_y = res_y
        
        # 计算阵列宽度
        array_width = calculate_array_width(opening_angle_deg, focal_distance)
        
        # 计算焦平面尺寸
        fov_x_rad = calculate_fov_from_focal_length(focal_length_mm, sensor_width_mm)
        aspect = res_x / res_y if res_y > 0 else 16/9
        plane_width, plane_height = calculate_focal_plane_size(focal_distance, fov_x_rad, aspect)
        
        # 创建控制器 Empty
        self._control_object = bpy.data.objects.new(self.CONTROL_NAME, None)
        self._control_object.empty_display_type = 'ARROWS'
        self._control_object.empty_display_size = 1.0
        self._control_object.location = location
        bpy.context.collection.objects.link(self._control_object)
        
        # 创建焦平面 (使用 Empty CUBE，扁平化)
        self._create_focal_plane(plane_width, plane_height)
        
        # 创建景深盒 (使用 Empty CUBE)
        self._create_depth_bbox(plane_width, plane_height, depth_range)
        
        # 创建相机阵列
        self._camera_manager = CameraArrayManager(self._control_object)
        self._camera_manager.create_cameras(
            count=camera_count,
            focal_distance=focal_distance,
            array_width=array_width,
            focal_length_mm=focal_length_mm,
            sensor_width_mm=sensor_width_mm
        )
        
        # 设置场景主相机为中心相机
        center_index = camera_count // 2
        self.set_active_camera(center_index)
        
        return True
    
    def _create_focal_plane(self, width: float, height: float) -> None:
        """创建焦平面可视化对象 (使用 Empty 对象，永不渲染)"""
        self._focal_plane = bpy.data.objects.new(self.FOCAL_PLANE_NAME, None)
        self._focal_plane.empty_display_type = 'CUBE'
        self._focal_plane.empty_display_size = 1.0
        
        # Empty CUBE 的显示范围是 -1 到 +1（总尺寸 = 2）
        # 所以 scale = (W/2, H/2, ...) 才能得到 W x H 的显示尺寸
        self._focal_plane.scale = (width / 2, height / 2, 0.01)
        
        self._focal_plane.location = (0, 0, 0)
        self._focal_plane.parent = self._control_object
        self._focal_plane.color = (0.2, 0.8, 0.2, 1.0)
        
        bpy.context.collection.objects.link(self._focal_plane)
    
    def _create_depth_bbox(self, width: float, height: float, depth: float) -> None:
        """创建景深盒可视化对象 (使用 Empty 对象，永不渲染)"""
        self._depth_bbox = bpy.data.objects.new(self.DEPTH_BBOX_NAME, None)
        self._depth_bbox.empty_display_type = 'CUBE'
        self._depth_bbox.empty_display_size = 1.0
        
        # Empty CUBE: scale 需要除以 2
        self._depth_bbox.scale = (width / 2, height / 2, depth / 2)
        
        self._depth_bbox.location = (0, 0, 0)
        self._depth_bbox.parent = self._control_object
        self._depth_bbox.color = (1.0, 0.5, 0.1, 1.0)
        
        bpy.context.collection.objects.link(self._depth_bbox)
    
    def update(
        self,
        camera_count: Optional[int] = None,
        focal_distance: Optional[float] = None,
        opening_angle_deg: Optional[float] = None,
        focal_length_mm: Optional[float] = None,
        sensor_width_mm: Optional[float] = None
    ) -> None:
        """更新光场相机系统参数"""
        if not self.is_created or not self._camera_manager:
            return
        
        props = bpy.context.scene.light_field_props
        
        count = camera_count if camera_count is not None else props.camera_count
        distance = focal_distance if focal_distance is not None else props.focal_distance
        angle = opening_angle_deg if opening_angle_deg is not None else props.opening_angle
        focal_len = focal_length_mm if focal_length_mm is not None else props.focal_length
        sensor_w = sensor_width_mm if sensor_width_mm is not None else props.sensor_width
        
        array_width = calculate_array_width(angle, distance)
        
        if count != self._camera_manager.camera_count:
            self._camera_manager.create_cameras(
                count=count,
                focal_distance=distance,
                array_width=array_width,
                focal_length_mm=focal_len,
                sensor_width_mm=sensor_w
            )
            max_index = max(0, count - 1)
            if props.active_camera_index > max_index:
                props["active_camera_index"] = max_index
            self.set_active_camera(props.active_camera_index)
        else:
            self._camera_manager.update_cameras(
                focal_distance=distance,
                array_width=array_width,
                focal_length_mm=focal_len,
                sensor_width_mm=sensor_w
            )
        
        self.update_visuals()
    
    def update_visuals(self) -> None:
        """更新焦平面和景深盒几何"""
        if not self.is_created:
            return
        
        props = bpy.context.scene.light_field_props
        fov_x_rad = calculate_fov_from_focal_length(props.focal_length, props.sensor_width)
        aspect = props.resolution_x / props.resolution_y if props.resolution_y > 0 else 16/9
        width, height = calculate_focal_plane_size(props.focal_distance, fov_x_rad, aspect)
        
        if self._focal_plane:
            try:
                # Empty CUBE: scale = size / 2
                self._focal_plane.scale = (width / 2, height / 2, 0.01)
            except ReferenceError:
                pass
        
        if self._depth_bbox:
            try:
                self._depth_bbox.scale = (width / 2, height / 2, props.depth_range / 2)
            except ReferenceError:
                pass
    
    def update_depth_box(self) -> None:
        if not self.is_created or not self._depth_bbox:
            return
        props = bpy.context.scene.light_field_props
        try:
            scale = self._depth_bbox.scale
            self._depth_bbox.scale = (scale[0], scale[1], props.depth_range / 2)
        except ReferenceError:
            pass
    
    def delete(self) -> None:
        if self._camera_manager:
            self._camera_manager.delete_cameras()
            self._camera_manager = None
        
        for obj in [self._focal_plane, self._depth_bbox, self._control_object]:
            if obj:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except ReferenceError:
                    pass
        
        self._focal_plane = None
        self._depth_bbox = None
        self._control_object = None
    
    def set_active_camera(self, index: int) -> bool:
        if not self._camera_manager:
            return False
        camera = self._camera_manager.get_camera(index)
        if camera:
            bpy.context.scene.camera = camera
            self._camera_manager.set_active_camera_display(index)
            return True
        return False
    
    @classmethod
    def find_existing(cls):
        control_obj = bpy.data.objects.get(cls.CONTROL_NAME)
        if not control_obj:
            return None
        
        instance = cls()
        instance._control_object = control_obj
        instance._focal_plane = bpy.data.objects.get(cls.FOCAL_PLANE_NAME)
        instance._depth_bbox = bpy.data.objects.get(cls.DEPTH_BBOX_NAME)
        instance._camera_manager = CameraArrayManager(control_obj)
        instance._camera_manager.collect_existing_cameras()
        return instance


_light_field_control = None

def get_light_field_control():
    """获取光场控制器单例
    
    每次调用都会检查当前引用是否有效，
    如果无效则尝试从场景中恢复（处理撤销操作）
    """
    global _light_field_control
    
    # 检查当前单例是否有效
    if _light_field_control is not None:
        if _light_field_control.is_created:
            return _light_field_control
        else:
            # 引用无效，尝试从场景恢复
            _light_field_control = None
    
    # 尝试从场景中查找已存在的控制器
    existing = LightFieldControl.find_existing()
    if existing is not None:
        _light_field_control = existing
        return _light_field_control
    
    # 创建新的空控制器
    _light_field_control = LightFieldControl()
    return _light_field_control

def reset_light_field_control():
    """重置光场控制器单例"""
    global _light_field_control
    _light_field_control = None
