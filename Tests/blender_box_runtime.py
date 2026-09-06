"""Isolated GUI/installed-ZIP fixture for box_runtime.py. No user scene access."""

from functools import partial
import json
import math
import os
from pathlib import Path
import sys
import threading

import bpy

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
assert "--factory-startup" in sys.argv and not bpy.data.filepath
assert not bpy.app.background and bpy.app.version[:3] == (5, 0, 1)
for variable in ("BLENDER_USER_SCRIPTS", "BLENDER_USER_CONFIG", "BLENDER_USER_EXTENSIONS"):
    assert Path(os.environ[variable]).resolve().is_relative_to(RUNTIME.resolve())
for name in ("box-control.json", "box-state.json", "box-events.json", "codex-box-result.json"):
    (RUNTIME / name).unlink(missing_ok=True)
assert "FINISHED" in bpy.ops.preferences.addon_install(filepath=str(ROOT / "dist/astro_modeler-0.1.0.zip"))
assert "FINISHED" in bpy.ops.preferences.addon_enable(module="astro_modeler")
import astro_modeler

assert Path(astro_modeler.__file__).resolve().is_relative_to(RUNTIME.resolve())
astro_modeler.Bridge = partial(astro_modeler.Bridge, port=0)
descriptor = RUNTIME / "box-session.json"
events = []
main_thread = threading.get_ident()
note_checks = []


def fingerprint(obj):
    return (list(map(list, obj.matrix_world)),
            [list(v.co) for v in obj.data.vertices] if obj.type == "MESH" else None)


