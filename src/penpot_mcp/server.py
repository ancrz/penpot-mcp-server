"""Penpot MCP Server — AI-powered design tool access via Model Context Protocol."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.responses import FileResponse, JSONResponse

from penpot_mcp.config import settings
from penpot_mcp.services.api import api
from penpot_mcp.services.db import db

logger = logging.getLogger(__name__)


mcp = FastMCP(
    "Penpot MCP",
    instructions=(
        "MCP server for self-hosted Penpot — provides AI agents with full access "
        "to design files, shapes, components, design tokens, comments, and more. "
        "Reads from PostgreSQL for speed, writes via Penpot RPC API for safety."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
)


# ═══════════════════════════════════════════════════════════════
# Root health check
# ═══════════════════════════════════════════════════════════════


@mcp.custom_route("/", methods=["GET"])
async def root(request):
    """Root health check endpoint."""
    return JSONResponse({"service": "Penpot MCP", "status": "ok", "version": "0.1.0"})


# ═══════════════════════════════════════════════════════════════
# Plugin: Static file serving for Penpot browser plugin
# Load via Penpot: Main Menu -> Plugin Manager -> http://localhost:8787/plugin/manifest.json
# ═══════════════════════════════════════════════════════════════

_PLUGIN_DIR = Path(__file__).parent / "plugin"


@mcp.custom_route("/plugin/manifest.json", methods=["GET", "OPTIONS"])
async def plugin_manifest(request):
    """Serve the Penpot plugin manifest."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return FileResponse(
        _PLUGIN_DIR / "manifest.json",
        media_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/plugin.js", methods=["GET", "OPTIONS"])
async def plugin_js(request):
    """Serve the Penpot plugin JavaScript."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return FileResponse(
        _PLUGIN_DIR / "plugin.js",
        media_type="application/javascript",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/ui.html", methods=["GET", "OPTIONS"])
async def plugin_ui(request):
    """Serve the Penpot plugin UI panel."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return FileResponse(
        _PLUGIN_DIR / "ui.html",
        media_type="text/html",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/config.json", methods=["GET", "OPTIONS"])
