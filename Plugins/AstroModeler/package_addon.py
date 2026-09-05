"""Build a minimal Blender Install from Disk ZIP, without caches or local state."""

from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[2]
source = Path(__file__).resolve().parent / "astro_modeler"
output = root / "dist" / "astro_modeler-0.1.0.zip"
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in ("__init__.py", "bridge.py"):
        archive.write(source / name, f"astro_modeler/{name}")
print(output)
