"""Export tools — render frames/shapes to PNG, SVG, PDF via Penpot exporter."""

from __future__ import annotations

import base64
import logging

from penpot_mcp.services.api import api

logger = logging.getLogger(__name__)


async def export_frame(
    file_id: str,
    page_id: str,
    object_id: str,
    export_type: str = "png",
    scale: float = 1.0,
) -> dict:
    """Export a frame or shape to PNG, SVG, or PDF via Penpot's exporter service.

    The exporter uses headless Chromium to render pixel-perfect output.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
        export_type: Output format — "png", "svg", or "pdf".
        scale: Scale factor (default 1.0, use 2.0 for retina).
    """
    if export_type not in ("png", "svg", "pdf"):
        return {"error": f"Unsupported export type: {export_type}. Use png, svg, or pdf."}

    try:
        resp = await api.export_object(
            file_id=file_id,
            page_id=page_id,
            object_id=object_id,
            export_type=export_type,
            scale=scale,
        )

        if export_type == "svg":
            # SVG is text — return directly
            return {
                "file_id": file_id,
                "object_id": object_id,
                "type": "svg",
                "content": resp.decode("utf-8") if isinstance(resp, bytes) else resp,
            }
        else:
            # PNG/PDF are binary — base64 encode
            content_b64 = base64.b64encode(resp).decode("ascii") if isinstance(resp, bytes) else resp
            return {
                "file_id": file_id,
                "object_id": object_id,
                "type": export_type,
                "content_base64": content_b64,
                "size_bytes": len(resp) if isinstance(resp, bytes) else len(content_b64),
            }
    except Exception as e:
        logger.warning("Export via Penpot exporter failed: %s", e)
        # Fallback: for SVG, use our local transformer
        if export_type == "svg":
            return await _fallback_svg_export(file_id, page_id, object_id)
        return {"error": f"Export failed: {e}. The Penpot exporter service may not be available."}


async def _fallback_svg_export(file_id: str, page_id: str, object_id: str) -> dict:
    """Generate SVG locally from shape data when the exporter is unavailable."""
    from penpot_mcp.transformers.svg import shapes_to_svg_document
    from penpot_mcp.tools.shapes import _get_file_data, _get_page_objects

    file_data = await _get_file_data(file_id)
    objects = _get_page_objects(file_data, page_id)
    root = objects.get(object_id)
    if not root:
        return {"error": f"Shape {object_id} not found on page {page_id}"}

    # Use full shape records rather than get_shape_tree's brief projection;
    # fills, strokes, text content, and radii are required for a useful render.
    shapes: list[dict] = []

    def collect(shape_id: str) -> None:
        shape = objects.get(shape_id)
        if not shape:
            return
        shapes.append(shape)
        for child_id in shape.get("shapes", []):
            collect(child_id)

    collect(object_id)
    w = root.get("width", 1920)
    h = root.get("height", 1080)

    svg = shapes_to_svg_document(shapes, width=w, height=h)
    return {
        "file_id": file_id,
        "object_id": object_id,
        "type": "svg",
        "content": svg,
        "note": "Generated locally from shape data (exporter unavailable)",
    }
async def export_frame_png(
    file_id: str,
    page_id: str,
    object_id: str,
    scale: float = 1.0,
) -> dict:
    """Export a frame or shape to PNG.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
        scale: Scale factor (1.0=normal, 2.0=retina).
    """
    return await export_frame(file_id, page_id, object_id, "png", scale)


async def export_frame_svg(
    file_id: str,
    page_id: str,
    object_id: str,
) -> dict:
    """Export a frame or shape to SVG.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
    """
    return await export_frame(file_id, page_id, object_id, "svg")
