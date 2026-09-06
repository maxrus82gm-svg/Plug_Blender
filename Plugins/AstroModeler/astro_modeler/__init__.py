"""Astro Modeler: explicitly connect one Blender session to the local MCP bridge."""

bl_info = {
    "name": "Astro Modeler",
    "author": "Plug_Blender contributors",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Astro Modeler",
    "description": "Local MCP modelling tools and Agent Feedback Log",
    "category": "3D View",
}

import bpy
from datetime import datetime
import math
import struct
import textwrap
from collections import deque
from bpy.app.handlers import persistent

from .bridge import Bridge, validate_box_sizes, validate_note

_bridge = None
_last_message = "Stopped"
_timer_ticks = 0
_feedback = deque(maxlen=20)
_expanded_feedback = set()
_next_feedback_id = 1


def _redraw_feedback():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _post_modeling_note(status, summary, details=""):
    """Runtime Python state only: no data blocks, scene mutation or undo operator."""
    global _next_feedback_id
    validate_note(status, summary, details)
    timestamp = datetime.now().strftime("%H:%M:%S")
    if (_feedback and all(_feedback[0][key] == value
                          for key, value in (("status", status), ("summary", summary), ("details", details)))):
        _feedback[0]["last_time"] = timestamp
        _feedback[0]["repeat_count"] += 1
    else:
        _feedback.appendleft({
            "id": _next_feedback_id,
            "first_time": timestamp,
            "last_time": timestamp,
            "repeat_count": 1,
            "status": status,
            "summary": summary,
            "details": details,
        })
        _next_feedback_id += 1
        live_ids = {note["id"] for note in _feedback}
        _expanded_feedback.intersection_update(live_ids)
    _redraw_feedback()


def _clear_feedback():
    """Clear service-only runtime state without a Blender data mutation."""
    global _next_feedback_id
    _feedback.clear()
    _expanded_feedback.clear()
    _next_feedback_id = 1
    _redraw_feedback()


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


def _create_box_at_cursor(size_x, size_y, size_z):
    """Create a world-aligned Box at Cursor position; ignore Cursor rotation."""
    global _last_message
    sizes = validate_box_sizes(size_x, size_y, size_z)
    context = bpy.context
    if context.mode != "OBJECT":
        raise RuntimeError("Switch Blender to Object Mode before creating a box.")
    # Mesh coordinates are float32. Reject overflow/underflow before allocating.
    try:
        half = [struct.unpack('f', struct.pack('f', size / 2))[0] for size in sizes]
        if any(not math.isfinite(h) or h <= 0 for h in half):
            raise ValueError()
        for h in half:
            if not math.isfinite(struct.unpack('f', struct.pack('f', h * 2))[0]):
                raise ValueError()
    except (OverflowError, ValueError):
        raise ValueError("Box sizes exceed Blender Mesh numeric precision/range.") from None
    cursor = context.scene.cursor
    position = cursor.location.copy()
    if not all(math.isfinite(v) for v in position):
        raise ValueError("3D Cursor position must be finite.")
    x, y, z = half
    vertices = [(-x,-y,-z), (x,-y,-z), (x,y,-z), (-x,y,-z),
                (-x,-y,z), (x,-y,z), (x,y,z), (-x,y,z)]
    faces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    selected = list(context.selected_objects)
    active = context.view_layer.objects.active
    mesh = obj = None
    try:
        mesh = bpy.data.meshes.new("Box")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new("Box", mesh)
        obj.location = position
        context.collection.objects.link(obj)
        for previous in selected:
            previous.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        context.view_layer.update()
    except Exception:
        # Remove only this operation's allocations; preserve the original scene.
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None:
            bpy.data.meshes.remove(mesh)
        for previous in selected:
            previous.select_set(True)
        context.view_layer.objects.active = active
        raise
    _last_message = f"Created {obj.name}"
    return obj.name