async def plugin_config(request):
    """Serve dynamic plugin configuration (WebSocket URL)."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return JSONResponse(
        {"ws_url": settings.plugin_ws_url, "version": "1.0.0"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


# ═══════════════════════════════════════════════════════════════
# Category 1: Projects & Teams
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def list_teams() -> str:
    """List all teams in the Penpot instance with member and project counts."""
    from penpot_mcp.tools.projects import list_teams as _list_teams

    result = await _list_teams()
    return json.dumps(result, indent=2)


@mcp.tool()
async def list_projects(team_id: str | None = None) -> str:
    """List projects, optionally filtered by team ID.

    Args:
        team_id: Filter by team UUID. Omit to list all projects.
    """
    from penpot_mcp.tools.projects import list_projects as _list_projects

    result = await _list_projects(team_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def list_files(project_id: str) -> str:
    """List all files in a project.

    Args:
        project_id: The project UUID.
    """
    from penpot_mcp.tools.projects import list_files as _list_files

    result = await _list_files(project_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def search_files(query: str) -> str:
    """Search files by name across all projects.

    Args:
        query: Search term (case-insensitive partial match).
    """
    from penpot_mcp.tools.projects import search_files as _search_files

    result = await _search_files(query)
    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 2: File Operations
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_file_summary(file_id: str) -> str:
    """Get detailed metadata for a file (counts, team, project, versions).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_summary as _get_file_summary

    result = await _get_file_summary(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_file_pages(file_id: str) -> str:
    """Get all pages in a file with their object counts.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_pages as _get_file_pages

    result = await _get_file_pages(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_file_history(file_id: str, limit: int = 20) -> str:
    """Get revision history of a file.

    Args:
        file_id: The file UUID.
        limit: Max entries to return (default 20).
    """
    from penpot_mcp.tools.files import get_file_history as _get_file_history

    result = await _get_file_history(file_id, limit)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_file_libraries(file_id: str) -> str:
    """List shared libraries linked to a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_libraries as _get_file_libraries

    result = await _get_file_libraries(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def create_project(team_id: str, name: str) -> str:
    """Create a new project in a team.

    Args:
        team_id: The team UUID.
        name: Project name.
    """
    from penpot_mcp.tools.files import create_project as _create_project

    result = await _create_project(team_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_file(project_id: str, name: str) -> str:
    """Create a new design file in a project.

    Args:
        project_id: The project UUID.
        name: File name.
    """
    from penpot_mcp.tools.files import create_file as _create_file

    result = await _create_file(project_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def rename_file(file_id: str, name: str) -> str:
    """Rename an existing file.

    Args:
        file_id: The file UUID.
        name: New name.
    """
    from penpot_mcp.tools.files import rename_file as _rename_file

    result = await _rename_file(file_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def duplicate_file(file_id: str, name: str | None = None) -> str:
    """Duplicate an existing file.

    Args:
        file_id: The file UUID.
        name: Optional name for the copy.
    """
    from penpot_mcp.tools.files import duplicate_file as _duplicate_file

    result = await _duplicate_file(file_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def delete_file(file_id: str) -> str:
    """Delete a file (moves to trash).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import delete_file as _delete_file

    result = await _delete_file(file_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 3: Shapes — Reading
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_page_objects(
    file_id: str, page_id: str, shape_type: str | None = None
) -> str:
    """List all objects on a page, optionally filtered by type.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_type: Filter — rect, circle, frame, text, group, path, image, svg-raw, bool.
    """
    from penpot_mcp.gateway import gateway

    result = await gateway.execute_intent(
        "get_page_objects", file_id=file_id, page_id=page_id, shape_type=shape_type
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_shape_tree(
    file_id: str, page_id: str, root_id: str | None = None, depth: int = 3
) -> str:
    """Get the hierarchical tree of shapes on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        root_id: Start from this shape ID (omit for page root).
        depth: Max tree depth (default 3).
    """
    from penpot_mcp.gateway import gateway

    result = await gateway.execute_intent(
        "get_shape_tree", file_id=file_id, page_id=page_id, root_id=root_id, depth=depth
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_shape_details(file_id: str, page_id: str, shape_id: str) -> str:
    """Get full details of a specific shape (fills, strokes, layout, text content).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details

    result = await _get_shape_details(file_id, page_id, shape_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def search_shapes(
    file_id: str, page_id: str, query: str, search_type: str = "name"
) -> str:
    """Search shapes by name or text content on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        query: Search term (case-insensitive).
        search_type: "name" or "text".
    """
    from penpot_mcp.tools.shapes import search_shapes as _search_shapes

    result = await _search_shapes(file_id, page_id, query, search_type)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_shape_css(file_id: str, page_id: str, shape_id: str) -> str:
    """Get CSS representation of a shape's visual properties.

    Converts Penpot fills, strokes, shadows, layout etc. to CSS.
    Inspired by Framelink/Figma's code generation approach.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details
    from penpot_mcp.transformers.css import shape_to_css_string

    shape = await _get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return json.dumps(shape)
    css = shape_to_css_string(shape)
    return json.dumps(
        {"shape_id": shape_id, "name": shape.get("name"), "css": css}, indent=2
    )


@mcp.tool()
async def get_shape_svg(file_id: str, page_id: str, shape_id: str) -> str:
    """Get SVG representation of a shape.

    Leverages Penpot's native SVG format for direct conversion.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details
    from penpot_mcp.transformers.svg import shape_to_svg

    shape = await _get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return json.dumps(shape)
    svg = shape_to_svg(shape)
    return json.dumps(
        {"shape_id": shape_id, "name": shape.get("name"), "svg": svg}, indent=2
    )


# ═══════════════════════════════════════════════════════════════
# Category 4: Components & Design Tokens
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_component_instances(file_id: str) -> str:
    """List all components defined in a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_component_instances as _get_components

    result = await _get_components(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_design_tokens(file_id: str) -> str:
    """Get consolidated design tokens (colors, typographies, component count) from a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_design_tokens as _get_design_tokens

    result = await _get_design_tokens(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_colors_library(file_id: str) -> str:
    """Get all colors defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_colors_library as _get_colors

    result = await _get_colors(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_typography_library(file_id: str) -> str:
    """Get all typographies defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_typography_library as _get_typography

    result = await _get_typography(file_id)
    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 5: Comments & Collaboration
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_comments(file_id: str, resolved: bool | None = None) -> str:
    """Get all comments on a file.

    Args:
        file_id: The file UUID.
        resolved: Filter — True=resolved, False=unresolved, omit=all.
    """
    from penpot_mcp.tools.comments import get_comments as _get_comments

    result = await _get_comments(file_id, resolved)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_active_users(file_id: str) -> str:
    """Get users currently editing a file (real-time presence).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.comments import get_active_users as _get_active_users

    result = await _get_active_users(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_share_links(file_id: str) -> str:
    """List share links for a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.comments import get_share_links as _get_share_links

    result = await _get_share_links(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def create_comment(
    file_id: str,
    page_id: str,
    content: str,
    x: float,
    y: float,
    frame_id: str | None = None,
) -> str:
    """Create a new comment on a page at a specific position.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        content: Comment text.
        x: X position.
        y: Y position.
        frame_id: Optional frame to attach the comment to.
    """
    from penpot_mcp.tools.comments import create_comment as _create_comment

    result = await _create_comment(file_id, page_id, content, x, y, frame_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def reply_to_comment(thread_id: str, content: str) -> str:
    """Reply to an existing comment thread.

    Args:
        thread_id: The comment thread UUID.
        content: Reply text.
    """
    from penpot_mcp.tools.comments import reply_to_comment as _reply

    result = await _reply(thread_id, content)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def resolve_comment(thread_id: str, resolved: bool = True) -> str:
    """Resolve or unresolve a comment thread.

    Args:
        thread_id: The comment thread UUID.
        resolved: True to resolve, False to unresolve.
    """
    from penpot_mcp.tools.comments import resolve_comment as _resolve

    result = await _resolve(thread_id, resolved)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 6: Media & Fonts
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def list_media_assets(file_id: str) -> str:
    """List all media assets (images) in a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.media import list_media_assets as _list_media

    result = await _list_media(file_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def list_fonts(team_id: str) -> str:
    """List custom fonts uploaded to a team.

    Args:
        team_id: The team UUID.
    """
    from penpot_mcp.tools.media import list_fonts as _list_fonts

    result = await _list_fonts(team_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def upload_media(file_id: str, name: str, url: str) -> str:
    """Upload an image to a file from a URL.

    Args:
        file_id: The file UUID.
        name: Name for the media asset.
        url: Public URL of the image.
    """
    from penpot_mcp.tools.media import upload_media as _upload

    result = await _upload(file_id, name, url)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 7: Database & Advanced
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def query_database(sql: str) -> str:
    """Execute a read-only SQL query against the Penpot database.

    Power tool for advanced queries. Only SELECT statements allowed.

    Args:
        sql: SQL SELECT query.
    """
    from penpot_mcp.tools.database import query_database as _query

    result = await _query(sql)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_webhooks(team_id: str) -> str:
    """List webhooks configured for a team.

    Args:
        team_id: The team UUID.
    """
    from penpot_mcp.tools.database import get_webhooks as _get_webhooks

    result = await _get_webhooks(team_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_profile() -> str:
    """Get the authenticated user's profile information."""
    result = await api.get_profile()
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8: Snapshots
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def create_snapshot(file_id: str, label: str) -> str:
    """Create a named snapshot (version) of a file.

    Args:
        file_id: The file UUID.
        label: Label for the snapshot.
    """
    result = await api.create_snapshot(file_id, label)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def get_snapshots(file_id: str) -> str:
    """List all snapshots of a file.

    Args:
        file_id: The file UUID.
    """
    result = await api.get_snapshots(file_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8b: Export
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def export_frame_png(
    file_id: str, page_id: str, object_id: str, scale: float = 1.0
) -> str:
    """Export a frame or shape to PNG via Penpot's exporter (headless Chromium).

    Returns base64-encoded PNG data. Use scale=2.0 for retina quality.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
        scale: Scale factor (1.0=normal, 2.0=retina).
    """
    from penpot_mcp.tools.export import export_frame_png as _export

    result = await _export(file_id, page_id, object_id, scale)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def export_frame_svg(file_id: str, page_id: str, object_id: str) -> str:
    """Export a frame or shape to SVG.

    Uses Penpot's exporter for pixel-perfect SVG; falls back to local
    SVG generation from shape data if the exporter is unavailable.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
    """
    from penpot_mcp.tools.export import export_frame_svg as _export

    result = await _export(file_id, page_id, object_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8c: Advanced Analysis
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_file_raw_data(file_id: str, page_id: str | None = None) -> str:
    """Get the decoded internal data structure of a file.

    Returns the file's pages, components, colors, and typographies
    after full transit decoding. Useful for debugging and advanced automation.

    Args:
        file_id: The file UUID.
        page_id: Optional — returns only that page's data if provided.
    """
    from penpot_mcp.tools.advanced import get_file_raw_data as _get

    result = await _get(file_id, page_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def compare_revisions(
    file_id: str, revn_from: int, revn_to: int | None = None
) -> str:
    """Compare two revisions of a file to see what changed.

    Shows the change operations (add-obj, mod-obj, del-obj, etc.)
    between two revision numbers. Useful for understanding edit history.

    Args:
        file_id: The file UUID.
        revn_from: Starting revision number.
        revn_to: Ending revision number (default: latest).
    """
    from penpot_mcp.tools.advanced import compare_revisions as _compare

    result = await _compare(file_id, revn_from, revn_to)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 9: Shape Creation
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def create_rectangle(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 100,
    height: float = 100,
    name: str = "Rectangle",
    fill_color: str = "#B1B2B5",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    parent_id: str | None = None,
) -> str:
    """Create a rectangle shape on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 100).
        height: Height in pixels (default 100).
        name: Shape name (default "Rectangle").
        fill_color: Fill color hex (default "#B1B2B5").
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Optional stroke color hex.
        stroke_width: Stroke width in pixels (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        border_radius: Corner radius for all corners (default 0).
        parent_id: Parent shape ID. If omitted, adds to root frame.
    """
    from penpot_mcp.tools.create import create_rectangle as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        border_radius,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_frame(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 300,
    height: float = 300,
    name: str = "Frame",
    fill_color: str = "#FFFFFF",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    clip_content: bool = True,
    parent_id: str | None = None,
) -> str:
    """Create a frame (artboard/container) that can hold child shapes.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 300).
        height: Height in pixels (default 300).
        name: Frame name (default "Frame").
        fill_color: Background color hex (default "#FFFFFF").
        fill_opacity: Background opacity 0-1 (default 1.0).
        stroke_color: Optional border color hex.
        stroke_width: Border width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        border_radius: Corner radius (default 0).
        clip_content: Clip children at frame bounds (default true).
        parent_id: Parent frame ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_frame as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        border_radius,
        clip_content,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_ellipse(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 100,
    height: float = 100,
    name: str = "Ellipse",
    fill_color: str = "#B1B2B5",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create an ellipse (or circle if width==height) on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 100).
        height: Height in pixels (default 100).
        name: Shape name (default "Ellipse").
        fill_color: Fill color hex (default "#B1B2B5").
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Optional stroke color hex.
        stroke_width: Stroke width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_ellipse as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_text(
    file_id: str,
    page_id: str,
    text: str = "Text",
    x: float = 0,
    y: float = 0,
    width: float | None = None,
    height: float | None = None,
    name: str | None = None,
    font_family: str = "sourcesanspro",
    font_size: int = 16,
    font_weight: str = "400",
    font_style: str = "normal",
    fill_color: str = "#000000",
    fill_opacity: float = 1.0,
    text_align: str = "left",
    line_height: float = 1.2,
    letter_spacing: float = 0,
    text_decoration: str = "none",
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create a text shape on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        text: Text content (default "Text").
        x: X position (default 0).
        y: Y position (default 0).
        width: Text box width (auto-calculated if omitted).
        height: Text box height (auto-calculated if omitted).
        name: Shape name (defaults to text content).
        font_family: Font family (default "sourcesanspro").
        font_size: Font size in pixels (default 16).
        font_weight: Weight — "400"=normal, "700"=bold (default "400").
        font_style: Style — "normal" or "italic" (default "normal").
        fill_color: Text color hex (default "#000000").
        fill_opacity: Text opacity 0-1 (default 1.0).
        text_align: Alignment — "left"/"center"/"right"/"justify" (default "left").
        line_height: Line height multiplier (default 1.2).
        letter_spacing: Letter spacing pixels (default 0).
        text_decoration: "none"/"underline"/"line-through" (default "none").
        opacity: Overall shape opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_text as _create

    result = await _create(
        file_id,
        page_id,
        text,
        x,
        y,
        width,
        height,
        name,
        font_family,
        font_size,
        font_weight,
        font_style,
        fill_color,
        fill_opacity,
        text_align,
        line_height,
        letter_spacing,
        text_decoration,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_path(
    file_id: str,
    page_id: str,
    segments: list[dict],
    name: str = "Path",
    fill_color: str | None = None,
    fill_opacity: float = 1.0,
    stroke_color: str = "#000000",
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create a vector path shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        segments: Path segments — list of {command, x, y} dicts. Commands: "M"=move, "L"=line, "C"=curve (with c1x,c1y,c2x,c2y), "Z"=close.
        name: Shape name (default "Path").
        fill_color: Optional fill color hex.
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Stroke color hex (default "#000000").
        stroke_width: Stroke width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_path as _create

    result = await _create(
        file_id,
        page_id,
        segments,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_group(
    file_id: str,
    page_id: str,
    shape_ids: list[str],
    name: str = "Group",
    parent_id: str | None = None,
) -> str:
    """Group existing shapes together.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_ids: List of shape UUIDs to group.
        name: Group name (default "Group").
        parent_id: Parent shape ID. If omitted, uses root.
    """
    from penpot_mcp.tools.create import create_group as _create

    result = await _create(file_id, page_id, shape_ids, name, parent_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_component(
    file_id: str,
    page_id: str,
    shape_id: str,
    name: str | None = None,
) -> str:
    """Convert a shape/frame into a reusable component.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to convert.
        name: Component name (keeps current name if omitted).
    """
    from penpot_mcp.tools.create import create_component as _create

    result = await _create(file_id, page_id, shape_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def create_page(file_id: str, name: str = "New Page") -> str:
    """Add a new page to a file.

    Args:
        file_id: The file UUID.
        name: Page name (default "New Page").
    """
    from penpot_mcp.tools.create import create_page as _create

    result = await _create(file_id, name)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 10: Shape Modification
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def modify_shape(file_id: str, page_id: str, shape_id: str, attrs: dict) -> str:
    """Modify arbitrary attributes of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        attrs: Dict of kebab-case attributes to set. E.g. {"opacity": 0.5, "name": "New Name"}.
    """
    from penpot_mcp.tools.modify import modify_shape as _modify

    result = await _modify(file_id, page_id, shape_id, attrs)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def move_shape(
    file_id: str, page_id: str, shape_id: str, x: float, y: float
) -> str:
    """Move a shape to a new position.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        x: New X position.
        y: New Y position.
    """
    from penpot_mcp.tools.modify import move_shape as _move

    result = await _move(file_id, page_id, shape_id, x, y)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def resize_shape(
    file_id: str, page_id: str, shape_id: str, width: float, height: float
) -> str:
    """Resize a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        width: New width in pixels.
        height: New height in pixels.
    """
    from penpot_mcp.tools.modify import resize_shape as _resize

    result = await _resize(file_id, page_id, shape_id, width, height)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def delete_shape(file_id: str, page_id: str, shape_id: str) -> str:
    """Delete a shape from a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.modify import delete_shape as _delete

    result = await _delete(file_id, page_id, shape_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def rename_shape(file_id: str, page_id: str, shape_id: str, name: str) -> str:
    """Rename a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        name: New name.
    """
    from penpot_mcp.tools.modify import rename_shape as _rename

    result = await _rename(file_id, page_id, shape_id, name)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_fill(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#B1B2B5",
    opacity: float = 1.0,
) -> str:
    """Set the fill color of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Fill color hex (default "#B1B2B5").
        opacity: Fill opacity 0-1 (default 1.0).
    """
    from penpot_mcp.tools.modify import set_fill as _set_fill

    result = await _set_fill(file_id, page_id, shape_id, color, opacity)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_stroke(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#000000",
    width: float = 1.0,
    opacity: float = 1.0,
    style: str = "solid",
) -> str:
    """Set the stroke (border) of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Stroke color hex (default "#000000").
        width: Stroke width pixels (default 1.0).
        opacity: Stroke opacity 0-1 (default 1.0).
        style: "solid"/"dashed"/"dotted"/"mixed" (default "solid").
    """
    from penpot_mcp.tools.modify import set_stroke as _set_stroke

    result = await _set_stroke(file_id, page_id, shape_id, color, width, opacity, style)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_opacity(file_id: str, page_id: str, shape_id: str, opacity: float) -> str:
    """Set the overall opacity of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        opacity: Opacity 0 (transparent) to 1 (opaque).
    """
    from penpot_mcp.tools.modify import set_opacity as _set_opacity

    result = await _set_opacity(file_id, page_id, shape_id, opacity)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_layout(
    file_id: str,
    page_id: str,
    frame_id: str,
    layout_type: str = "flex",
    direction: str = "row",
    gap: float = 0,
    padding: float = 0,
    align_items: str | None = None,
    justify_content: str | None = None,
    wrap: str = "nowrap",
) -> str:
    """Set flex/grid layout on a frame (auto-layout container).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        frame_id: The frame shape UUID.
        layout_type: "flex" or "grid" (default "flex").
        direction: "row"/"column"/"row-reverse"/"column-reverse" (default "row").
        gap: Gap between children pixels (default 0).
        padding: Padding on all sides pixels (default 0).
        align_items: Cross-axis — "start"/"center"/"end"/"stretch".
        justify_content: Main-axis — "start"/"center"/"end"/"space-between"/"space-around"/"space-evenly".
        wrap: "nowrap" or "wrap" (default "nowrap").
    """
    from penpot_mcp.tools.modify import set_layout as _set_layout

    result = await _set_layout(
        file_id,
        page_id,
        frame_id,
        layout_type,
        direction,
        gap,
        padding,
        align_items,
        justify_content,
        wrap,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def reorder_shapes(
    file_id: str,
    page_id: str,
    parent_id: str,
    shape_ids: list[str],
    index: int = 0,
) -> str:
    """Reorder shapes within a parent (z-order).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        parent_id: Parent shape/frame UUID.
        shape_ids: Shape UUIDs to move.
        index: Target index (0=bottom, default 0).
    """
    from penpot_mcp.tools.modify import reorder_shapes as _reorder

    result = await _reorder(file_id, page_id, parent_id, shape_ids, index)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def delete_page(file_id: str, page_id: str) -> str:
    """Delete a page from a file.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
    """
    from penpot_mcp.tools.modify import delete_page as _delete

    result = await _delete(file_id, page_id)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def rename_page(file_id: str, page_id: str, name: str) -> str:
    """Rename a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        name: New name.
    """
    from penpot_mcp.tools.modify import rename_page as _rename

    result = await _rename(file_id, page_id, name)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 10: Interactive Mode (WebSocket)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_active_selection() -> str:
    """Get the UUIDs of the shapes currently selected by the user in the Penpot Plugin.

    This requires the user to have the Penpot MCP Plugin open and connected in their browser.
    """
    from penpot_mcp.gateway import gateway

    if not gateway.is_interactive:
        return json.dumps(
            {
                "error": "No active Penpot Plugin connection found. The user must have the plugin open."
            },
            indent=2,
        )

    return json.dumps({"selected_shape_ids": gateway.active_selection}, indent=2)


@mcp.tool()
async def execute_plugin_script(script: str) -> str:
    """Execute a JavaScript snippet directly within the user's Penpot Plugin environment.

    This allows direct manipulation of the canvas using the Penpot Plugin API.

    Args:
        script: The JavaScript code to execute.
    """
    from penpot_mcp.ws_controller import ws_controller

    if not ws_controller.is_connected:
        return json.dumps(
            {"error": "No active Penpot Plugin connection found."}, indent=2
        )

    success = await ws_controller.send_command(script)
    if success:
        return json.dumps(
            {"status": "Script executed (or broadcasted) successfully."}, indent=2
        )
    else:
        return json.dumps({"error": "Failed to send script."}, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 11: Text Operations
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def set_text_content(
    file_id: str,
    page_id: str,
    shape_id: str,
    text: str,
    font_family: str | None = None,
    font_size: int | None = None,
    font_weight: str | None = None,
    fill_color: str | None = None,
    text_align: str | None = None,
) -> str:
    """Replace text content of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        text: New text content.
        font_family: Optional font family override.
        font_size: Optional font size override.
        font_weight: Optional font weight override.
        fill_color: Optional text color override (hex).
        text_align: Optional alignment override.
    """
    from penpot_mcp.tools.text import set_text_content as _set

    result = await _set(
        file_id,
        page_id,
        shape_id,
        text,
        font_family,
        font_size,
        font_weight,
        fill_color,
        text_align,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_font(file_id: str, page_id: str, shape_id: str, font_family: str) -> str:
    """Change the font family of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_family: Font family name (e.g., "sourcesanspro", "roboto").
    """
    from penpot_mcp.tools.text import set_font as _set

    result = await _set(file_id, page_id, shape_id, font_family)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_font_size(
    file_id: str, page_id: str, shape_id: str, font_size: int
) -> str:
    """Change the font size of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_size: Font size in pixels.
    """
    from penpot_mcp.tools.text import set_font_size as _set

    result = await _set(file_id, page_id, shape_id, font_size)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_text_align(file_id: str, page_id: str, shape_id: str, align: str) -> str:
    """Set text alignment.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        align: "left"/"center"/"right"/"justify".
    """
    from penpot_mcp.tools.text import set_text_align as _set

    result = await _set(file_id, page_id, shape_id, align)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def set_text_style(
    file_id: str,
    page_id: str,
    shape_id: str,
    font_weight: str | None = None,
    font_style: str | None = None,
    text_decoration: str | None = None,
) -> str:
    """Set text styling (bold, italic, underline).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_weight: "400"=normal, "700"=bold, etc.
        font_style: "normal" or "italic".
        text_decoration: "none"/"underline"/"line-through".
    """
    from penpot_mcp.tools.text import set_text_style as _set

    result = await _set(
        file_id,
        page_id,
        shape_id,
        font_weight,
        font_style,
        text_decoration,
    )
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


def main():
    """Run the Penpot MCP server."""
    import anyio
    import uvicorn

    logging.basicConfig(
        level=getattr(logging, settings.mcp_log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    async def run():
        starlette_app = mcp.streamable_http_app()
        _orig_lifespan = starlette_app.router.lifespan_context

        @asynccontextmanager
        async def _startup_lifespan(app):
            logger.info("Connecting to Penpot services...")
            await db.connect()
            await api.connect()
            from penpot_mcp.ws_controller import ws_controller
            await ws_controller.start()
            logger.info("Penpot MCP server ready (WS on port %d)", settings.ws_port)
            try:
                async with _orig_lifespan(app):
                    yield
            finally:
                await ws_controller.stop()
                await api.close()
                await db.close()
                logger.info("Penpot MCP server stopped")

        starlette_app.router.lifespan_context = _startup_lifespan

        config = uvicorn.Config(
            starlette_app,
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level=settings.mcp_log_level.lower(),
        )
        await uvicorn.Server(config).serve()

    anyio.run(run)


if __name__ == "__main__":
    main()
