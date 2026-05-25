# Light Field Render Plugin for Blender 4.2 LTS
# 光场渲染插件

bl_info = {
    "name": "Light Field Render",
    "author": "Light Field Studio",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Light Field",
    "description": "面向光场显示屏的渲染插件，支持离轴透视相机阵列",
    "category": "Render",
}

import bpy

from . import properties
from . import operators
from . import panels


def register():
    """注册插件"""
    properties.register()
    operators.register()
    panels.register()


def unregister():
    """注销插件"""
    panels.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
