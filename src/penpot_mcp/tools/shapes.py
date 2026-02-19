"""Shape reading tools — page objects, shape tree, shape details, search."""

from __future__ import annotations

from typing import Any

from penpot_mcp.services.api import api
from penpot_mcp.services.transit import decode_transit


def _serialize_shape(shape: dict) -> dict:
    """Extract the most useful properties from a Penpot shape object."""
    return {
        "id": shape.get("id"),
        "name": shape.get("name"),
        "type": shape.get("type"),
        "x": shape.get("x"),
        "y": shape.get("y"),
        "width": shape.get("width"),
        "height": shape.get("height"),
        "rotation": shape.get("rotation", 0),
        "opacity": shape.get("opacity", 1),
        "hidden": shape.get("hidden", False),
        "blocked": shape.get("blocked", False),
        "parent_id": shape.get("parent-id"),
        "frame_id": shape.get("frame-id"),
        "fills": shape.get("fills", []),
        "strokes": shape.get("strokes", []),
        "blend_mode": shape.get("blend-mode"),
        "constraints_h": shape.get("constraints-h"),
        "constraints_v": shape.get("constraints-v"),
    }


def _serialize_shape_brief(shape: dict) -> dict:
    """Minimal shape info for listings."""
    return {
        "id": shape.get("id"),
        "name": shape.get("name"),
        "type": shape.get("type"),
        "x": shape.get("x"),
        "y": shape.get("y"),
        "width": shape.get("width"),
        "height": shape.get("height"),
    }


def _decode_shape_obj(obj: Any) -> dict:
    """Decode a shape object that may be a transit-encoded tagged value.

    Penpot shapes with 'fdata/shape-data-type' feature come as
    tagged transit values: [tag_string, {shape_data}].
    The tag may be '~#shape' or a stale cache reference.
    """
    if isinstance(obj, str):
        decoded = decode_transit(obj)
        if isinstance(decoded, dict):
            return decoded
        if isinstance(decoded, list) and len(decoded) == 2:
            return decoded[1] if isinstance(decoded[1], dict) else {}
        return {}
    if isinstance(obj, dict):
        return obj
    # Handle already-decoded tagged value: [tag, {data}]
    if isinstance(obj, list) and len(obj) == 2 and isinstance(obj[1], dict):
        return obj[1]
    return {}


def _get_page_objects(file_data: dict, page_id: str) -> dict[str, dict]:
    """Extract and decode all objects from a page.

    Ensures each shape has its 'id' set from the objects dict key,
    which is the ground truth for shape IDs.
    """
    page = file_data.get("data", {}).get("pages-index", {}).get(page_id, {})
    raw_objects = page.get("objects", {})
    decoded = {}
    for obj_id, obj in raw_objects.items():
        shape = _decode_shape_obj(obj)
        # Ensure id is set from dict key (ground truth)
        shape["id"] = obj_id
        decoded[obj_id] = shape
    return decoded


async def _get_file_data(file_id: str) -> dict:
    """Fetch and return file data dict."""
    return await api.command("get-file", {"id": file_id, "components-v2": True})


async def get_page_objects(
    file_id: str, page_id: str, shape_type: str | None = None
) -> list[dict]:
    """List all objects on a page, optionally filtered by type.

    Args:
        file_id: The file UUID.
        page_id: The page UUID within the file.
        shape_type: Optional filter — one of: rect, circle, frame, text, group, path, image, svg-raw, bool.
    """
    file_data = await _get_file_data(file_id)
    objects = _get_page_objects(file_data, page_id)

    result = []
    for obj_id, obj in objects.items():
        obj_type = obj.get("type", "")
        if shape_type:
            if obj_type != shape_type and obj_type != f":{shape_type}":
                continue
        result.append(_serialize_shape_brief(obj))
    return result


