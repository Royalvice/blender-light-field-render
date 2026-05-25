# Create Operators

import bpy
from bpy.types import Operator
from ..core.light_field_control import get_light_field_control, reset_light_field_control


class LIGHTFIELD_OT_create(Operator):
    bl_idname = "lightfield.create"
    bl_label = "创建光场相机"
    bl_description = "创建光场相机阵列系统"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if control.is_created:
            self.report({'WARNING'}, "光场相机系统已存在")
            return {'CANCELLED'}
        
        success = control.create(
            camera_count=props.camera_count,
            focal_distance=props.focal_distance,
            opening_angle_deg=props.opening_angle,
            focal_length_mm=props.focal_length,
            sensor_width_mm=props.sensor_width,
            depth_range=props.depth_range
        )
        
        if success:
            props.active_camera_index = props.camera_count // 2
            self.report({'INFO'}, f"已创建 {props.camera_count} 个光场相机")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "创建光场相机系统失败")
            return {'CANCELLED'}


class LIGHTFIELD_OT_delete(Operator):
    bl_idname = "lightfield.delete"
    bl_label = "删除光场相机"
    bl_description = "删除光场相机阵列系统"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        control = get_light_field_control()
        
        if not control.is_created:
            self.report({'WARNING'}, "没有找到光场相机系统")
            return {'CANCELLED'}
        
        control.delete()
        reset_light_field_control()
        self.report({'INFO'}, "已删除光场相机系统")
        return {'FINISHED'}


class LIGHTFIELD_OT_update(Operator):
    bl_idname = "lightfield.update"
    bl_label = "更新参数"
    bl_description = "更新光场相机阵列的所有参数"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if not control.is_created:
            self.report({'WARNING'}, "请先创建光场相机系统")
            return {'CANCELLED'}
        
        control.update(
            camera_count=props.camera_count,
            focal_distance=props.focal_distance,
            opening_angle_deg=props.opening_angle,
            focal_length_mm=props.focal_length,
            sensor_width_mm=props.sensor_width
        )
        
        self.report({'INFO'}, "已更新光场相机参数")
        return {'FINISHED'}
