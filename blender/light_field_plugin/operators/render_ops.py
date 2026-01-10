# Render Operators (Revised v2.4)
# 渲染操作符 - 支持进度显示、帧范围和时间估算

import bpy
import os
import time
from bpy.types import Operator

from ..core.light_field_control import get_light_field_control


def format_time(seconds: float) -> str:
    """格式化时间为 HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class LIGHTFIELD_OT_render_frame(Operator):
    """渲染单帧 - 遍历所有相机"""
    bl_idname = "lightfield.render_frame"
    bl_label = "渲染单帧"
    bl_description = "渲染当前帧，遍历所有相机"
    bl_options = {'REGISTER'}
    
    _original_camera = None
    
    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if not control.is_created:
            self.report({'ERROR'}, "请先创建光场相机系统")
            return {'CANCELLED'}
        
        if props.is_rendering:
            self.report({'WARNING'}, "已有渲染任务在进行中")
            return {'CANCELLED'}
        
        output_path = bpy.path.abspath(props.output_path)
        if not output_path:
            self.report({'ERROR'}, "请设置输出路径")
            return {'CANCELLED'}
        
        frame = context.scene.frame_current
        frame_dir = os.path.join(output_path, f"frame_{frame:04d}")
        os.makedirs(frame_dir, exist_ok=True)
        
        start_index = self._detect_render_progress(frame_dir, props.camera_count)
        
        if start_index >= props.camera_count:
            self.report({'INFO'}, "该帧已完成渲染")
            return {'FINISHED'}
        
        self._original_camera = context.scene.camera
        
        # 初始化渲染状态
        props.is_rendering = True
        props.render_progress = start_index
        props.render_start_time = time.time()
        props.render_elapsed_time = 0.0
        
        context.scene.render.resolution_x = props.resolution_x
        context.scene.render.resolution_y = props.resolution_y
        
        total = props.camera_count
        for cam_idx in range(start_index, total):
            if not props.is_rendering:
                break
            
            # 更新时间和进度
            elapsed = time.time() - props.render_start_time
            props.render_elapsed_time = elapsed
            
            # 估算剩余时间
            completed = cam_idx - start_index
            if completed > 0:
                avg_time = elapsed / completed
                remaining = avg_time * (total - cam_idx)
                props.render_info = f"相机 {cam_idx + 1}/{total} | 剩余 ~{format_time(remaining)}"
            else:
                props.render_info = f"相机 {cam_idx + 1}/{total}"
            
            props.render_progress = cam_idx
            
            # 刷新UI
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            
            control.set_active_camera(cam_idx)
            output_file = os.path.join(frame_dir, f"camera_{cam_idx:03d}")
            context.scene.render.filepath = output_file
            
            bpy.ops.render.render(write_still=True)
        
        # 完成
        total_time = time.time() - props.render_start_time
        props.is_rendering = False
        props.render_progress = total
        props.render_elapsed_time = total_time
        props.render_info = f"完成! 总用时 {format_time(total_time)}"
        
        if self._original_camera:
            context.scene.camera = self._original_camera
        
        self.report({'INFO'}, f"单帧渲染完成：{total} 个相机，用时 {format_time(total_time)}")
        return {'FINISHED'}
    
    def _detect_render_progress(self, output_dir: str, total_cameras: int) -> int:
        if not os.path.exists(output_dir):
            return 0
        for i in range(total_cameras):
            found = False
            for ext in ['.png', '.jpg', '.exr']:
                if os.path.exists(os.path.join(output_dir, f"camera_{i:03d}{ext}")):
                    found = True
                    break
            if not found:
                return i
        return total_cameras


class LIGHTFIELD_OT_render_animation(Operator):
    """渲染动画 - 逐相机渲染指定帧范围"""
    bl_idname = "lightfield.render_animation"
    bl_label = "渲染动画"
    bl_description = "逐相机渲染动画序列（支持帧范围）"
    bl_options = {'REGISTER'}
    
    _original_camera = None
    
    def execute(self, context):
        props = context.scene.light_field_props
        control = get_light_field_control()
        
        if not control.is_created:
            self.report({'ERROR'}, "请先创建光场相机系统")
            return {'CANCELLED'}
        
        if props.is_rendering:
            self.report({'WARNING'}, "已有渲染任务在进行中")
            return {'CANCELLED'}
        
        output_path = bpy.path.abspath(props.output_path)
        if not output_path:
            self.report({'ERROR'}, "请设置输出路径")
            return {'CANCELLED'}
        
        os.makedirs(output_path, exist_ok=True)
        
        frame_start = props.frame_start
        frame_end = props.frame_end
        
        if frame_end < frame_start:
            self.report({'ERROR'}, "结束帧必须大于等于开始帧")
            return {'CANCELLED'}
        
        start_camera = self._detect_animation_progress(
            output_path, props.camera_count, frame_start, frame_end
        )
        
        if start_camera >= props.camera_count:
            self.report({'INFO'}, "动画已完成渲染")
            return {'FINISHED'}
        
        self._original_camera = context.scene.camera
        original_filepath = context.scene.render.filepath
        original_frame_start = context.scene.frame_start
        original_frame_end = context.scene.frame_end
        
        # 初始化渲染状态
        props.is_rendering = True
        props.render_progress = start_camera
        props.render_start_time = time.time()
        props.render_elapsed_time = 0.0
        
        context.scene.render.resolution_x = props.resolution_x
        context.scene.render.resolution_y = props.resolution_y
        context.scene.frame_start = frame_start
        context.scene.frame_end = frame_end
        
        total_cameras = props.camera_count
        total_frames = frame_end - frame_start + 1
        
        for cam_idx in range(start_camera, total_cameras):
            if not props.is_rendering:
                break
            
            # 更新时间和进度
            elapsed = time.time() - props.render_start_time
            props.render_elapsed_time = elapsed
            
            # 估算剩余时间
            completed = cam_idx - start_camera
            if completed > 0:
                avg_time = elapsed / completed
                remaining = avg_time * (total_cameras - cam_idx)
                props.render_info = f"相机 {cam_idx + 1}/{total_cameras} | 帧 {frame_start}-{frame_end} | 剩余 ~{format_time(remaining)}"
            else:
                props.render_info = f"相机 {cam_idx + 1}/{total_cameras} | 帧 {frame_start}-{frame_end}"
            
            props.render_progress = cam_idx
            
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            
            control.set_active_camera(cam_idx)
            
            camera_dir = os.path.join(output_path, f"camera_{cam_idx:03d}")
            os.makedirs(camera_dir, exist_ok=True)
            context.scene.render.filepath = os.path.join(camera_dir, "frame_")
            
            bpy.ops.render.render(animation=True)
        
        # 完成
        total_time = time.time() - props.render_start_time
        props.is_rendering = False
        props.render_progress = total_cameras
        props.render_elapsed_time = total_time
        props.render_info = f"完成! 总用时 {format_time(total_time)}"
        
        context.scene.render.filepath = original_filepath
        context.scene.frame_start = original_frame_start
        context.scene.frame_end = original_frame_end
        if self._original_camera:
            context.scene.camera = self._original_camera
        
        self.report({'INFO'}, f"动画渲染完成：{total_cameras} 个相机，用时 {format_time(total_time)}")
        return {'FINISHED'}
    
    def _detect_animation_progress(self, output_path: str, total_cameras: int, 
                                   frame_start: int, frame_end: int) -> int:
        if not os.path.exists(output_path):
            return 0
        expected_frames = frame_end - frame_start + 1
        for i in range(total_cameras):
            camera_dir = os.path.join(output_path, f"camera_{i:03d}")
            if not os.path.exists(camera_dir):
                return i
            frame_files = [f for f in os.listdir(camera_dir) if f.startswith("frame_")]
            if len(frame_files) < expected_frames:
                return i
        return total_cameras


class LIGHTFIELD_OT_stop_render(Operator):
    """停止渲染"""
    bl_idname = "lightfield.stop_render"
    bl_label = "停止渲染"
    bl_description = "停止当前渲染任务"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        props = context.scene.light_field_props
        if not props.is_rendering:
            self.report({'WARNING'}, "没有正在进行的渲染任务")
            return {'CANCELLED'}
        props.is_rendering = False
        self.report({'INFO'}, "已停止渲染")
        return {'FINISHED'}
