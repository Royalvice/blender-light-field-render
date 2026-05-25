# Core module
from .light_field_control import LightFieldControl
from .camera_array import CameraArrayManager
from .math_utils import (
    calculate_array_width,
    calculate_opening_angle,
    calculate_camera_position_linear,
    calculate_camera_spacing,
    calculate_shift_x_blender,
    calculate_fov_from_focal_length,
    calculate_focal_plane_size,
    degrees_to_radians,
    radians_to_degrees,
)
from .film_tiff import (
    am_clustered_halftone,
    fm_error_diffusion_halftone,
    halftone_luma,
    write_1bit_tiff,
    write_halftoned_1bit_tiff,
)

__all__ = [
    "LightFieldControl",
    "CameraArrayManager",
    "calculate_array_width",
    "calculate_opening_angle",
    "calculate_camera_position_linear",
    "calculate_camera_spacing",
    "calculate_shift_x_blender",
    "calculate_fov_from_focal_length",
    "calculate_focal_plane_size",
    "degrees_to_radians",
    "radians_to_degrees",
    "am_clustered_halftone",
    "fm_error_diffusion_halftone",
    "halftone_luma",
    "write_1bit_tiff",
    "write_halftoned_1bit_tiff",
]
