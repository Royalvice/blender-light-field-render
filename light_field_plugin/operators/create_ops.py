# Create and update operators.

import bpy
from bpy.types import Operator

from ..core.light_field_control import get_light_field_control, reset_light_field_control
from ..properties.light_field_props import sync_render_resolution


def apply_light_field_parameters(scene) -> bool:
    props = scene.light_field_props
    control = get_light_field_control()
    if not control.is_created:
        return False

    sync_render_resolution(scene)
    control.update(
        camera_count=props.camera_count,
        focal_distance=props.focal_distance,
        opening_angle_deg=props.opening_angle,
        focal_length_mm=props.focal_length,
        sensor_width_mm=props.sensor_width,
    )
    control.update_depth_box()
    props.geometry_dirty = False
    props.render_settings_dirty = False
    return True


class LIGHTFIELD_OT_create(Operator):
    bl_idname = "lightfield.create"
    bl_label = "Create Light Field Camera"
    bl_description = "Create the light-field camera-array system"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()

        if control.is_created:
            self.report({"WARNING"}, "Light-field camera system already exists")
            return {"CANCELLED"}

        sync_render_resolution(context.scene)
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
            self.report({"INFO"}, f"Created {props.camera_count} light-field cameras")
            return {"FINISHED"}

        self.report({"ERROR"}, "Failed to create light-field camera system")
        return {"CANCELLED"}


class LIGHTFIELD_OT_delete(Operator):
    bl_idname = "lightfield.delete"
    bl_label = "Delete Light Field Camera"
    bl_description = "Delete the light-field camera-array system"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        control = get_light_field_control()

        if not control.is_created:
            self.report({"WARNING"}, "No light-field camera system found")
            return {"CANCELLED"}

        control.delete()
        reset_light_field_control()
        context.scene.light_field_props.geometry_dirty = False
        self.report({"INFO"}, "Deleted light-field camera system")
        return {"FINISHED"}


class LIGHTFIELD_OT_update(Operator):
    bl_idname = "lightfield.update"
    bl_label = "Apply Camera Parameters"
    bl_description = "Apply pending light-field camera-array parameter changes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not apply_light_field_parameters(context.scene):
            self.report({"WARNING"}, "Create the light-field camera system first")
            return {"CANCELLED"}

        self.report({"INFO"}, "Applied light-field camera parameters")
        return {"FINISHED"}


class LIGHTFIELD_OT_apply_render_settings(Operator):
    bl_idname = "lightfield.apply_render_settings"
    bl_label = "Apply Output Settings"
    bl_description = "Apply output resolution settings to the Blender scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sync_render_resolution(context.scene)
        self.report({"INFO"}, "Applied output resolution")
        return {"FINISHED"}
