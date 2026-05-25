# Operators module.

from .create_ops import (
    LIGHTFIELD_OT_apply_render_settings,
    LIGHTFIELD_OT_create,
    LIGHTFIELD_OT_delete,
    LIGHTFIELD_OT_update,
)
from .render_ops import (
    LIGHTFIELD_OT_render_animation,
    LIGHTFIELD_OT_render_frame,
    LIGHTFIELD_OT_stop_render,
)


classes = [
    LIGHTFIELD_OT_create,
    LIGHTFIELD_OT_delete,
    LIGHTFIELD_OT_update,
    LIGHTFIELD_OT_apply_render_settings,
    LIGHTFIELD_OT_render_frame,
    LIGHTFIELD_OT_render_animation,
    LIGHTFIELD_OT_stop_render,
]


def register():
    import bpy

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    import bpy

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
