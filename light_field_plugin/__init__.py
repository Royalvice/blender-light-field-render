# Light Field Render Plugin for Blender 4.2 LTS
# 光场渲染插件

bl_info = {
    "name": "Light Field Render",
    "author": "Light Field Studio",
    "version": (0, 1, 16),
    "blender": (4, 2, 0),
    "location": "3D 视图 > 侧边栏 > 光场",
    "description": "面向光场显示与多视角输出的渲染插件，支持离轴透视相机阵列",
    "category": "Render",
}

import bpy

from . import operators
from . import panels
from . import properties


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