async def get_shape_tree(
    file_id: str, page_id: str, root_id: str | None = None, depth: int = 3
) -> dict:
    """Get the hierarchical tree of shapes on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        root_id: Start from this shape ID. If omitted, starts from root frame.
        depth: Maximum tree depth to traverse (default 3, to avoid huge outputs).
    """
    file_data = await _get_file_data(file_id)
    objects = _get_page_objects(file_data, page_id)

    if not root_id:
        # Root frame uses the zero UUID convention in Penpot
        root_id = "00000000-0000-0000-0000-000000000000"

    def build_tree(shape_id: str, current_depth: int) -> dict | None:
        shape = objects.get(shape_id)
        if not shape:
            return None
        node = _serialize_shape_brief(shape)
        children_ids = shape.get("shapes", [])
        if children_ids and current_depth < depth:
            node["children"] = [
                child
                for cid in children_ids
                if (child := build_tree(cid, current_depth + 1)) is not None
            ]
        elif children_ids:
            node["children_count"] = len(children_ids)
        return node

    tree = build_tree(root_id, 0)
    return tree or {"error": f"Shape {root_id} not found on page {page_id}"}


async def get_shape_details(file_id: str, page_id: str, shape_id: str) -> dict:
    """Get full details of a specific shape including fills, strokes, text content.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to inspect.
    """
    file_data = await _get_file_data(file_id)
    objects = _get_page_objects(file_data, page_id)
    shape = objects.get(shape_id)

    if not shape:
        return {"error": f"Shape {shape_id} not found on page {page_id}"}

    details = _serialize_shape(shape)

    # Add text-specific content
    if shape.get("type") in (":text", "text"):
        details["content"] = shape.get("content")

    # Add layout info
    layout = shape.get("layout")
    if layout:
        details["layout"] = layout
        details["layout_flex_dir"] = shape.get("layout-flex-dir")
        details["layout_gap"] = shape.get("layout-gap")
        details["layout_padding"] = shape.get("layout-padding")
        details["layout_align_items"] = shape.get("layout-align-items")
        details["layout_justify_content"] = shape.get("layout-justify-content")
        details["layout_wrap_type"] = shape.get("layout-wrap-type")

    # Add shadow and blur
    details["shadow"] = shape.get("shadow", [])
    details["blur"] = shape.get("blur")

    # Add border radius
    details["rx"] = shape.get("rx")
    details["ry"] = shape.get("ry")
    details["r1"] = shape.get("r1")
    details["r2"] = shape.get("r2")
    details["r3"] = shape.get("r3")
    details["r4"] = shape.get("r4")

    # Children
    children_ids = shape.get("shapes", [])
    if children_ids:
        details["children"] = [
            _serialize_shape_brief(objects[cid])
            for cid in children_ids
            if cid in objects
        ]

    return details


async def search_shapes(
    file_id: str, page_id: str, query: str, search_type: str = "name"
) -> list[dict]:
    """Search shapes on a page by name or text content.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        query: Search string (case-insensitive).
        search_type: What to search — "name" for shape names, "text" for text content.
    """
    file_data = await _get_file_data(file_id)
    objects = _get_page_objects(file_data, page_id)

    query_lower = query.lower()
    results = []

    for obj_id, obj in objects.items():
        if search_type == "name":
            name = obj.get("name", "")
            if query_lower in name.lower():
                results.append(_serialize_shape_brief(obj))
        elif search_type == "text":
            if obj.get("type") in (":text", "text"):
                content = obj.get("content", {})
                # Penpot text content is a nested structure with paragraphs
                text = _extract_text_content(content)
                if query_lower in text.lower():
                    result = _serialize_shape_brief(obj)
                    result["text_preview"] = text[:200]
                    results.append(result)

    return results


def _extract_text_content(content: Any) -> str:
    """Recursively extract plain text from Penpot text content structure."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text_parts = []
        for child in content.get("children", []):
            text_parts.append(_extract_text_content(child))
        if "text" in content:
            text_parts.append(content["text"])
        return " ".join(text_parts)
    if isinstance(content, list):
        return " ".join(_extract_text_content(c) for c in content)
    return ""