def _get_selected_context():
    """Read only transforms/context. Never access Object.data or mesh elements.

    matrix_world contains three ROWS of the affine world matrix. Its last
    column is the pivot; columns 0..2 are transformed local X/Y/Z, including
    scale, reflection and shear. The omitted last row is always [0, 0, 0, 1].
    """
    context = bpy.context
    active = context.view_layer.objects.active
    cursor = context.scene.cursor
    return {
        "mode": context.mode,
        "active_object": {"name": active.name, "type": active.type} if active else None,
        "selected_objects": [
            {"name": obj.name, "type": obj.type,
             "matrix_world": [list(row) for row in obj.matrix_world.copy()][:3]}
            for obj in sorted(context.selected_objects, key=lambda obj: obj.name)
        ],
        "3d_cursor": {
            "position": list(cursor.location),
            # Matrix respects the cursor's current Euler/quaternion/axis-angle mode.
            "orientation_wxyz": list(cursor.matrix.to_quaternion().normalized()),
        },
    }


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
    bridge = Bridge(_create_cube, session_file=session_file, get_selected_context=_get_selected_context,
                    create_box_at_cursor=_create_box_at_cursor, post_modeling_note=_post_modeling_note)
    try:
        bridge.start()
        _bridge = bridge
        bpy.app.timers.register(_tick, first_interval=0.05)
    except Exception:
        bridge.stop()
        _bridge = None
        raise
    _last_message = "Listening on localhost"
    _clear_feedback()


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
    _clear_feedback()
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


class ASTRO_MODELER_OT_clear_feedback(bpy.types.Operator):
    bl_idname = "astro_modeler.clear_feedback"
    bl_label = "Clear Feedback"
    bl_description = "Clear the visible Astro Modeler feedback runtime history"

    def execute(self, context):
        _clear_feedback()
        return {"FINISHED"}


class ASTRO_MODELER_OT_toggle_feedback(bpy.types.Operator):
    bl_idname = "astro_modeler.toggle_feedback"
    bl_label = "Toggle Feedback Details"
    bl_description = "Expand or collapse this feedback entry"

    cluster_id: bpy.props.IntProperty()

    def execute(self, context):
        if self.cluster_id in _expanded_feedback:
            _expanded_feedback.remove(self.cluster_id)
        else:
            _expanded_feedback.add(self.cluster_id)
        _redraw_feedback()
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
            layout.label(text="One local session / 4 tools")


class ASTRO_MODELER_PT_feedback(bpy.types.Panel):
    bl_label = "AGENT FEEDBACK LOG"
    bl_idname = "ASTRO_MODELER_PT_feedback"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Astro Modeler"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        layout.operator("astro_modeler.clear_feedback", icon="TRASH")
        if not _feedback:
            layout.label(text="No feedback yet", icon="INFO")
            return
        width = max(12, int((context.region.width - 40) / (8 * context.preferences.system.ui_scale)))
        icons = {"OK": "CHECKMARK", "WARNING": "ERROR", "BLOCKED": "CANCEL"}
        for note in _feedback:
            box = layout.box()
            heading = box.row()
            heading.alert = note["status"] != "OK"
            time_label = note["first_time"]
            if note["last_time"] != note["first_time"]:
                time_label += f'–{note["last_time"]}'
            count_label = f' ×{note["repeat_count"]}' if note["repeat_count"] > 1 else ""
            heading.label(text=f'{time_label}  {note["status"]}{count_label}', icon=icons[note["status"]])
            for paragraph in note["summary"].splitlines():
                for line in textwrap.wrap(paragraph, width=width) or [""]:
                    box.label(text=line)
            if note["details"]:
                box.separator(factor=0.35)
                collapsed = " ".join(note["details"].split())
                preview_limit = max(80, width * 3)
                has_more = len(collapsed) > preview_limit or collapsed != note["details"]
                expanded = note["id"] in _expanded_feedback
                shown = note["details"] if expanded or not has_more else collapsed[:preview_limit - 1].rstrip() + "…"
                for paragraph in shown.splitlines():
                    for line in textwrap.wrap(paragraph, width=width) or [""]:
                        box.label(text=line)
                if has_more:
                    operator = box.operator(
                        "astro_modeler.toggle_feedback",
                        text="Hide details" if expanded else "Show full details",
                        icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
                        emboss=False,
                    )
                    operator.cluster_id = note["id"]


_classes = (ASTRO_MODELER_OT_start, ASTRO_MODELER_OT_stop, ASTRO_MODELER_OT_clear_feedback,
            ASTRO_MODELER_OT_toggle_feedback,
            ASTRO_MODELER_PT_status, ASTRO_MODELER_PT_feedback)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    stop()
    _clear_feedback()
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
