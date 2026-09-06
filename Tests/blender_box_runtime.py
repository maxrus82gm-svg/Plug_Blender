"""Isolated GUI/installed-ZIP fixture for box_runtime.py. No user scene access."""

from functools import partial
import json
import math
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
assert "--factory-startup" in sys.argv and not bpy.data.filepath
assert not bpy.app.background and bpy.app.version[:3] == (5, 0, 1)
for variable in ("BLENDER_USER_SCRIPTS", "BLENDER_USER_CONFIG", "BLENDER_USER_EXTENSIONS"):
    assert Path(os.environ[variable]).resolve().is_relative_to(RUNTIME.resolve())
for name in ("box-control.json", "box-state.json", "box-events.json", "codex-box-result.json"):
    (RUNTIME / name).unlink(missing_ok=True)
expected_version = runpy.run_path(
    ROOT / "Plugins/AstroModeler/astro_modeler/version.py")["FULL_VERSION"]
addon_zip = ROOT / "dist" / f"astro_modeler-{expected_version}.zip"
assert "FINISHED" in bpy.ops.preferences.addon_install(filepath=str(addon_zip))
assert "FINISHED" in bpy.ops.preferences.addon_enable(module="astro_modeler")
import astro_modeler

assert Path(astro_modeler.__file__).resolve().is_relative_to(RUNTIME.resolve())
assert astro_modeler.FULL_VERSION == expected_version
assert astro_modeler._loaded_version_label() == f"Version: {expected_version}"
assert astro_modeler._hud_draw_handle is not None
astro_modeler.Bridge = partial(astro_modeler.Bridge, port=0)
descriptor = RUNTIME / "box-session.json"
events = []
main_thread = threading.get_ident()
note_checks = []

preview, hidden = astro_modeler._details_display_lines(
    "Детерминированный preview должен занимать ровно три визуальные строки в узкой панели.", 12)
assert hidden and len(preview) == 3 and preview[-1].endswith("…")
expanded, hidden = astro_modeler._details_display_lines(
    "Детерминированный preview должен занимать ровно три визуальные строки в узкой панели.", 12, True)
