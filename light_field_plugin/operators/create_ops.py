# 创建和更新操作。

import bpy
from bpy.types import Operator

from ..core.light_field_control import get_light_field_control, reset_light_field_control
from ..properties.light_field_props import sync_render_resolution


def apply_output_settings(scene) -> None:
    sync_render_resolution(scene)
    control = get_light_field_control()
    if control.is_created:
        control.update_visuals()


def apply_light_field_parameters(scene) -> bool:
    props = scene.light_field_props
    control = get_light_field_control()
    if not control.is_created:
        return False

    apply_output_settings(scene)
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
    bl_label = "创建光场相机"
    bl_description = "创建光场相机阵列系统"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()

        if control.is_created:
            self.report({"WARNING"}, "光场相机系统已存在")
            return {"CANCELLED"}

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
            self.report({"INFO"}, f"已创建 {props.camera_count} 台光场相机")
            return {"FINISHED"}

        self.report({"ERROR"}, "创建光场相机系统失败")
        return {"CANCELLED"}


class LIGHTFIELD_OT_delete(Operator):
    bl_idname = "lightfield.delete"
    bl_label = "删除光场相机"
    bl_description = "删除光场相机阵列系统"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        control = get_light_field_control()

        if not control.is_created:
            self.report({"WARNING"}, "未找到光场相机系统")
            return {"CANCELLED"}

        control.delete()
        reset_light_field_control()
        context.scene.light_field_props.geometry_dirty = False
        self.report({"INFO"}, "已删除光场相机系统")
        return {"FINISHED"}


class LIGHTFIELD_OT_update(Operator):
    bl_idname = "lightfield.update"
    bl_label = "应用相机参数"
    bl_description = "应用待更新的光场相机阵列参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not apply_light_field_parameters(context.scene):
            self.report({"WARNING"}, "请先创建光场相机系统")
            return {"CANCELLED"}

        self.report({"INFO"}, "已应用光场相机参数")
        return {"FINISHED"}


class LIGHTFIELD_OT_apply_render_settings(Operator):
    bl_idname = "lightfield.apply_render_settings"
    bl_label = "应用输出设置"
    bl_description = "将输出分辨率设置应用到 Blender 场景，并刷新辅助显示"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        apply_output_settings(context.scene)
        self.report({"INFO"}, "已应用输出分辨率")
        return {"FINISHED"}
