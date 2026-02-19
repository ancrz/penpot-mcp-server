"""Shape modification tools — move, resize, delete, restyle, layout, reorder."""

from __future__ import annotations

from typing import Any

from penpot_mcp.services.changes import (
    apply_changes,
    build_fill,
    build_shape_geometry,
    build_stroke,
    change_del_obj,
    change_del_page,
    change_mod_obj,
    change_mod_page,
    change_mov_objects,
    set_op,
)


async def modify_shape(
    file_id: str,
    page_id: str,
    shape_id: str,
    attrs: dict,
) -> dict:
    """Modify arbitrary attributes of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to modify.
        attrs: Dictionary of attribute names and values to set. Keys use kebab-case (e.g., "fill-color", "opacity", "name", "hidden", "blocked"). Example: {"opacity": 0.5, "name": "My Shape", "hidden": false}.
    """
    ops = [set_op(k, v) for k, v in attrs.items()]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "modified_attrs": list(attrs.keys())}


async def move_shape(
    file_id: str,
    page_id: str,
    shape_id: str,
    x: float,
    y: float,
) -> dict:
    """Move a shape to a new position.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to move.
        x: New X position.
        y: New Y position.
    """
    ops = [set_op("x", x), set_op("y", y)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "x": x, "y": y}


async def resize_shape(
    file_id: str,
    page_id: str,
    shape_id: str,
    width: float,
    height: float,
) -> dict:
    """Resize a shape to new dimensions.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to resize.
        width: New width in pixels.
        height: New height in pixels.
    """
    ops = [set_op("width", width), set_op("height", height)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "width": width, "height": height}


async def delete_shape(
    file_id: str,
    page_id: str,
    shape_id: str,
) -> dict:
    """Delete a shape from a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to delete.
    """
    change = change_del_obj(page_id, shape_id)
    await apply_changes(file_id, [change])
    return {"deleted": shape_id}


async def rename_shape(
    file_id: str,
    page_id: str,
    shape_id: str,
    name: str,
) -> dict:
    """Rename a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to rename.
        name: New name for the shape.
    """
    ops = [set_op("name", name)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "name": name}


async def set_fill(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#B1B2B5",
    opacity: float = 1.0,
) -> dict:
    """Set the fill color of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Fill color hex string (default "#B1B2B5").
        opacity: Fill opacity 0-1 (default 1.0).
    """
    fills = [build_fill(color=color, opacity=opacity)]
    ops = [set_op("fills", fills)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "fill_color": color, "fill_opacity": opacity}


async def set_stroke(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#000000",
    width: float = 1.0,
    opacity: float = 1.0,
    style: str = "solid",
) -> dict:
    """Set the stroke (border) of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Stroke color hex string (default "#000000").
        width: Stroke width in pixels (default 1.0).
        opacity: Stroke opacity 0-1 (default 1.0).
        style: Stroke style — "solid", "dashed", "dotted", "mixed" (default "solid").
    """
    strokes = [build_stroke(color=color, width=width, opacity=opacity, style=style)]
    ops = [set_op("strokes", strokes)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "stroke_color": color, "stroke_width": width}


async def set_opacity(
    file_id: str,
    page_id: str,
    shape_id: str,
    opacity: float,
) -> dict:
    """Set the overall opacity of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        opacity: Opacity value from 0 (transparent) to 1 (opaque).
    """
    ops = [set_op("opacity", opacity)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "opacity": opacity}


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
) -> dict:
    """Set flex layout on a frame, turning it into an auto-layout container.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        frame_id: The frame shape UUID to configure layout on.
        layout_type: Layout type — "flex" or "grid" (default "flex").
        direction: Flex direction — "row", "column", "row-reverse", "column-reverse" (default "row").
        gap: Gap between children in pixels (default 0).
        padding: Padding on all sides in pixels (default 0).
        align_items: Cross-axis alignment — "start", "center", "end", "stretch" (optional).
        justify_content: Main-axis alignment — "start", "center", "end", "space-between", "space-around", "space-evenly" (optional).
        wrap: Wrap behavior — "nowrap" or "wrap" (default "nowrap").
    """
    ops = [
        set_op("layout", layout_type),
        set_op("layout-flex-dir", direction),
        set_op("layout-gap", {"row-gap": gap, "column-gap": gap}),
        set_op("layout-padding", {
            "p1": padding, "p2": padding, "p3": padding, "p4": padding
        }),
        set_op("layout-wrap-type", wrap),
    ]
    if align_items:
        ops.append(set_op("layout-align-items", align_items))
    if justify_content:
        ops.append(set_op("layout-justify-content", justify_content))

    change = change_mod_obj(page_id, frame_id, ops)
    await apply_changes(file_id, [change])
    return {
        "frame_id": frame_id,
        "layout": layout_type,
        "direction": direction,
        "gap": gap,
    }


async def reorder_shapes(
    file_id: str,
    page_id: str,
    parent_id: str,
    shape_ids: list[str],
    index: int = 0,
) -> dict:
    """Reorder shapes within a parent (change z-order or position).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        parent_id: The parent shape/frame UUID containing the shapes.
        shape_ids: List of shape UUIDs to move.
        index: Target index position within the parent (default 0 = bottom).
    """
    change = change_mov_objects(page_id, parent_id, shape_ids, index)
    await apply_changes(file_id, [change])
    return {
        "parent_id": parent_id,
        "moved_shapes": shape_ids,
        "index": index,
    }


async def delete_page(
    file_id: str,
    page_id: str,
) -> dict:
    """Delete a page from a file.

    Args:
        file_id: The file UUID.
        page_id: The page UUID to delete.
    """
    change = change_del_page(page_id)
    await apply_changes(file_id, [change])
    return {"deleted_page": page_id}


async def rename_page(
    file_id: str,
    page_id: str,
    name: str,
) -> dict:
    """Rename a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID to rename.
        name: New name for the page.
    """
    change = change_mod_page(page_id, name)
    await apply_changes(file_id, [change])
    return {"page_id": page_id, "name": name}
