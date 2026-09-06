"""Astro Modeler: explicitly connect one Blender session to the local MCP bridge."""

from .version import FULL_VERSION, PRODUCT_VERSION

bl_info = {
    "name": "Astro Modeler",
    "author": "Plug_Blender contributors",
    # Blender requires bl_info to contain literals so addon_utils can parse it.
    # package_addon.py rejects a build if this compatibility tuple diverges
    # from the authoritative PRODUCT_VERSION in version.py.
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Astro Modeler",
    "description": "Local MCP modelling tools and Agent Feedback Log",
    "category": "3D View",
}

import bpy
import blf
from datetime import datetime
import math
import struct
import textwrap
import time
from collections import deque
from bpy.app.handlers import persistent

from .bridge import Bridge, validate_box_sizes, validate_note

_bridge = None
_last_message = "Stopped"
_timer_ticks = 0
_feedback = deque(maxlen=20)
_expanded_feedback = set()
_next_feedback_id = 1
_activity_tools = ("create_cube", "get_selected_context", "create_box_at_cursor", "post_modeling_note")
_activity_counts = {}
_last_activity = None
_hud_until = 0.0
_hud_draw_handle = None
_hud_timeout_seconds = 3.0


def _redraw_feedback():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _activity_settings():
    manager = getattr(bpy.context, "window_manager", None)
    return getattr(manager, "astro_modeler_activity_settings", None)


def _hud_settings_changed(_self=None, _context=None):
    _redraw_feedback()


def _activity_event(tool_name, outcome):
    """Record one accepted bridge invocation, then update its final outcome."""
    global _last_activity, _hud_until
    if tool_name not in _activity_tools:
        return
    if outcome is None:
        _activity_counts[tool_name] = _activity_counts.get(tool_name, 0) + 1
        outcome = "RUNNING"
    _last_activity = {
        "tool_name": tool_name,
        "call_count": _activity_counts[tool_name],
        "last_time": datetime.now().strftime("%H:%M:%S"),
        "outcome": outcome,
    }
    _hud_until = time.monotonic() + _hud_timeout_seconds
    if not bpy.app.timers.is_registered(_hud_timeout_tick):
        bpy.app.timers.register(_hud_timeout_tick, first_interval=0.25)
    _redraw_feedback()


def _clear_activity(clear_counts=True):
    global _last_activity, _hud_until
    if clear_counts:
        _activity_counts.clear()
    _last_activity = None
    _hud_until = 0.0
    if bpy.app.timers.is_registered(_hud_timeout_tick):
        bpy.app.timers.unregister(_hud_timeout_tick)
    _redraw_feedback()


def _hud_timeout_tick():
    remaining = _hud_until - time.monotonic()
    _redraw_feedback()
    return min(0.25, remaining) if remaining > 0 else None


def _activity_hud_text():
    return None if _last_activity is None else f'Astro Modeler · {_last_activity["tool_name"]}'


def _activity_hud_visible(now=None):
    return _last_activity is not None and (time.monotonic() if now is None else now) < _hud_until


def _activity_hud_position(region_width, region_height, text_width, text_height,
                           vertical_percent):
    x = max(16, (region_width - text_width) / 2)
    y = max(0, region_height - text_height) * vertical_percent / 100
    return x, y


def _draw_activity_hud():
    settings = _activity_settings()
    if settings is None or not settings.show_hud or not _activity_hud_visible():
        return
    text = _activity_hud_text()
    font_id = 0
    blf.size(font_id, settings.text_size)
    blf.color(font_id, *settings.text_color)
    width, height = blf.dimensions(font_id, text)
    region = bpy.context.region
    x, y = _activity_hud_position(
        region.width, region.height, width, height, settings.vertical_position)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


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


def _wrapped_lines(text, width):
    lines = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return lines


def _details_display_lines(details, width, expanded=False):
    """Return native label rows and whether collapsed UI hides any rows."""
    lines = _wrapped_lines(details, width)
    has_more = len(lines) > 3
    if expanded or not has_more:
        return lines, has_more
    preview = lines[:3]
    preview[-1] = preview[-1].rstrip() + "…"
    return preview, True


def _loaded_version_label():
    return f"Version: {FULL_VERSION}"


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
                    create_box_at_cursor=_create_box_at_cursor, post_modeling_note=_post_modeling_note,
                    activity_callback=_activity_event)
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
    _clear_activity(clear_counts=False)
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