assert hidden and len(expanded) > 3 and not expanded[-1].endswith("…")
short, hidden = astro_modeler._details_display_lines("Короткий details.", 30)
assert not hidden and short == ["Короткий details."]


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
    note = astro_modeler._feedback[0]
    assert note["status"] == status and note["summary"] == summary and note["details"] == details
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", note["first_time"])
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", note["last_time"])
    assert type(note["repeat_count"]) is int and note["repeat_count"] >= 1
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
            "active": bpy.context.view_layer.objects.active.name,
            "full_version": astro_modeler.FULL_VERSION}


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
        elif action == "version_check":
            assert astro_modeler.FULL_VERSION == expected_version
            assert astro_modeler._loaded_version_label() == f"Version: {expected_version}"
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
        elif action == "activity_state":
            pass
        elif action == "activity_timeout":
            assert astro_modeler._activity_hud_visible()
            astro_modeler._hud_until = time.monotonic() - 0.01
            assert not astro_modeler._activity_hud_visible()
            assert astro_modeler._hud_timeout_tick() is None
        elif action == "activity_settings":
            before = astro_modeler._get_selected_context()
            dirty = bpy.data.is_dirty
            settings = bpy.context.window_manager.astro_modeler_activity_settings
            settings.show_hud = request["show_hud"]
            settings.text_size = request["text_size"]
            settings.text_color = request["text_color"]
            settings.vertical_position = request["vertical_position"]
            assert astro_modeler._activity_hud_position(1000, 500, 200, 20, 0)[1] == 0
            assert astro_modeler._activity_hud_position(1000, 500, 200, 20, 100)[1] == 480
            assert astro_modeler._get_selected_context() == before and bpy.data.is_dirty == dirty
        elif action == "modifier_prepare":
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            obj = bpy.context.view_layer.objects.active
            assert obj is not None and obj.type == "MESH" and obj in bpy.context.selected_objects
            obj.modifiers.clear()
            obj.modifiers.new("Bevel Default", "BEVEL")
            tuned = obj.modifiers.new("Bevel Tuned", "BEVEL")
            tuned.width = 0.003
            tuned.segments = 6
            tuned.offset_type = "WIDTH"
            settings = bpy.context.window_manager.astro_modeler_inspector_settings
            settings.explanation_context = "Объясни как начинающему."
            assert "FINISHED" in bpy.ops.astro_modeler.get_modifiers()
            assert len(astro_modeler._modifier_targets) == 2
            assert [item["modifier_type"] for item in astro_modeler._modifier_targets] == ["BEVEL", "BEVEL"]
            cached_items = astro_modeler._modifier_enum_items()
            assert cached_items is astro_modeler._modifier_enum_cache
            assert [item[0] for item in cached_items] == ["0", "1"]
            assert [item[1] for item in cached_items] == ["Bevel Default", "Bevel Tuned"]
            stable_ids = [[id(part) for part in item] for item in cached_items]
            for _ in range(25):
                astro_modeler._redraw_feedback()
                repeated = astro_modeler._modifier_enum_items()
                assert repeated is cached_items
                assert [[id(part) for part in item] for item in repeated] == stable_ids
            settings.modifier_target = "1"
        elif action == "modifier_compare":
            obj = bpy.context.view_layer.objects.active
            selected = [item.as_pointer() for item in bpy.context.selected_objects]
            active = obj.as_pointer()
            cursor = list(map(list, bpy.context.scene.cursor.matrix))
            geometry = fingerprint(obj)
            values = [(mod.name, mod.type, mod.width, mod.segments, mod.offset_type)
                      for mod in obj.modifiers]
            dirty_before = bpy.data.is_dirty
            assert "FINISHED" in bpy.ops.astro_modeler.compare_modifier()
            assert [item["property"] for item in astro_modeler._modifier_result["changed_properties"]] == [
                "width", "segments", "offset_type"]
            assert fingerprint(obj) == geometry
            assert [(mod.name, mod.type, mod.width, mod.segments, mod.offset_type)
                    for mod in obj.modifiers] == values
            assert [item.as_pointer() for item in bpy.context.selected_objects] == selected
            assert bpy.context.view_layer.objects.active.as_pointer() == active
            assert list(map(list, bpy.context.scene.cursor.matrix)) == cursor
            assert not any(block.name.startswith("__ASTRO_MODELER_TEMP")
                           for block in (*bpy.data.objects, *bpy.data.meshes))
            target = astro_modeler._modifier_targets[1]
            obj.modifiers[1].name += " stale"
            try:
                try:
                    astro_modeler._resolve_modifier_target()
                except RuntimeError as exc:
                    assert str(exc) == astro_modeler.INSPECTOR_UI_RU["stale_selection"]
                else:
                    raise AssertionError("Stale modifier selection was accepted")
            finally:
                obj.modifiers[1].name = target["modifier_name"]
            (RUNTIME / "modifier-diff.json").write_text(
                json.dumps(astro_modeler._modifier_result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif action == "modifier_format":
            assert astro_modeler.format_display_value(0.10000000149011612) == "0.1"
            assert astro_modeler.format_display_value(0.003000000026077032) == "0.003"
            ui = astro_modeler.INSPECTOR_UI_RU
            assert astro_modeler.ASTRO_MODELER_PT_modifier_inspector.bl_label == "АНАЛИЗ МОДИФИКАТОРОВ"
            assert astro_modeler.ASTRO_MODELER_OT_get_modifiers.bl_label == "Получить модификаторы"
            assert astro_modeler.ASTRO_MODELER_OT_compare_modifier.bl_label == "Сравнить параметры"
            assert ui["default"] == "По умолчанию" and ui["current"] == "Текущее"
            assert "width" not in ui.values() and "BEVEL" not in ui.values()
        elif action == "clear_activity":
            before = astro_modeler._get_selected_context()
            dirty = bpy.data.is_dirty
            assert "FINISHED" in bpy.ops.astro_modeler.clear_activity()
            assert not astro_modeler._activity_counts and astro_modeler._last_activity is None
            assert astro_modeler._get_selected_context() == before and bpy.data.is_dirty == dirty
        elif action == "toggle_first_feedback":
            note = astro_modeler._feedback[0]
            before = astro_modeler._get_selected_context()
            dirty = bpy.data.is_dirty
            assert "FINISHED" in bpy.ops.astro_modeler.toggle_feedback(cluster_id=note["id"])
            assert note["id"] in astro_modeler._expanded_feedback
            assert astro_modeler._get_selected_context() == before and bpy.data.is_dirty == dirty
            assert "FINISHED" in bpy.ops.astro_modeler.toggle_feedback(cluster_id=note["id"])
            assert note["id"] not in astro_modeler._expanded_feedback
        elif action == "clear_feedback":
            before = {obj.name: fingerprint(obj) for obj in bpy.data.objects}
            cursor = list(map(list, bpy.context.scene.cursor.matrix))
            selected = astro_modeler._get_selected_context()
            dirty = bpy.data.is_dirty
            data_counts = (len(bpy.data.scenes), len(bpy.data.meshes), len(bpy.data.texts))
            properties = [dict(block.items()) for block in (*bpy.data.scenes, *bpy.data.objects)]
            assert "FINISHED" in bpy.ops.astro_modeler.clear_feedback()
            assert not astro_modeler._feedback
            assert {obj.name: fingerprint(obj) for obj in bpy.data.objects} == before
            assert list(map(list, bpy.context.scene.cursor.matrix)) == cursor
            assert astro_modeler._get_selected_context() == selected
            assert bpy.data.is_dirty == dirty
            assert data_counts == (len(bpy.data.scenes), len(bpy.data.meshes), len(bpy.data.texts))
            assert properties == [dict(block.items()) for block in (*bpy.data.scenes, *bpy.data.objects)]
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
            assert astro_modeler._hud_draw_handle is None
            bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=1)
        else:
            assert action == "snapshot"
        result = {"nonce": nonce, "success": True, **state()}
        if action == "feedback_state":
            result.update(notes=list(astro_modeler._feedback), note_checks=len(note_checks), dirty=bpy.data.is_dirty)
        if action in {"activity_state", "activity_timeout", "activity_settings", "clear_activity"}:
            settings = getattr(bpy.context.window_manager, "astro_modeler_activity_settings", None)
            result.update(activity_counts=dict(astro_modeler._activity_counts),
                          last_activity=astro_modeler._last_activity,
                          hud_text=astro_modeler._activity_hud_text(),
                          hud_handler=astro_modeler._hud_draw_handle is not None,
                          hud_settings=None if settings is None else {
                              "show_hud": settings.show_hud,
                              "text_size": settings.text_size,
                              "text_color": list(settings.text_color),
                              "vertical_position": settings.vertical_position,
                          }, dirty=bpy.data.is_dirty)
        if action in {"modifier_prepare", "modifier_compare"}:
            result.update(modifier_targets=list(astro_modeler._modifier_targets),
                          modifier_result=astro_modeler._modifier_result,
                          inspector_message=astro_modeler._inspector_message)
            if action == "modifier_compare":
                result.update(dirty_before=dirty_before, dirty_after=bpy.data.is_dirty)
    except Exception as exc:
        result = {"nonce": nonce, "success": False, "error": repr(exc)}
    (RUNTIME / "box-state.json").write_text(json.dumps(result), encoding="utf-8")
    return None if request["action"] == "finish" else 0.05


bpy.app.timers.register(control, first_interval=0.1, persistent=True)
(RUNTIME / "box-api.json").write_text(json.dumps({
    "blender": bpy.app.version_string, "background": bpy.app.background,
    "cursor_location_api": bpy.types.View3DCursor.bl_rna.properties["location"].description,
    "installed_addon": astro_modeler.__file__, "installed_zip": str(addon_zip),
    "full_version": astro_modeler.FULL_VERSION,
}), encoding="utf-8")
print("BOX FIXTURE READY", flush=True)
