# Camera Array Manager
# 相机阵列管理器 (Revised v2.1)

import bpy
import math
from typing import List, Optional

from .math_utils import (
    calculate_camera_position_linear,
    calculate_shift_x_blender,
    calculate_fov_from_focal_length,
)


class CameraArrayManager:
    """相机阵列管理器 - 使用线性均匀分布"""
    
    CAMERA_PREFIX = "LF_Camera_"
    
    def __init__(self, parent_object: bpy.types.Object):
        self.parent = parent_object
        self._cameras: List[bpy.types.Object] = []
    
    @property
    def cameras(self) -> List[bpy.types.Object]:
        return self._cameras
    
    @property
    def camera_count(self) -> int:
        return len(self._cameras)
    
    def create_cameras(
        self,
        count: int,
        focal_distance: float,
        array_width: float,
        focal_length_mm: float,
        sensor_width_mm: float = 36.0
    ) -> List[bpy.types.Object]:
        """
        创建相机阵列
        
        Args:
            count: 相机数量 N
            focal_distance: 焦平面距离 d_f (米)
            array_width: 阵列宽度 W_array (米)
            focal_length_mm: 焦距 f_L (mm)
            sensor_width_mm: 传感器宽度 S_w (mm)
        """
        self.delete_cameras()
        
        fov_x_rad = calculate_fov_from_focal_length(focal_length_mm, sensor_width_mm)
        
        for i in range(count):
            camera = self._create_single_camera(
                index=i,
                count=count,
                focal_distance=focal_distance,
                array_width=array_width,
                focal_length_mm=focal_length_mm,
                sensor_width_mm=sensor_width_mm,
                fov_x_rad=fov_x_rad
            )
            self._cameras.append(camera)
        
        return self._cameras
    
    def _create_single_camera(
        self,
        index: int,
        count: int,
        focal_distance: float,
        array_width: float,
        focal_length_mm: float,
        sensor_width_mm: float,
        fov_x_rad: float
    ) -> bpy.types.Object:
        """创建单个相机"""
        # 创建相机数据
        camera_data = bpy.data.cameras.new(name=f"{self.CAMERA_PREFIX}{index:03d}")
        camera_data.lens = focal_length_mm
        camera_data.sensor_width = sensor_width_mm
        camera_data.sensor_fit = 'HORIZONTAL'
        camera_data.display_size = 0.0  # 默认隐藏视锥体
        
        # 创建相机对象
        camera_obj = bpy.data.objects.new(
            name=f"{self.CAMERA_PREFIX}{index:03d}", 
            object_data=camera_data
        )
        
        # 计算位置 (线性均匀分布)
        x, y, z = calculate_camera_position_linear(index, count, array_width)
        camera_obj.location = (x, y, -focal_distance)
        
        # 相机朝向 +Z (绕 X 轴旋转 180°)
        camera_obj.rotation_euler = (math.pi, 0, 0)
        
        # 计算 Blender shift_x
        shift_x = calculate_shift_x_blender(x, focal_distance, fov_x_rad)
        camera_data.shift_x = shift_x
        
        # 设置父对象
        camera_obj.parent = self.parent
        
        # 添加到场景
        bpy.context.collection.objects.link(camera_obj)
        
        # 锁定变换
        camera_obj.lock_location = (True, True, True)
        camera_obj.lock_rotation = (True, True, True)
        camera_obj.lock_scale = (True, True, True)
        
        return camera_obj
    
    def update_cameras(
        self,
        focal_distance: float,
        array_width: float,
        focal_length_mm: float,
        sensor_width_mm: float = 36.0
    ) -> None:
        """更新所有相机的位置和参数"""
        self._cameras = [c for c in self._cameras if self._is_valid_object(c)]
        
        if not self._cameras:
            return
        
        count = len(self._cameras)
        fov_x_rad = calculate_fov_from_focal_length(focal_length_mm, sensor_width_mm)
        
        for i, camera_obj in enumerate(self._cameras):
            if not self._is_valid_object(camera_obj):
                continue
            
            # 解锁位置
            camera_obj.lock_location = (False, False, False)
            
            # 更新位置
            x, y, z = calculate_camera_position_linear(i, count, array_width)
            camera_obj.location = (x, y, -focal_distance)
            
            # 重新锁定
            camera_obj.lock_location = (True, True, True)
            
            # 更新相机数据
            camera_data = camera_obj.data
            camera_data.lens = focal_length_mm
            camera_data.sensor_width = sensor_width_mm
            
            # 更新 shift_x
            shift_x = calculate_shift_x_blender(x, focal_distance, fov_x_rad)
            camera_data.shift_x = shift_x
    
    def _is_valid_object(self, obj) -> bool:
        try:
            _ = obj.name
            return True
        except ReferenceError:
            return False
    
    def delete_cameras(self) -> None:
        """删除所有相机"""
        for camera_obj in self._cameras:
            if not self._is_valid_object(camera_obj):
                continue
            try:
                camera_data = camera_obj.data
                bpy.data.objects.remove(camera_obj, do_unlink=True)
                if camera_data and camera_data.users == 0:
                    bpy.data.cameras.remove(camera_data)
            except ReferenceError:
                pass
        self._cameras.clear()
    
    def get_camera(self, index: int) -> Optional[bpy.types.Object]:
        if 0 <= index < len(self._cameras):
            cam = self._cameras[index]
            if self._is_valid_object(cam):
                return cam
        return None
    
    def set_active_camera_display(self, active_index: int) -> None:
        """只显示激活相机的视锥体"""
        for i, camera_obj in enumerate(self._cameras):
            if not self._is_valid_object(camera_obj):
                continue
            camera_obj.data.display_size = 1.0 if i == active_index else 0.0
    
    def collect_existing_cameras(self) -> None:
        """从场景中收集已存在的光场相机"""
        self._cameras.clear()
        if not self.parent:
            return
        
        cameras_with_index = []
        for obj in bpy.data.objects:
            if (obj.type == 'CAMERA' and 
                obj.name.startswith(self.CAMERA_PREFIX) and
                obj.parent == self.parent):
                try:
                    index = int(obj.name[len(self.CAMERA_PREFIX):])
                    cameras_with_index.append((index, obj))
                except ValueError:
                    continue
        
        cameras_with_index.sort(key=lambda x: x[0])
        self._cameras = [cam for _, cam in cameras_with_index]
