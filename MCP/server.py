"""STDIO MCP server. stdout is reserved for the official SDK protocol."""

import argparse
from typing import Annotated, TypedDict
from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from blender_client import create_cube as request_cube
from blender_client import get_selected_context as request_selected_context
from blender_client import create_box_at_cursor as request_box

PositiveSize = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

Vector3 = tuple[float, float, float]
Vector4 = tuple[float, float, float, float]
Matrix3x4 = tuple[Vector4, Vector4, Vector4]


class ObjectIdentity(TypedDict):
    name: str
    type: str


class SelectedObject(ObjectIdentity):
    matrix_world: Matrix3x4


class CursorContext(TypedDict):
    position: Vector3
    orientation_wxyz: Vector4


SelectedContext = TypedDict("SelectedContext", {
    "mode": str,
    "active_object": ObjectIdentity | None,
    "selected_objects": list[SelectedObject],
    "3d_cursor": CursorContext,
})


class SelectedContextResult(TypedDict):
    success: bool
    context: SelectedContext | None
    message: str


class CubeResult(TypedDict):
    success: bool
    object_name: str | None
    message: str


def make_server(session_file=None):
    server = FastMCP(
        "Astro Modeler",
        instructions="Use create_cube, create_box_at_cursor or read-only get_selected_context in the explicitly connected Blender session. Heavy geometry stays in Blender. Never automatically retry an uncertain creating operation; inspect the scene first.",
    )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def create_cube() -> CubeResult:
        """Create a 2-unit cube at the world origin in the connected Blender scene. Requires Object Mode."""
        return request_cube(session_file)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def create_box_at_cursor(size_x: PositiveSize, size_y: PositiveSize, size_z: PositiveSize) -> CubeResult:
        """Create a NEW world-aligned Box centered at the current 3D Cursor position.

        Sizes are positive finite Blender units along world X/Y/Z axes;
        no mm/cm or Scene Unit Scale conversion. Mesh dimensions match sizes,
        Object Scale is (1,1,1), origin is centered. Requires Object Mode.
        New Box becomes selected and active; previous selection is deselected.
        Cursor rotation is ignored. Existing geometry/transforms and Cursor
        are unchanged. Never auto-retry.
        """
        return request_box(size_x, size_y, size_z, session_file)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def get_selected_context() -> SelectedContextResult:
        """Read current selected/active objects and 3D Cursor; never return mesh data.

        Objects use matrix_world: 3 ROWS x 4 columns, world space, Blender units.
        Column 3 is pivot position; columns 0/1/2 are local X/Y/Z in world space,
        including scale/shear/reflection. Column lengths give world-axis scale
        magnitudes; normalized nonzero columns give axis directions. These are
        not the local transform channels in Blender's UI. Last row [0,0,0,1]
        is omitted. No redundant Euler/quaternion/scale fields for objects.
        Cursor: world position and normalized quaternion ordered [w,x,y,z].
        Selected objects are sorted by name; active may be null or unselected.
        No selection is a successful empty list. Result grows with selection
        count only, never vertex count; >1 MiB returns an error, no truncation.
        """
        return request_selected_context(session_file)

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Astro Modeler STDIO MCP server")
    parser.add_argument("--session-file", help="Optional explicit session descriptor for development/testing")
    args = parser.parse_args()
    make_server(args.session_file).run(transport="stdio")
