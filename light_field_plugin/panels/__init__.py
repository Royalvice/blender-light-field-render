# Panels module.

from .main_panel import (
    LIGHTFIELD_OT_set_first_camera,
    LIGHTFIELD_OT_set_last_camera,
    LIGHTFIELD_OT_set_next_camera,
    LIGHTFIELD_OT_set_prev_camera,
    LIGHTFIELD_PT_camera_intrinsics,
    LIGHTFIELD_PT_film_tiff,
    LIGHTFIELD_PT_geometry,
    LIGHTFIELD_PT_main,
    LIGHTFIELD_PT_preview,
    LIGHTFIELD_PT_render_actions,
    LIGHTFIELD_PT_render_settings,
)


classes = [
    LIGHTFIELD_PT_main,
    LIGHTFIELD_PT_geometry,
    LIGHTFIELD_PT_camera_intrinsics,
    LIGHTFIELD_PT_preview,
    LIGHTFIELD_PT_render_settings,
    LIGHTFIELD_PT_film_tiff,
    LIGHTFIELD_PT_render_actions,
    LIGHTFIELD_OT_set_first_camera,
    LIGHTFIELD_OT_set_last_camera,
    LIGHTFIELD_OT_set_prev_camera,
    LIGHTFIELD_OT_set_next_camera,
]


def register():
    import bpy

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    import bpy

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
