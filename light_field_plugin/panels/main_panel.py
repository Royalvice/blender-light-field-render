# 中文 UI 面板。

import bpy
from bpy.types import Panel

from ..core.light_field_control import get_light_field_control


class LIGHTFIELD_PT_main(Panel):
    bl_label = "光场相机阵列"
    bl_idname = "LIGHTFIELD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        row = layout.row(align=True)
        row.scale_y = 1.5
        if not control.is_created:
            row.operator("lightfield.create", text="创建光场相机", icon="CAMERA_DATA")
        else:
            row.operator("lightfield.delete", text="删除光场相机", icon="TRASH")

        if control.is_created and props.geometry_dirty:
            box = layout.box()
            box.label(text="相机参数已变更", icon="INFO")
            box.operator("lightfield.update", text="应用相机参数", icon="CHECKMARK")


class LIGHTFIELD_PT_geometry(Panel):
    bl_label = "物理几何参数"
    bl_idname = "LIGHTFIELD_PT_geometry"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        col = layout.column(align=True)
        col.prop(props, "camera_count", text="相机数量 N")
        col.prop(props, "focal_distance", text="焦平面距离 d_f (m)")
        col.prop(props, "opening_angle", text="阵列张角 (°)")
        col.prop(props, "depth_range", text="景深范围 D_cube (m)")
        col.prop(props, "auto_apply_parameters", text="拖动结束后自动应用")

        if control.is_created:
            row = layout.row(align=True)
            row.enabled = props.geometry_dirty
            row.operator("lightfield.update", text="应用相机参数", icon="CHECKMARK")

        layout.separator()
        box = layout.box()
        box.label(text="计算值", icon="INFO")
        box.label(text=f"阵列宽度 W_array: {props.get_array_width():.3f} m")
        box.label(text=f"相机间距 dx: {props.get_camera_spacing() * 100:.2f} cm")


class LIGHTFIELD_PT_camera_intrinsics(Panel):
    bl_label = "相机内参"
    bl_idname = "LIGHTFIELD_PT_camera_intrinsics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        col = layout.column(align=True)
        col.prop(props, "focal_length", text="焦距 f_L (mm)")
        col.prop(props, "sensor_width", text="传感器宽度 S_w (mm)")

        if control.is_created:
            row = layout.row(align=True)
            row.enabled = props.geometry_dirty
            row.operator("lightfield.update", text="应用相机参数", icon="CHECKMARK")

        layout.separator()
        box = layout.box()
        box.label(text="计算值", icon="INFO")
        box.label(text=f"水平视场角 FOV_x: {props.get_fov_x_deg():.2f}°")
        w, h = props.get_focal_plane_size()
        box.label(text=f"焦平面尺寸: {w:.2f} x {h:.2f} m")


class LIGHTFIELD_PT_preview(Panel):
    bl_label = "预览控制"
    bl_idname = "LIGHTFIELD_PT_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            layout.label(text="请先创建光场相机系统", icon="INFO")
            return

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "active_camera_index", text="当前相机")
        max_idx = max(0, props.camera_count - 1)
        row.label(text=f"/ {max_idx}")

        row = layout.row(align=True)
        row.operator("lightfield.set_first_camera", text="首个", icon="REW")
        row.operator("lightfield.set_prev_camera", text="上一个", icon="TRIA_LEFT")
        row.operator("lightfield.set_next_camera", text="下一个", icon="TRIA_RIGHT")
        row.operator("lightfield.set_last_camera", text="末个", icon="FF")


