# Main UI panels.

import bpy
from bpy.types import Panel

from ..core.light_field_control import get_light_field_control


class LIGHTFIELD_PT_main(Panel):
    bl_label = "Light Field Camera Array"
    bl_idname = "LIGHTFIELD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        row = layout.row(align=True)
        row.scale_y = 1.5
        if not control.is_created:
            row.operator("lightfield.create", text="Create Light Field Camera", icon="CAMERA_DATA")
        else:
            row.operator("lightfield.delete", text="Delete Light Field Camera", icon="TRASH")

        if control.is_created and props.geometry_dirty:
            box = layout.box()
            box.label(text="Camera parameters changed", icon="INFO")
            box.operator("lightfield.update", text="Apply Camera Parameters", icon="CHECKMARK")


class LIGHTFIELD_PT_geometry(Panel):
    bl_label = "Physical Geometry"
    bl_idname = "LIGHTFIELD_PT_geometry"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        col = layout.column(align=True)
        col.prop(props, "camera_count", text="Camera Count N")
        col.prop(props, "focal_distance", text="Focal Distance d_f (m)")
        col.prop(props, "opening_angle", text="Opening Angle (deg)")
        col.prop(props, "depth_range", text="Depth Range D_cube (m)")
        col.prop(props, "auto_apply_parameters", text="Auto Apply After Drag")

        if control.is_created:
            row = layout.row(align=True)
            row.enabled = props.geometry_dirty
            row.operator("lightfield.update", text="Apply Camera Parameters", icon="CHECKMARK")

        layout.separator()
        box = layout.box()
        box.label(text="Calculated values", icon="INFO")
        box.label(text=f"Array width W_array: {props.get_array_width():.3f} m")
        box.label(text=f"Camera spacing dx: {props.get_camera_spacing() * 100:.2f} cm")


class LIGHTFIELD_PT_camera_intrinsics(Panel):
    bl_label = "Camera Intrinsics"
    bl_idname = "LIGHTFIELD_PT_camera_intrinsics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        col = layout.column(align=True)
        col.prop(props, "focal_length", text="Focal Length f_L (mm)")
        col.prop(props, "sensor_width", text="Sensor Width S_w (mm)")

        if control.is_created:
            row = layout.row(align=True)
            row.enabled = props.geometry_dirty
            row.operator("lightfield.update", text="Apply Camera Parameters", icon="CHECKMARK")

        layout.separator()
        box = layout.box()
        box.label(text="Calculated values", icon="INFO")
        box.label(text=f"Horizontal FOV_x: {props.get_fov_x_deg():.2f} deg")
        w, h = props.get_focal_plane_size()
        box.label(text=f"Focal plane size: {w:.2f} x {h:.2f} m")


class LIGHTFIELD_PT_preview(Panel):
    bl_label = "Preview Control"
    bl_idname = "LIGHTFIELD_PT_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            layout.label(text="Create the light-field camera system first", icon="INFO")
            return

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "active_camera_index", text="Active Camera")
        max_idx = max(0, props.camera_count - 1)
        row.label(text=f"/ {max_idx}")

        row = layout.row(align=True)
        row.operator("lightfield.set_first_camera", text="First", icon="REW")
        row.operator("lightfield.set_prev_camera", text="Prev", icon="TRIA_LEFT")
        row.operator("lightfield.set_next_camera", text="Next", icon="TRIA_RIGHT")
        row.operator("lightfield.set_last_camera", text="Last", icon="FF")


class LIGHTFIELD_PT_render_settings(Panel):
    bl_label = "Output Settings"
    bl_idname = "LIGHTFIELD_PT_render_settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props

        col = layout.column(align=True)
        col.label(text="Output resolution", icon="RENDER_RESULT")
        row = col.row(align=True)
        row.prop(props, "resolution_x", text="W")
        row.prop(props, "resolution_y", text="H")
        aspect = props.resolution_x / props.resolution_y if props.resolution_y > 0 else 1
        col.label(text=f"Aspect ratio r = {aspect:.3f}")

        row = layout.row(align=True)
        row.enabled = props.render_settings_dirty
        row.operator("lightfield.apply_render_settings", text="Apply Output Settings", icon="CHECKMARK")

        layout.separator()
        col = layout.column(align=True)
        col.prop(props, "output_file_format", text="Format")
        if props.output_file_format == "FILM_TIFF":
            col.prop(props, "keep_continuous_source")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Animation frame range", icon="TIME")
        row = col.row(align=True)
        row.prop(props, "frame_start", text="Start")
        row.prop(props, "frame_end", text="End")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Output path", icon="FILE_FOLDER")
        col.prop(props, "output_path", text="")


class LIGHTFIELD_PT_film_tiff(Panel):
    bl_label = "1-bit Film TIFF"
    bl_idname = "LIGHTFIELD_PT_film_tiff"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_render_settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        layout.enabled = props.output_file_format == "FILM_TIFF"

        col = layout.column(align=True)
        col.prop(props, "film_halftone_method", text="Method")
        col.prop(props, "film_dpi", text="DPI")
        col.prop(props, "film_gamma", text="Gamma")

        if props.film_halftone_method == "AM":
            col.prop(props, "film_lpi", text="LPI")
            col.prop(props, "film_angle", text="Angle")
            col.prop(props, "film_dot_shape", text="Dot")

        box = layout.box()
        box.label(text="Film TIFF is 1-bit black/white.", icon="INFO")
        box.label(text="FM is recommended for fewer moire artifacts.")


class LIGHTFIELD_PT_render_actions(Panel):
    bl_label = "Render Actions"
    bl_idname = "LIGHTFIELD_PT_render_actions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Light Field"
    bl_parent_id = "LIGHTFIELD_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            layout.label(text="Create the light-field camera system first", icon="INFO")
            return

        if props.geometry_dirty:
            box = layout.box()
            box.label(text="Pending camera changes will be applied before rendering.", icon="INFO")

        col = layout.column(align=True)
        col.scale_y = 1.3

        if props.is_rendering:
            col.operator("lightfield.stop_render", text="Stop Render", icon="CANCEL")
            box = layout.box()
            progress = props.render_progress
            total = props.camera_count
            percent = (progress / total * 100) if total > 0 else 0
            box.label(text=f"Progress: {progress}/{total} ({percent:.1f}%)", icon="TIME")

            elapsed = props.render_elapsed_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            secs = int(elapsed % 60)
            time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours > 0 else f"{minutes:02d}:{secs:02d}"
            box.label(text=f"Elapsed: {time_str}")
            if props.render_info:
                box.label(text=props.render_info)
        else:
            col.operator("lightfield.render_frame", text="Render Current Frame", icon="RENDER_STILL")
            col.operator("lightfield.render_animation", text="Render Animation", icon="RENDER_ANIMATION")


class LIGHTFIELD_OT_set_first_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_first_camera"
    bl_label = "First Camera"

    def execute(self, context):
        context.scene.light_field_props.active_camera_index = 0
        return {"FINISHED"}


class LIGHTFIELD_OT_set_last_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_last_camera"
    bl_label = "Last Camera"

    def execute(self, context):
        props = context.scene.light_field_props
        props.active_camera_index = max(0, props.camera_count - 1)
        return {"FINISHED"}


class LIGHTFIELD_OT_set_prev_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_prev_camera"
    bl_label = "Previous Camera"

    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index > 0:
            props.active_camera_index -= 1
        return {"FINISHED"}


class LIGHTFIELD_OT_set_next_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_next_camera"
    bl_label = "Next Camera"

    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index < props.camera_count - 1:
            props.active_camera_index += 1
        return {"FINISHED"}
