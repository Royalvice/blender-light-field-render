# Light Field Properties

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
        name="Camera Count",
        description="Total number of cameras/views in the light-field array",
        default=60,
        min=2,
        max=200,
        update=mark_geometry_dirty,
    )

    focal_distance: FloatProperty(
        name="Focal Distance",
        description="Distance from the camera array to the focal plane, in meters",
        default=10.0,
        min=0.1,
        max=1000.0,
        unit="LENGTH",
        update=mark_geometry_dirty,
    )

    opening_angle: FloatProperty(
        name="Opening Angle",
        description="Effective angular coverage of the camera array, in degrees",
        default=11.4,
        min=0.1,
        max=120.0,
        update=mark_geometry_dirty,
    )

    depth_range: FloatProperty(
        name="Depth Range",
        description="Depth of the display volume around the focal plane, in meters",
        default=3.0,
        min=0.1,
        max=100.0,
        unit="LENGTH",
        update=mark_geometry_dirty,
    )

    focal_length: FloatProperty(
        name="Focal Length",
        description="Camera focal length f_L, in millimeters",
        default=50.0,
        min=1.0,
        max=500.0,
        update=mark_geometry_dirty,
    )

    sensor_width: FloatProperty(
        name="Sensor Width",
        description="Camera sensor width S_w, in millimeters",
        default=36.0,
        min=1.0,
        max=100.0,
        update=mark_geometry_dirty,
    )

    resolution_x: IntProperty(
        name="Width",
        description="Output image width in pixels",
        default=1920,
        min=1,
        max=16384,
        update=mark_render_settings_dirty,
    )

    resolution_y: IntProperty(
        name="Height",
        description="Output image height in pixels",
        default=1080,
        min=1,
        max=16384,
        update=mark_render_settings_dirty,
    )

    output_path: StringProperty(
        name="Output Path",
        description="Directory for rendered output",
        default="//light_field_output/",
        subtype="DIR_PATH",
    )

    output_file_format: EnumProperty(
        name="Output Format",
        description="Image format written by the light-field render operators",
        items=[
            ("PNG", "PNG", "Continuous-tone PNG output"),
            ("TIFF", "TIFF", "Continuous-tone TIFF output from Blender"),
            (
                "FILM_TIFF",
                "1-bit Film TIFF",
                "Render a continuous source image, then export a halftoned 1-bit TIFF",
            ),
        ],
        default="PNG",
        update=mark_render_settings_dirty,
    )

    keep_continuous_source: BoolProperty(
        name="Keep Continuous Source",
        description="Keep the temporary continuous-tone PNG source used for 1-bit Film TIFF export",
        default=False,
        update=mark_render_settings_dirty,
    )

    film_halftone_method: EnumProperty(
        name="Halftone Method",
        description="Halftone strategy for 1-bit Film TIFF export",
        items=[
            ("FM", "FM / Error Diffusion", "Dispersed fixed-size dots; usually safer for lenticular/light-field work"),
            ("AM", "AM / Clustered Dot", "Traditional clustered screen dots controlled by LPI and angle"),
        ],
        default="FM",
        update=mark_render_settings_dirty,
    )

    film_lpi: IntProperty(
        name="LPI",
        description="Screen ruling for AM halftone, in lines per inch",
        default=200,
        min=30,
        max=600,
        update=mark_render_settings_dirty,
    )

    film_dpi: IntProperty(
        name="DPI",
        description="Film output resolution metadata and AM cell-size basis",
        default=2400,
        min=300,
        max=9600,
        update=mark_render_settings_dirty,
    )

    film_angle: FloatProperty(
        name="Screen Angle",
        description="AM halftone screen angle in degrees",
        default=45.0,
        min=-90.0,
        max=90.0,
        update=mark_render_settings_dirty,
    )

    film_dot_shape: EnumProperty(
        name="Dot Shape",
        description="AM halftone dot shape",
        items=[
            ("ROUND", "Round", "Round clustered dots"),
            ("DIAMOND", "Diamond", "Diamond clustered dots"),
            ("ELLIPSE", "Ellipse", "Elliptical clustered dots"),
        ],
        default="ROUND",
        update=mark_render_settings_dirty,
    )

    film_gamma: FloatProperty(
        name="Gamma",
        description="Luminance gamma applied before 1-bit halftone conversion",
        default=1.0,
        min=0.1,
        max=5.0,
        update=mark_render_settings_dirty,
    )

    auto_apply_parameters: BoolProperty(
        name="Auto Apply After Drag",
        description="Apply changed camera-array parameters after slider dragging stops; disabled by default to avoid UI stalls",
        default=False,
    )

    active_camera_index: IntProperty(
        name="Active Camera",
        description="Active camera/view index",
        default=30,
        min=0,
        soft_max=199,
        update=update_active_camera,
    )

    is_rendering: BoolProperty(name="Rendering", default=False)
    render_progress: IntProperty(name="Render Progress", default=0, min=0)
    render_info: StringProperty(name="Render Info", default="")
    render_elapsed_time: FloatProperty(name="Elapsed Time", default=0.0, min=0.0)
    render_start_time: FloatProperty(name="Start Time", default=0.0)

    geometry_dirty: BoolProperty(
        name="Geometry Dirty",
        default=False,
        options={"HIDDEN"},
    )

    render_settings_dirty: BoolProperty(
        name="Render Settings Dirty",
        default=False,
        options={"HIDDEN"},
    )

    frame_start: IntProperty(
        name="Frame Start",
        description="First frame for animation rendering",
        default=1,
        min=0,
    )

    frame_end: IntProperty(
        name="Frame End",
        description="Last frame for animation rendering",
        default=250,
        min=0,
    )

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


def register():
    bpy.utils.register_class(LightFieldProperties)
    bpy.types.Scene.light_field_props = bpy.props.PointerProperty(type=LightFieldProperties)


def unregister():
    del bpy.types.Scene.light_field_props
    bpy.utils.unregister_class(LightFieldProperties)