class LIGHTFIELD_PT_render_settings(Panel):
    bl_label = "输出设置"
    bl_idname = "LIGHTFIELD_PT_render_settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props

        col = layout.column(align=True)
        col.label(text="输出分辨率", icon="RENDER_RESULT")
        row = col.row(align=True)
        row.prop(props, "resolution_x", text="W")
        row.prop(props, "resolution_y", text="H")
        aspect = props.resolution_x / props.resolution_y if props.resolution_y > 0 else 1
        col.label(text=f"宽高比 r = {aspect:.3f}")

        row = layout.row(align=True)
        row.enabled = props.render_settings_dirty
        row.operator("lightfield.apply_render_settings", text="应用输出设置", icon="CHECKMARK")

        layout.separator()
        col = layout.column(align=True)
        col.prop(props, "output_file_format", text="输出格式")
        if props.output_file_format == "JPG":
            col.prop(props, "jpeg_quality", text="JPG 质量")
            col.label(text="JPG 输出会强制使用 Standard 色彩管理。", icon="INFO")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="动画帧范围", icon="TIME")
        row = col.row(align=True)
        row.prop(props, "frame_start", text="开始")
        row.prop(props, "frame_end", text="结束")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="输出路径", icon="FILE_FOLDER")
        col.prop(props, "output_path", text="")


class LIGHTFIELD_PT_film_tiff(Panel):
    bl_label = "1-bit 菲林 TIFF"
    bl_idname = "LIGHTFIELD_PT_film_tiff"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_render_settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        layout.enabled = props.output_file_format == "FILM_TIFF"

        col = layout.column(align=True)
        col.prop(props, "film_halftone_method", text="挂网方式")
        col.prop(props, "film_dpi", text="DPI")
        col.prop(props, "film_line_period_px", text="线周期 px")
        col.prop(props, "film_line_phase_y", text="Y 相位 px")
        col.prop(props, "film_line_density", text="密度")
        col.prop(props, "film_gamma", text="Gamma")

        box = layout.box()
        box.label(text="菲林 TIFF 为 1-bit 纯黑白输出。", icon="INFO")
        box.label(text="LBY 行阈值屏：18px 水平周期，可走 Native 加速。")


class LIGHTFIELD_PT_delivery_output(Panel):
    bl_label = "最终交付输出"
    bl_idname = "LIGHTFIELD_PT_delivery_output"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props

        col = layout.column(align=True)
        col.label(text="交付尺寸", icon="OUTPUT")
        col.prop(props, "delivery_width_mm", text="宽度 mm")
        col.prop(props, "delivery_height_mm", text="高度 mm")
        col.prop(props, "delivery_ppi", text="PPI")

        width_px, height_px = props.get_delivery_pixel_size()
        if width_px > 0 and height_px > 0:
            col.label(text=f"最终像素: {width_px} x {height_px}")
        else:
            col.label(text="请填写交付宽度、高度和 PPI", icon="ERROR")

        if props.is_delivery_large_output():
            box = layout.box()
            box.label(text="最终像素超过 100MP，可能耗时较长。", icon="ERROR")
            box.prop(props, "delivery_confirm_large_output", text="确认生成大图")

        if props.has_delivery_source_upscale_warning():
            box = layout.box()
            box.label(text="最终尺寸超过源视角 2 倍，清晰度可能不足。", icon="INFO")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="交织参数", icon="MOD_WAVE")
        col.prop(props, "interlace_pe", text="PE")
        col.prop(props, "interlace_angle", text="Angle (°)")
        col.prop(props, "interlace_offset", text="Offset")
        col.prop(props, "interlace_reverse_views", text="反转视角顺序")
        col.label(text="交织模式：整像素交织", icon="CHECKMARK")
        if abs(props.interlace_angle) < 1.0e-6:
            col.label(text="Angle=0° 可使用 Native 快速路径", icon="CHECKMARK")
        else:
            col.label(text="Angle 非 0° 时不会使用 Native 快速路径", icon="ERROR")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="输出文件", icon="FILE_TICK")
        col.prop(props, "delivery_write_interlaced_tiff", text="输出连续调 interlaced.tif")
        if props.delivery_write_interlaced_tiff:
            col.label(text="interlaced.tif / interlaced_preview.png")
        else:
            col.label(text="快速模式：跳过 interlaced.tif")
            col.label(text="输出 interlaced_preview.png 用于检查")
        col.label(text="film_1bit.tif / delivery_manifest.json")
        col.label(text="1-bit 输出算法：LBY 行阈值屏", icon="INFO")
        col.prop(props, "delivery_write_halftone_variants", text="输出多版挂网候选")
        if props.delivery_write_halftone_variants:
            col.label(text="额外输出 low_fp / balanced / more_black", icon="INFO")
        col.prop(props, "delivery_calibration_target_tiff", text="校准目标 TIFF")

        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.3
        if props.is_delivery_generating:
            col.operator("lightfield.stop_delivery", text="停止交付生成", icon="CANCEL")
            box = layout.box()
            total = max(1, props.delivery_progress_total)
            percent = props.delivery_progress / total * 100
            box.label(text=f"{props.delivery_stage}: {props.delivery_progress}/{total} ({percent:.1f}%)", icon="TIME")
            if props.delivery_info:
                box.label(text=props.delivery_info)
        else:
            col.operator("lightfield.generate_interlaced", text="只生成连续调交织图", icon="IMAGE_DATA")
            col.operator("lightfield.halftone_interlaced", text="从交织图生成菲林 TIFF", icon="TEXTURE")
            col.operator("lightfield.generate_delivery", text="生成当前帧交付文件", icon="RENDER_RESULT")
            if props.delivery_last_output_dir:
                layout.label(text=f"最近输出: {props.delivery_last_output_dir}", icon="FILE_FOLDER")