class ASTRO_MODELER_OT_clear_activity(bpy.types.Operator):
    bl_idname = "astro_modeler.clear_activity"
    bl_label = "Clear Statistics"
    bl_description = "Clear Astro Modeler runtime tool usage statistics"

    def execute(self, context):
        _clear_activity()
        return {"FINISHED"}


class ASTRO_MODELER_PG_activity_settings(bpy.types.PropertyGroup):
    show_hud: bpy.props.BoolProperty(
        name="Show HUD", default=True, update=_hud_settings_changed)
    text_size: bpy.props.IntProperty(
        name="Text Size", default=24, min=12, max=48, update=_hud_settings_changed)
    text_color: bpy.props.FloatVectorProperty(
        name="Text Color", subtype="COLOR_GAMMA", size=4, min=0.0, max=1.0,
        default=(0.2, 1.0, 0.3, 1.0), update=_hud_settings_changed)
    vertical_position: bpy.props.FloatProperty(
        name="Vertical Position", subtype="PERCENTAGE", default=82.0,
        min=0.0, max=100.0, precision=0, update=_hud_settings_changed)


class ASTRO_MODELER_PT_status(bpy.types.Panel):
    bl_label = "Astro Modeler"
    bl_idname = "ASTRO_MODELER_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Astro Modeler"

    def draw(self, context):
        layout = self.layout
        layout.label(text=_loaded_version_label(), icon="INFO")
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
    bl_order = 2

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
            heading = box.row(align=True)
            heading.alert = note["status"] != "OK"
            heading.label(text=note["status"], icon=icons[note["status"]])
            if note["repeat_count"] > 1:
                heading.label(text=f'×{note["repeat_count"]}')
            time_label = note["first_time"]
            if note["last_time"] != note["first_time"]:
                time_label += f'–{note["last_time"]}'
            box.label(text=time_label, icon="TIME")
            for line in _wrapped_lines(note["summary"], width):
                box.label(text=line)
            if note["details"]:
                box.separator(factor=0.35)
                expanded = note["id"] in _expanded_feedback
                shown, has_more = _details_display_lines(note["details"], width, expanded)
                for line in shown:
                    box.label(text=line)
                if has_more:
                    operator = box.operator(
                        "astro_modeler.toggle_feedback",
                        text="Hide details" if expanded else "Show full details",
                        icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
                        emboss=False,
                    )
                    operator.cluster_id = note["id"]


class ASTRO_MODELER_PT_activity(bpy.types.Panel):
    bl_label = "AGENT ACTIVITY"
    bl_idname = "ASTRO_MODELER_PT_activity"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Astro Modeler"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.astro_modeler_activity_settings
        layout.prop(settings, "show_hud")
        layout.prop(settings, "text_size")
        layout.prop(settings, "text_color")
        layout.prop(settings, "vertical_position")
        layout.separator()
        if _last_activity is None:
            layout.label(text="Last tool: None", icon="INFO")
        else:
            layout.label(text=f'Last tool: {_last_activity["tool_name"]}')
            layout.label(text=f'Result: {_last_activity["outcome"]}')
            layout.label(text=f'Time: {_last_activity["last_time"]}', icon="TIME")
        layout.label(text="TOOL USAGE")
        used = False
        for tool_name in _activity_tools:
            count = _activity_counts.get(tool_name, 0)
            if count:
                layout.label(text=f"{tool_name}  ×{count}")
                used = True
        if not used:
            layout.label(text="No tool calls yet")
        layout.operator("astro_modeler.clear_activity", icon="TRASH")


_classes = (ASTRO_MODELER_OT_start, ASTRO_MODELER_OT_stop, ASTRO_MODELER_OT_clear_feedback,
            ASTRO_MODELER_OT_toggle_feedback, ASTRO_MODELER_OT_clear_activity,
            ASTRO_MODELER_PG_activity_settings,
            ASTRO_MODELER_PT_status, ASTRO_MODELER_PT_activity, ASTRO_MODELER_PT_feedback)


def register():
    global _hud_draw_handle
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.astro_modeler_activity_settings = bpy.props.PointerProperty(
        type=ASTRO_MODELER_PG_activity_settings)
    _hud_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_activity_hud, (), "WINDOW", "POST_PIXEL")
    bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    global _hud_draw_handle
    stop()
    _clear_feedback()
    _clear_activity()
    if _hud_draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_hud_draw_handle, "WINDOW")
        _hud_draw_handle = None
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    if hasattr(bpy.types.WindowManager, "astro_modeler_activity_settings"):
        del bpy.types.WindowManager.astro_modeler_activity_settings
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
