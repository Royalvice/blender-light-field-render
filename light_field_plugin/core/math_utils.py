# Math utilities for light field camera array calculations
# 光场相机阵列数学计算工具 (Revised v2.0)
#
# 基于修正后的规范：
# 1. 相机位置：线性均匀分布
# 2. Blender shift_x = -x_i / (2 * d_f * tan(FOV_x/2))

import math
from typing import Tuple


def degrees_to_radians(degrees: float) -> float:
    """将角度转换为弧度"""
    return degrees * math.pi / 180.0


def radians_to_degrees(radians: float) -> float:
    """将弧度转换为角度"""
    return radians * 180.0 / math.pi


def calculate_camera_spacing(array_width: float, camera_count: int) -> float:
    """
    计算相机间距 Δx
    
    Δx = W_array / (N - 1)
    
    Args:
        array_width: 相机阵列总宽 W_array (米)
        camera_count: 相机数量 N
    
    Returns:
        相机间距 Δx (米)
    """
    if camera_count <= 1:
        return 0.0
    return array_width / (camera_count - 1)


def calculate_array_width(opening_angle_deg: float, focal_distance: float) -> float:
    """
    从阵列张角计算阵列宽度
    
    W_array = 2 * d_f * tan(θ_array / 2)
    
    Args:
        opening_angle_deg: 阵列张角 θ_array (度)
        focal_distance: 焦平面距离 d_f (米)
    
    Returns:
        阵列宽度 W_array (米)
    """
    if focal_distance <= 0:
        return 0.0
    theta_rad = degrees_to_radians(opening_angle_deg)
    return 2.0 * focal_distance * math.tan(theta_rad / 2.0)


def calculate_opening_angle(array_width: float, focal_distance: float) -> float:
    """
    从阵列宽度计算阵列张角 θ_array
    
    θ_array = 2 * arctan(W_array / (2 * d_f))
    
    Args:
        array_width: 相机阵列总宽 W_array (米)
        focal_distance: 焦平面距离 d_f (米)
    
    Returns:
        阵列张角 θ_array (弧度)
    """
    if focal_distance <= 0:
        return 0.0
    return 2.0 * math.atan(array_width / (2.0 * focal_distance))


def calculate_camera_position_linear(
    camera_index: int,
    camera_count: int,
    array_width: float
) -> Tuple[float, float, float]:
    """
    计算第 i 个相机的位置 (线性均匀分布)
    
    x_i = (i - (N-1)/2) * Δx
    
    其中 Δx = W_array / (N-1)
    
    Camera 0 在最左侧 (-x), Camera N-1 在最右侧 (+x)
    
    Args:
        camera_index: 相机索引 i (0 到 N-1)
        camera_count: 相机总数 N
        array_width: 阵列总宽 W_array (米)
    
    Returns:
        (x, y, z) 相机位置，z=0 表示相机在世界原点平面
    """
    if camera_count <= 1:
        return (0.0, 0.0, 0.0)
    
    # 计算间距
    delta_x = array_width / (camera_count - 1)
    
    # 线性分布: i=0 在最左侧, i=N-1 在最右侧
    x = (camera_index - (camera_count - 1) / 2.0) * delta_x
    
    return (x, 0.0, 0.0)


def calculate_shift_x_blender(
    camera_x: float,
    focal_distance: float,
    fov_x_rad: float
) -> float:
    """
    计算 Blender 相机的 shift_x 值
    
    根据规范 v2.0 (Section 9):
    shift_x = -x_i / (2 * d_f * tan(FOV_x/2))
    
    注意：Blender 的 shift_x = 0.5 表示图像边缘，
    所以需要除以 2
    
    Args:
        camera_x: 相机水平位置 x_i (米)
        focal_distance: 焦平面距离 d_f (米)
        fov_x_rad: 水平视场角 FOV_x (弧度)
    
    Returns:
        Blender shift_x 值
    """
    if focal_distance <= 0 or fov_x_rad <= 0:
        return 0.0
    
    half_fov = fov_x_rad / 2.0
    denominator = 2.0 * focal_distance * math.tan(half_fov)
    
    if abs(denominator) < 1e-10:
        return 0.0
    
    # 负号：相机在右侧(+x)时，shift 应为负值（向左偏移）
    return -camera_x / denominator


def calculate_fov_from_focal_length(focal_length_mm: float, sensor_width_mm: float) -> float:
    """
    从焦距和传感器宽度计算水平视场角
    
    FOV_x = 2 * arctan(S_w / (2 * f_L))
    
    Args:
        focal_length_mm: 焦距 f_L (mm)
        sensor_width_mm: 传感器宽度 S_w (mm)
    
    Returns:
        水平视场角 FOV_x (弧度)
    """
    if focal_length_mm <= 0:
        return 0.0
    return 2.0 * math.atan(sensor_width_mm / (2.0 * focal_length_mm))


def calculate_focal_plane_size(
    focal_distance: float,
    fov_x_rad: float,
    aspect_ratio: float
) -> Tuple[float, float]:
    """
    计算焦平面可见尺寸
    
    W_f = 2 * d_f * tan(FOV_x / 2)
    H_f = W_f / r
    
    Args:
        focal_distance: 焦平面距离 d_f (米)
        fov_x_rad: 水平视场角 FOV_x (弧度)
        aspect_ratio: 宽高比 r = w/h
    
    Returns:
        (宽度 W_f, 高度 H_f) 焦平面尺寸 (米)
    """
    width = 2.0 * focal_distance * math.tan(fov_x_rad / 2.0)
    height = width / aspect_ratio if aspect_ratio > 0 else width
    return (width, height)