class LIGHTFIELD_PT_render_actions(Panel):
    bl_label = "渲染操作"
    bl_idname = "LIGHTFIELD_PT_render_actions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "光场"
    bl_parent_id = "LIGHTFIELD_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.light_field_props
        control = get_light_field_control()

        if not control.is_created:
            layout.label(text="请先创建光场相机系统", icon="INFO")
            return

        if props.geometry_dirty:
            box = layout.box()
            box.label(text="渲染前会先应用待更新的相机参数。", icon="INFO")

        col = layout.column(align=True)
        col.scale_y = 1.3

        if props.is_rendering:
            col.operator("lightfield.stop_render", text="停止渲染", icon="CANCEL")
            box = layout.box()
            progress = props.render_progress
            total = props.camera_count
            percent = (progress / total * 100) if total > 0 else 0
            box.label(text=f"进度: {progress}/{total} ({percent:.1f}%)", icon="TIME")

            elapsed = props.render_elapsed_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            secs = int(elapsed % 60)
            time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours > 0 else f"{minutes:02d}:{secs:02d}"
            box.label(text=f"已用时间: {time_str}")
            if props.render_info:
                box.label(text=props.render_info)
        else:
            col.operator("lightfield.render_frame", text="渲染当前帧", icon="RENDER_STILL")
            col.operator("lightfield.render_animation", text="渲染动画", icon="RENDER_ANIMATION")


class LIGHTFIELD_OT_set_first_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_first_camera"
    bl_label = "首个相机"

    def execute(self, context):
        context.scene.light_field_props.active_camera_index = 0
        return {"FINISHED"}


class LIGHTFIELD_OT_set_last_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_last_camera"
    bl_label = "末个相机"

    def execute(self, context):
        props = context.scene.light_field_props
        props.active_camera_index = max(0, props.camera_count - 1)
        return {"FINISHED"}


class LIGHTFIELD_OT_set_prev_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_prev_camera"
    bl_label = "上一个相机"

    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index > 0:
            props.active_camera_index -= 1
        return {"FINISHED"}


class LIGHTFIELD_OT_set_next_camera(bpy.types.Operator):
    bl_idname = "lightfield.set_next_camera"
    bl_label = "下一个相机"

    def execute(self, context):
        props = context.scene.light_field_props
        if props.active_camera_index < props.camera_count - 1:
            props.active_camera_index += 1
        return {"FINISHED"}
