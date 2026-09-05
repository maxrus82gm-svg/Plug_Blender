"""Private GUI fixture for Tests/selected_runtime.py, never a user's scene.

Launch Blender --factory-startup --python-exit-code 1 --python this_file.py.
Fixture control uses local test files, not additional public MCP tools.
"""

from functools import partial
import json
import math
from pathlib import Path
import sys
import threading

import bpy

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
sys.path.insert(0, str(ROOT / "Plugins/AstroModeler"))
import astro_modeler

assert "--factory-startup" in sys.argv and not bpy.data.filepath, "Use a separate factory test session"
# A completed previous run leaves a finish request. Never replay it on restart.
for filename in ("selected-control.json", "selected-state.json", "selected-result.json", "codex-selected-result.json"):
    (RUNTIME / filename).unlink(missing_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
mesh = bpy.context.object
mesh.name = "ContextMesh"
small_mesh = mesh.data
mesh.location = (3, -2, 5)
mesh.rotation_euler = (0.2, -0.3, 0.7)
mesh.scale = (2, -1, 0.5)


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


parent = empty("ContextParent")
parent.location = (8, 1, -2)
parent.rotation_euler = (0, 0.6, 0.2)
parent.scale = (1, 2, 3)
mesh.parent = parent
marker = empty("ContextEmpty")
marker.location = (-4, 6, 2)
marker.rotation_euler = (0, 0, math.pi / 2)
marker.scale = (2, 3, 4)
light = bpy.data.objects.new("ContextLight", bpy.data.lights.new("TestLight", "POINT"))
bpy.context.collection.objects.link(light)
light.location = (-1, 4, 8)
many = [empty(f"Marker.{index:03d}") for index in range(100)]
cursor = bpy.context.scene.cursor
cursor.location = (7, -3, 2.5)
cursor.rotation_mode = "XYZ"
cursor.rotation_euler = (math.pi / 2, 0, 0)
heavy_mesh = None
main_thread = threading.get_ident()
reads = 0
nonce = None
case = None


def facts():
    """Independent fixture evidence; no geometry coordinates leave Blender."""
    selected = sorted(bpy.context.selected_objects, key=lambda obj: obj.name)
    active = bpy.context.view_layer.objects.active
    return {
        "mode": bpy.context.mode,
        "active": active.name if active else None,
        "active_type": active.type if active else None,
        "selected": {obj.name: {"type": obj.type, "matrix": [list(row) for row in obj.matrix_world],
                                "vertices": len(obj.data.vertices) if obj.type == "MESH" else None} for obj in selected},
        "cursor_matrix": [list(row) for row in cursor.matrix],
        "object_count": len(bpy.context.scene.objects),
    }


def write_state():
    state = {"nonce": nonce, "case": case, "background": bpy.app.background,
             "blender": bpy.app.version_string, "read_only_calls_verified": reads,
             "timer_ticks": astro_modeler._timer_ticks, "facts": facts()}
    temporary = RUNTIME / "selected-state.tmp"
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(RUNTIME / "selected-state.json")


original_read = astro_modeler._get_selected_context


def checked_read():
    global reads
    assert threading.get_ident() == main_thread
    before = facts()
    result = original_read()
    assert facts() == before, "Read-only tool changed fixture context"
    reads += 1
    write_state()
    return result


astro_modeler._get_selected_context = checked_read
astro_modeler.Bridge = partial(astro_modeler.Bridge, port=0)
astro_modeler.register()
astro_modeler.start(RUNTIME / "selected-session.json")


def control():
    global nonce, case, heavy_mesh
    path = RUNTIME / "selected-control.json"
    if not path.exists():
        return 0.05
    request = json.loads(path.read_text(encoding="utf-8"))
    if request["nonce"] == nonce:
        return 0.05
    nonce, case = request["nonce"], request["case"]
    if case == "finish":
        astro_modeler.stop()
        bpy.ops.wm.save_as_mainfile(filepath=str(RUNTIME / "selected-context-smoke.blend"))
        write_state()
        bpy.ops.wm.quit_blender()
        return None
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = None
    marker.scale = (0, 3, 4) if case == "zero_scale" else (2, 3, 4)
    if case == "heavy":
        if heavy_mesh is None:
            heavy_mesh = bpy.data.meshes.new("MillionVertices")
            heavy_mesh.vertices.add(1_000_000)
            heavy_mesh.update()
        mesh.data = heavy_mesh
    else:
        mesh.data = small_mesh
    choices = {
        "none": [], "none_active": [], "single": [mesh], "heavy": [mesh],
        "edit_mode": [mesh], "multiple": [mesh, marker, light],
        "zero_scale": [marker], "many": many,
    }
    chosen = choices[case]
    for obj in chosen:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = marker if case in {"multiple", "none_active"} else (chosen[0] if chosen else None)
    bpy.context.view_layer.update()
    if case == "edit_mode":
        bpy.ops.object.mode_set(mode="EDIT")
    write_state()
    return 0.05


bpy.app.timers.register(control, first_interval=0.05)
write_state()
