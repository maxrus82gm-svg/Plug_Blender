"""Verify the ZIP in an isolated Blender user directory and inspect runtime proof.

Run only with Blender's scripts/config/extensions user paths under .runtime.
The interactive runtime fixture must have finished and released the bridge port.
"""

import json
from functools import partial
import os
from pathlib import Path
import runpy

import bpy

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
for variable in ("BLENDER_USER_SCRIPTS", "BLENDER_USER_CONFIG", "BLENDER_USER_EXTENSIONS"):
    path = Path(os.environ[variable]).resolve()
    assert path.is_relative_to(RUNTIME.resolve()), "Use isolated test preferences."

expected_version = runpy.run_path(
    ROOT / "Plugins/AstroModeler/astro_modeler/version.py")["FULL_VERSION"]
archive = ROOT / "dist" / f"astro_modeler-{expected_version}.zip"
assert "FINISHED" in bpy.ops.preferences.addon_install(filepath=str(archive))
assert "FINISHED" in bpy.ops.preferences.addon_enable(module="astro_modeler")
import astro_modeler

assert Path(astro_modeler.__file__).resolve().is_relative_to(RUNTIME.resolve())
assert astro_modeler.FULL_VERSION == expected_version
assert astro_modeler._loaded_version_label() == f"Version: {expected_version}"
astro_modeler.Bridge = partial(astro_modeler.Bridge, port=0)
descriptor = RUNTIME / "install-session.json"
astro_modeler.start(descriptor)
assert descriptor.exists()
assert bpy.app.timers.is_registered(astro_modeler._tick)

# Loading a file must disconnect the previously connected session.
bpy.ops.wm.open_mainfile(filepath=str(RUNTIME / "astro-modeler-smoke.blend"))
assert astro_modeler._bridge is None
assert not descriptor.exists()
assert not bpy.app.timers.is_registered(astro_modeler._tick)

evidence = json.loads((RUNTIME / "codex-result.json").read_text(encoding="utf-8"))
names = [call["structuredContent"]["object_name"] for call in evidence["calls"]]
assert names == ["Cube.001", "Cube.002"]
for name in names:
    obj = bpy.context.scene.objects[name]
    assert obj.type == "MESH"
    assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
    assert tuple(obj.dimensions) == (2.0, 2.0, 2.0)
    assert tuple(obj.location) == (0.0, 0.0, 0.0)

# Reject Edit Mode without modifying the existing mesh.
bpy.ops.object.mode_set(mode="EDIT")
try:
    astro_modeler._create_cube()
except RuntimeError as exc:
    assert "Object Mode" in str(exc)
else:
    raise AssertionError("Edit Mode must be rejected.")
bpy.ops.object.mode_set(mode="OBJECT")
assert len(bpy.context.scene.objects) == 5
selected = astro_modeler._get_selected_context()
assert selected["active_object"]["name"] == "Cube.002"
assert len(selected["selected_objects"][0]["matrix_world"]) == 3
assert "3d_cursor" in selected

astro_modeler.start(descriptor)
astro_modeler.stop()
assert not descriptor.exists()
astro_modeler.start(descriptor)
assert "FINISHED" in bpy.ops.preferences.addon_disable(module="astro_modeler")
assert astro_modeler._bridge is None
assert not descriptor.exists()
assert not bpy.app.timers.is_registered(astro_modeler._tick)
print("PASS: ZIP install/enable and selected context; load disconnect; restart/stop/disable cleanup; Edit Mode rejection; saved cubes match both Codex results")
