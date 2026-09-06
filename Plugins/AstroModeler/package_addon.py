"""Build a minimal Blender Install from Disk ZIP, without caches or local state."""

from pathlib import Path
import ast
import os
import runpy
import zipfile

root = Path(__file__).resolve().parents[2]
source = Path(__file__).resolve().parent / "astro_modeler"
version = runpy.run_path(source / "version.py")
full_version = version["FULL_VERSION"]

tree = ast.parse((source / "__init__.py").read_text(encoding="utf-8"))
bl_info_node = next(
    node.value
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
)
bl_info = ast.literal_eval(bl_info_node)
if tuple(bl_info["version"]) != tuple(version["PRODUCT_VERSION"]):
    raise SystemExit(
        "bl_info version must match PRODUCT_VERSION in astro_modeler/version.py."
    )

output = root / "dist" / f"astro_modeler-{full_version}.zip"
output.parent.mkdir(exist_ok=True)
try:
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
except FileExistsError:
    raise SystemExit(
        f"Build artifact already exists: {output}\n"
        "Increase BUILD_NUMBER in astro_modeler/version.py before rebuilding."
    ) from None
try:
    with os.fdopen(descriptor, "wb") as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ("__init__.py", "bridge.py", "version.py"):
                archive.write(source / name, f"astro_modeler/{name}")
except BaseException:
    output.unlink(missing_ok=True)
    raise
print(output)
