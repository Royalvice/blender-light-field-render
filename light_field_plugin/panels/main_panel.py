# Main Panel (中文界面) - Revised v2.2

import bpy
from bpy.types import Panel
from ..core.light_field_control import get_light_field_control


class LIGHTFIELD_PT_main(Panel):
    bl_label = "光场相机阵列"
    bl_idname = "LIGHTFIELD_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    
    def draw(self, context):
        layout = self.layout
        control = get_light_field_control()
        row = layout.row(align=True)
        row.scale_y = 1.5
        if not control.is_created:
            row.operator("lightfield.create", text="创建光场相机", icon='CAMERA_DATA')
        else:
            row.operator("lightfield.delete", text="删除光场相机", icon='TRASH')


class LIGHTFIELD_PT_geometry(Panel):
    bl_label = "物理几何参数"
    bl_idname = "LIGHTFIELD_PT_geometry"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        
        col = layout.column(align=True)
        col.prop(props, "camera_count", text="相机数量 N")
        col.prop(props, "focal_distance", text="焦平面距离 d_f (m)")
        col.prop(props, "opening_angle", text="阵列张角 θ (°)")
        col.prop(props, "depth_range", text="景深范围 D_cube (m)")
        
        layout.separator()
        box = layout.box()
        box.label(text="计算值（只读）", icon='INFO')
        
        # 阵列宽度
        array_width = props.get_array_width()
        box.label(text=f"阵列宽度 W_array: {array_width:.3f} m")
        
        # 相机间距
        spacing = props.get_camera_spacing()
        box.label(text=f"相机间距 Δx: {spacing*100:.2f} cm")


class LIGHTFIELD_PT_camera_intrinsics(Panel):
    bl_label = "相机内参"
    bl_idname = "LIGHTFIELD_PT_camera_intrinsics"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        
        col = layout.column(align=True)
        col.prop(props, "focal_length", text="焦距 f_L (mm)")
        col.prop(props, "sensor_width", text="传感器宽度 S_w (mm)")
        
        layout.separator()
        box = layout.box()
        box.label(text="计算值（只读）", icon='INFO')
        
        fov_deg = props.get_fov_x_deg()
        box.label(text=f"水平视场角 FOV_x: {fov_deg:.2f}°")
        
        w, h = props.get_focal_plane_size()
        box.label(text=f"焦平面尺寸: {w:.2f} × {h:.2f} m")


class LIGHTFIELD_PT_preview(Panel):
    bl_label = "预览控制"
    bl_idname = "LIGHTFIELD_PT_preview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if not control.is_created:
            layout.label(text="请先创建光场相机", icon='INFO')
            return
        
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "active_camera_index", text="当前相机")
        max_idx = max(0, props.camera_count - 1)
        row.label(text=f"/ {max_idx}")
        
        row = layout.row(align=True)
        row.operator("lightfield.set_first_camera", text="首", icon='REW')
        row.operator("lightfield.set_prev_camera", text="◀", icon='BLANK1')
        row.operator("lightfield.set_next_camera", text="▶", icon='BLANK1')
        row.operator("lightfield.set_last_camera", text="尾", icon='FF')


class LIGHTFIELD_PT_render_settings(Panel):
    bl_label = "渲染设置"
    bl_idname = "LIGHTFIELD_PT_render_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        
        col = layout.column(align=True)
        col.label(text="输出分辨率:", icon='RENDER_RESULT')
        row = col.row(align=True)
        row.prop(props, "resolution_x", text="W")
        row.prop(props, "resolution_y", text="H")
        
        aspect = props.resolution_x / props.resolution_y if props.resolution_y > 0 else 1
        col.label(text=f"宽高比 r = {aspect:.3f}")
        
        layout.separator()
        
        # 帧范围
        col = layout.column(align=True)
        col.label(text="动画帧范围:", icon='TIME')
        row = col.row(align=True)
        row.prop(props, "frame_start", text="开始")
        row.prop(props, "frame_end", text="结束")
        
        layout.separator()
        
        col = layout.column(align=True)
        col.label(text="输出路径:", icon='FILE_FOLDER')
        col.prop(props, "output_path", text="")


class LIGHTFIELD_PT_render_actions(Panel):
    bl_label = "渲染操作"
    bl_idname = "LIGHTFIELD_PT_render_actions"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if not control.is_created:
            layout.label(text="请先创建光场相机", icon='INFO')
            return
        
        col = layout.column(align=True)
        col.scale_y = 1.3
        
        if props.is_rendering:
            col.operator("lightfield.stop_render", text="停止渲染", icon='CANCEL')
            
            # 进度显示
            box = layout.box()
            progress = props.render_progress
            total = props.camera_count
            percent = (progress / total * 100) if total > 0 else 0
            
            box.label(text=f"进度: {progress}/{total} ({percent:.1f}%)", icon='TIME')
            
            # 已用时间
            elapsed = props.render_elapsed_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            secs = int(elapsed % 60)
            if hours > 0:
                time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}"
            else:
                time_str = f"{minutes:02d}:{secs:02d}"
            box.label(text=f"已用时间: {time_str}")
            
            if props.render_info:
                box.label(text=props.render_info)
        else:
            col.operator("lightfield.render_frame", text="渲染单帧 (所有相机)", icon='RENDER_STILL')
            col.operator("lightfield.render_animation", text="渲染动画 (所有相机)", icon='RENDER_ANIMATION')


# 快速导航操作符
class LIGHTFIELD_OT_set_first_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_first_camera"
    bl_label = "首"
    def execute(self, context):
        context.scene.light_field_props.active_camera_index = 0
        return {'FINISHED'}


class LIGHTFIELD_OT_set_last_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_last_camera"
    bl_label = "尾"
    def execute(self, context):
        props = context.scene.light_field_props
        props.active_camera_index = max(0, props.camera_count - 1)
        return {'FINISHED'}


class LIGHTFIELD_OT_set_prev_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_prev_camera"
    bl_label = "上一个"
    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index > 0:
            props.active_camera_index -= 1
        return {'FINISHED'}


class LIGHTFIELD_OT_set_next_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_next_camera"
    bl_label = "下一个"
    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index < props.camera_count - 1:
            props.active_camera_index += 1
        return {'FINISHED'}
