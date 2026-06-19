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
from .delivery_ops import (
    LIGHTFIELD_OT_generate_delivery,
    LIGHTFIELD_OT_generate_interlaced,
    LIGHTFIELD_OT_halftone_interlaced,
    LIGHTFIELD_OT_stop_delivery,
)


classes = [
    LIGHTFIELD_OT_create,
    LIGHTFIELD_OT_delete,
    LIGHTFIELD_OT_update,
    LIGHTFIELD_OT_apply_render_settings,
    LIGHTFIELD_OT_render_frame,
    LIGHTFIELD_OT_render_animation,
    LIGHTFIELD_OT_stop_render,
    LIGHTFIELD_OT_generate_delivery,
    LIGHTFIELD_OT_generate_interlaced,
    LIGHTFIELD_OT_halftone_interlaced,
    LIGHTFIELD_OT_stop_delivery,
]


def register():
    import bpy

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    import bpy

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
