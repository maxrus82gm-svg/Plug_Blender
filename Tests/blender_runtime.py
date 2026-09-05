"""Run with Blender --factory-startup --python Tests/blender_runtime.py.

Explicitly registers a development session; it never searches other Blender
processes or modifies user preferences. Runtime evidence is local and ignored.
"""

import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "Plugins/AstroModeler"))
import astro_modeler

astro_modeler.register()
astro_modeler.start(RUNTIME / "astro-session.json")
initial = set(bpy.context.scene.objects.keys())


def capture():
    created = [obj for obj in bpy.context.scene.objects if obj.name not in initial]
    evidence = {
        "blender": bpy.app.version_string,
        "python": sys.version.split()[0],
        "background": bpy.app.background,
        "timer_registered": bpy.app.timers.is_registered(astro_modeler._tick),
        "timer_ticks": astro_modeler._timer_ticks,
        "created": [{"name": obj.name, "type": obj.type,
                     "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
                     "faces": len(obj.data.polygons) if obj.type == "MESH" else None,
                     "dimensions": list(obj.dimensions)} for obj in created],
    }
    temporary = RUNTIME / "blender-state.tmp"
    temporary.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    temporary.replace(RUNTIME / "blender-state.json")
    if len(created) >= 2:
        bpy.ops.wm.save_as_mainfile(filepath=str(RUNTIME / "astro-modeler-smoke.blend"))
        return None
    return 0.2


bpy.app.timers.register(capture, first_interval=0.2)
