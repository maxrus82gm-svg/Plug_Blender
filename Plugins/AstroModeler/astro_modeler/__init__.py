"""Astro Modeler: explicitly connect one Blender session to the local MCP bridge."""

bl_info = {
    "name": "Astro Modeler",
    "author": "Plug_Blender contributors",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Astro Modeler",
    "description": "Minimal local MCP Create Cube prototype",
    "category": "3D View",
}

import bpy
from bpy.app.handlers import persistent

from .bridge import Bridge

_bridge = None
_last_message = "Stopped"
_timer_ticks = 0


def _create_cube():
    global _last_message
    if bpy.context.mode != "OBJECT":
        raise RuntimeError("Switch Blender to Object Mode before creating a cube.")
    result = bpy.ops.mesh.primitive_cube_add(size=2.0, enter_editmode=False, location=(0.0, 0.0, 0.0))
    obj = bpy.context.view_layer.objects.active
    if "FINISHED" not in result or obj is None or obj.name not in bpy.context.scene.objects:
        raise RuntimeError("Blender did not confirm cube creation. Inspect the scene before retrying.")
    _last_message = f"Created {obj.name}"
    return obj.name


def _tick():
    global _last_message, _timer_ticks
    if _bridge is None:
        return None
    try:
        _bridge.poll()
        _timer_ticks += 1
    except Exception as exc:
        _last_message = f"Bridge stopped: {type(exc).__name__}"
        stop(unregister_timer=False)
        return None
    return 0.05


def start(session_file=None):
    global _bridge, _last_message
    if _bridge is not None:
        raise RuntimeError("Astro Modeler is already running.")
    bridge = Bridge(_create_cube, session_file=session_file)
    try:
        bridge.start()
        _bridge = bridge
        bpy.app.timers.register(_tick, first_interval=0.05)
    except Exception:
        bridge.stop()
        _bridge = None
        raise
    _last_message = "Listening on localhost"


def stop(unregister_timer=True):
    global _bridge
    if unregister_timer and bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


@persistent
def _on_load(_):
    global _last_message
    stop()
    _last_message = "Stopped after file load; reconnect explicitly"


class ASTRO_MODELER_OT_start(bpy.types.Operator):
    bl_idname = "astro_modeler.start"
    bl_label = "Start Integration"

    def execute(self, context):
        try:
            start()
        except Exception as exc:
            self.report({"ERROR"}, f"Cannot start Astro Modeler: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class ASTRO_MODELER_OT_stop(bpy.types.Operator):
    bl_idname = "astro_modeler.stop"
    bl_label = "Stop Integration"

    def execute(self, context):
        global _last_message
        stop()
        _last_message = "Stopped"
        return {"FINISHED"}


class ASTRO_MODELER_PT_status(bpy.types.Panel):
    bl_label = "Astro Modeler"
    bl_idname = "ASTRO_MODELER_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Astro Modeler"

    def draw(self, context):
        layout = self.layout
        layout.label(text=_last_message)
        if _bridge is None:
            layout.operator("astro_modeler.start")
        else:
            layout.operator("astro_modeler.stop")
            layout.label(text="One local session / create_cube")


_classes = (ASTRO_MODELER_OT_start, ASTRO_MODELER_OT_stop, ASTRO_MODELER_PT_status)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    stop()
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
