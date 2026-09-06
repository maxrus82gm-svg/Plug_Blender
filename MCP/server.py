"""STDIO MCP server. stdout is reserved for the official SDK protocol."""

import argparse
from typing import Annotated, Any, Literal, TypedDict
from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from blender_client import create_cube as request_cube
from blender_client import get_selected_context as request_selected_context
from blender_client import create_box_at_cursor as request_box
from blender_client import post_modeling_note as request_note
from blender_client import inspect_selected_modifier_changes as request_modifier_changes

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


class NoteResult(TypedDict):
    success: bool
    message: str


class ModifierInspectionResult(TypedDict):
    success: bool
    inspection: dict[str, Any] | None
    message: str


def make_server(session_file=None, feedback_log=None):
    server = FastMCP(
        "Astro Modeler",
        instructions="Use controlled tools in the connected Blender session; heavy geometry stays in Blender. Never auto-retry uncertain creating operations. After substantive mutating modelling operations, call post_modeling_note with brief user-facing engineering feedback, not chain-of-thought; explain missing capabilities when relevant. Also post on user request. Posting a note does not require another note.",
    )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def create_cube() -> CubeResult:
        """Create a 2-unit cube at the world origin in the connected Blender scene. Requires Object Mode."""
        return request_cube(session_file)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def post_modeling_note(status: Literal["OK", "WARNING", "BLOCKED"],
                           summary: Annotated[str, Field(strict=True, min_length=1, max_length=240)],
                           details: Annotated[str, Field(strict=True, max_length=1800)] = "") -> NoteResult:
        """Post engineering feedback to the Blender Sidebar, not internal reasoning.

        Runtime history holds 20 notes, newest first; no scene/undo changes.
        Entire UTF-8 request including token and newline must fit 4096 bytes.
        Use a short self-contained summary of what happened. Details must add
        new useful information rather than restating summary: a verified cause,
        fact, recommendation, missing capability or required next step. Use OK for a completed
        substantive modelling result, WARNING for a risk or recoverable
        limitation, and BLOCKED when safe progress needs a capability or user
        decision. Do not post after routine reads, duplicate the same error, or
        trigger another note from this tool. For one problem, perform at most
        one useful read-only diagnostic, post one WARNING/BLOCKED, then stop
        that dependent branch instead of retrying or posting repeated notes.
        """
        return request_note(status, summary, details, session_file, feedback_log)

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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def inspect_selected_modifier_changes() -> ModifierInspectionResult:
        """Read the last deterministic modifier diff prepared in Blender.

        The user first runs Get Modifiers and Compare Parameters in the Blender
        MODIFIER INSPECTOR. Python compares the chosen modifier with a freshly
        created modifier of the same type in that running Blender; this tool
        does not compute or infer the diff. Explain only changed_properties
        returned here, in simple Russian, following explanation_instruction and
        optional user_context. Keep proven values separate from probable author
        intent; never invent changed properties. If no current comparison exists
        or selection became stale, ask the user to run the local buttons again.
        """
        return request_modifier_changes(session_file)

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Astro Modeler STDIO MCP server")
    parser.add_argument("--session-file", help="Optional explicit session descriptor for development/testing")
    parser.add_argument("--feedback-log", help="Optional diagnostic feedback JSONL path for development/testing")
    args = parser.parse_args()
    make_server(args.session_file, args.feedback_log).run(transport="stdio")