def checked_box(x, y, z):
    assert threading.get_ident() == main_thread
    before = {obj.name: fingerprint(obj) for obj in bpy.data.objects}
    meshes = len(bpy.data.meshes)
    cursor = [list(row) for row in bpy.context.scene.cursor.matrix]
    position = tuple(bpy.context.scene.cursor.location)
    selection = sorted(o.name for o in bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    try:
        name = original_box(x, y, z)
    except Exception:
        assert {obj.name: fingerprint(obj) for obj in bpy.data.objects} == before
        assert len(bpy.data.meshes) == meshes
        assert sorted(o.name for o in bpy.context.selected_objects) == selection
        assert bpy.context.view_layer.objects.active == active
        raise
    obj = bpy.data.objects[name]
    bpy.context.view_layer.update()
    assert len(bpy.data.objects) == len(before) + 1
    assert len(bpy.data.meshes) == meshes + 1
    assert all(fingerprint(bpy.data.objects[n]) == facts for n, facts in before.items())
    assert [list(row) for row in bpy.context.scene.cursor.matrix] == cursor
    assert tuple(obj.location) == position
    assert tuple(obj.scale) == (1, 1, 1) and obj.parent is None
    assert bpy.context.view_layer.objects.active == obj
    assert bpy.context.selected_objects == [obj]
    assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
    for axis, size in enumerate((x, y, z)):
        coordinates = [v.co[axis] for v in obj.data.vertices]
        assert math.isclose(max(coordinates) - min(coordinates), size, rel_tol=1e-6)
        assert math.isclose(obj.dimensions[axis], size, rel_tol=1e-6)
        assert abs(max(coordinates) + min(coordinates)) < 1e-6
        for row in range(3):
            assert abs(obj.matrix_world[row][axis] - (row == axis)) < 1e-6
    for face in obj.data.polygons:
        assert face.center.dot(face.normal) > 0, "Box normals must point outward"
    events.append({"object_name": name, "position": list(obj.location),
                   "dimensions": list(obj.dimensions), "scale": list(obj.scale),
                   "matrix_world": list(map(list, obj.matrix_world)),
                   "checks": "world axes, mesh extents/center/normals, count, previous objects, cursor, selection, main thread"})
    (RUNTIME / "box-events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    return name


original_box = astro_modeler._create_box_at_cursor
astro_modeler._create_box_at_cursor = checked_box


def checked_note(status, summary, details=""):
    assert threading.get_ident() == main_thread
    before = {obj.name: fingerprint(obj) for obj in bpy.data.objects}
    cursor = list(map(list, bpy.context.scene.cursor.matrix))
    selected = astro_modeler._get_selected_context()
    dirty = bpy.data.is_dirty
    data_counts = (len(bpy.data.scenes), len(bpy.data.meshes), len(bpy.data.texts))
    properties = [dict(block.items()) for block in (*bpy.data.scenes, *bpy.data.objects)]
    original_note(status, summary, details)
    assert {obj.name: fingerprint(obj) for obj in bpy.data.objects} == before
    assert list(map(list, bpy.context.scene.cursor.matrix)) == cursor
    assert astro_modeler._get_selected_context() == selected
    assert bpy.data.is_dirty == dirty
    assert data_counts == (len(bpy.data.scenes), len(bpy.data.meshes), len(bpy.data.texts))
    assert properties == [dict(block.items()) for block in (*bpy.data.scenes, *bpy.data.objects)]
    assert astro_modeler._feedback[0] == dict(status=status, summary=summary, details=details)
    note_checks.append(summary)


original_note = astro_modeler._post_modeling_note
astro_modeler._post_modeling_note = checked_note
astro_modeler.start(descriptor)
assert bpy.app.timers.is_registered(astro_modeler._tick)
nonce = None


def state():
    return {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes),
            "box_calls": len(events), "mode": bpy.context.mode,
            "cursor": list(bpy.context.scene.cursor.location),
            "active": bpy.context.view_layer.objects.active.name}


def control():
    global nonce
    path = RUNTIME / "box-control.json"
    if not path.exists():
        return 0.05
    request = json.loads(path.read_text(encoding="utf-8"))
    if request["nonce"] == nonce:
        return 0.05
    nonce = request["nonce"]
    try:
        action = request["action"]
        if action == "configure":
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            cursor = bpy.context.scene.cursor
            cursor.location = request["position"]
            cursor.rotation_mode = "XYZ"
            cursor.rotation_euler = request.get("rotation", [0, 0, 0])
            bpy.context.scene.unit_settings.scale_length = request.get("unit_scale", 1)
            if request.get("edit"):
                bpy.ops.object.mode_set(mode="EDIT")
        elif action == "cube_check":
            obj = bpy.context.object
            assert obj.name.startswith("Cube") and len(obj.data.vertices) == 8
            assert tuple(obj.location) == (0, 0, 0) and tuple(obj.dimensions) == (2, 2, 2)
        elif action == "prepare_feedback":
            assert not astro_modeler._feedback, "Geometry callbacks must not auto-post feedback"
            bpy.ops.wm.save_as_mainfile(filepath=str(RUNTIME / "feedback-smoke.blend"))
            assert not bpy.data.is_dirty
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.spaces.active.show_region_ui = True
                        area.tag_redraw()
        elif action == "feedback_state":
            pass
        elif action == "feedback_save_load":
            assert astro_modeler._feedback
            bpy.ops.wm.save_as_mainfile(filepath=str(RUNTIME / "feedback-smoke.blend"))
            bpy.ops.wm.open_mainfile(filepath=str(RUNTIME / "feedback-smoke.blend"))
            assert not astro_modeler._feedback and not bpy.data.is_dirty
            assert not descriptor.exists()
            astro_modeler.start(descriptor)
            assert not astro_modeler._feedback
        elif action == "finish":
            astro_modeler.stop()
            assert not descriptor.exists() and not bpy.app.timers.is_registered(astro_modeler._tick)
            astro_modeler.start(descriptor)
            assert not astro_modeler._feedback
            bpy.ops.wm.save_as_mainfile(filepath=str(RUNTIME / "box-smoke.blend"))
            bpy.ops.wm.open_mainfile(filepath=str(RUNTIME / "box-smoke.blend"))
            assert astro_modeler._bridge is None and not descriptor.exists()
            astro_modeler.start(descriptor)
            assert "FINISHED" in bpy.ops.preferences.addon_disable(module="astro_modeler")
            assert astro_modeler._bridge is None and not descriptor.exists()
            assert not bpy.app.timers.is_registered(astro_modeler._tick)
            bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=1)
        else:
            assert action == "snapshot"
        result = {"nonce": nonce, "success": True, **state()}
        if action == "feedback_state":
            result.update(notes=list(astro_modeler._feedback), note_checks=len(note_checks), dirty=bpy.data.is_dirty)
    except Exception as exc:
        result = {"nonce": nonce, "success": False, "error": repr(exc)}
    (RUNTIME / "box-state.json").write_text(json.dumps(result), encoding="utf-8")
    return None if request["action"] == "finish" else 0.05


bpy.app.timers.register(control, first_interval=0.1, persistent=True)
(RUNTIME / "box-api.json").write_text(json.dumps({
    "blender": bpy.app.version_string, "background": bpy.app.background,
    "cursor_location_api": bpy.types.View3DCursor.bl_rna.properties["location"].description,
    "installed_addon": astro_modeler.__file__,
}), encoding="utf-8")
print("BOX FIXTURE READY", flush=True)
