"""Shape creation tools — rectangle, frame, ellipse, text, path, group, component, page."""

from __future__ import annotations

from typing import Any

from penpot_mcp.services.changes import (
    ROOT_FRAME_ID,
    apply_changes,
    build_fill,
    build_shape_geometry,
    build_stroke,
    build_text_content,
    change_add_obj,
    change_add_page,
    new_uuid,
)


def _base_shape(
    shape_type: str,
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    frame_id: str = ROOT_FRAME_ID,
    parent_id: str | None = None,
    fill_color: str | None = None,
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    r1: float = 0,
    r2: float = 0,
    r3: float = 0,
    r4: float = 0,
    extra: dict | None = None,
) -> dict:
    """Build a base shape dict with geometry and optional styling."""
    shape_id = new_uuid()
    geom = build_shape_geometry(x, y, width, height)

    obj: dict[str, Any] = {
        "id": shape_id,
        "type": shape_type,
        "name": name,
        "frame-id": frame_id,
        "parent-id": parent_id or frame_id,
        **geom,
    }

    if fill_color:
        obj["fills"] = [build_fill(color=fill_color, opacity=fill_opacity)]
    elif shape_type in ("rect", "frame"):
        obj["fills"] = [build_fill()]  # default gray

    if stroke_color:
        obj["strokes"] = [build_stroke(color=stroke_color, width=stroke_width)]
    else:
        obj["strokes"] = []

    if opacity != 1.0:
        obj["opacity"] = opacity

    # Border radius
    if border_radius > 0:
        obj["r1"] = border_radius
        obj["r2"] = border_radius
        obj["r3"] = border_radius
        obj["r4"] = border_radius
    else:
        if r1:
            obj["r1"] = r1
        if r2:
            obj["r2"] = r2
        if r3:
            obj["r3"] = r3
        if r4:
            obj["r4"] = r4

    if extra:
        obj.update(extra)

    return obj


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
) -> dict:
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
    frame_id = parent_id or ROOT_FRAME_ID
    obj = _base_shape(
        "rect", name, x, y, width, height,
        frame_id=frame_id, parent_id=parent_id,
        fill_color=fill_color, fill_opacity=fill_opacity,
        stroke_color=stroke_color, stroke_width=stroke_width,
        opacity=opacity, border_radius=border_radius,
    )
    change = change_add_obj(page_id, frame_id, obj)
    await apply_changes(file_id, [change])
    return {"id": obj["id"], "name": name, "type": "rect"}


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
) -> dict:
    """Create a frame (artboard/container) on a page.

    Frames can contain other shapes and act as layout containers.

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
        stroke_width: Border width in pixels (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        border_radius: Corner radius for all corners (default 0).
        clip_content: Whether to clip child content at frame bounds (default true).
        parent_id: Parent frame ID. If omitted, adds to root frame.
    """
    frame_id = parent_id or ROOT_FRAME_ID
    extra = {
        "shapes": [],
        "hide-in-viewer": False,
    }
    if clip_content:
        extra["clip-content"] = True

    obj = _base_shape(
        "frame", name, x, y, width, height,
        frame_id=frame_id, parent_id=parent_id,
        fill_color=fill_color, fill_opacity=fill_opacity,
        stroke_color=stroke_color, stroke_width=stroke_width,
        opacity=opacity, border_radius=border_radius,
        extra=extra,
    )
    change = change_add_obj(page_id, frame_id, obj)
    await apply_changes(file_id, [change])
    return {"id": obj["id"], "name": name, "type": "frame"}


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
) -> dict:
    """Create an ellipse (circle) shape on a page.

    For a perfect circle, set width == height.

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
        stroke_width: Stroke width in pixels (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root frame.
    """
    frame_id = parent_id or ROOT_FRAME_ID
    obj = _base_shape(
        "circle", name, x, y, width, height,
        frame_id=frame_id, parent_id=parent_id,
        fill_color=fill_color, fill_opacity=fill_opacity,
        stroke_color=stroke_color, stroke_width=stroke_width,
        opacity=opacity,
    )
    change = change_add_obj(page_id, frame_id, obj)
    await apply_changes(file_id, [change])
    return {"id": obj["id"], "name": name, "type": "circle"}


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
) -> dict:
    """Create a text shape on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        text: Text content (default "Text").
        x: X position (default 0).
        y: Y position (default 0).
        width: Text box width. If omitted, auto-calculated from text length.
        height: Text box height. If omitted, auto-calculated from font size.
        name: Shape name. If omitted, uses the text content.
        font_family: Font family (default "sourcesanspro"). Use list_fonts to see available fonts.
        font_size: Font size in pixels (default 16).
        font_weight: Font weight — "400" (normal), "700" (bold), etc. (default "400").
        font_style: Font style — "normal" or "italic" (default "normal").
        fill_color: Text color hex (default "#000000").
        fill_opacity: Text opacity 0-1 (default 1.0).
        text_align: Text alignment — "left", "center", "right", "justify" (default "left").
        line_height: Line height multiplier (default 1.2).
        letter_spacing: Letter spacing in pixels (default 0).
        text_decoration: Text decoration — "none", "underline", "line-through" (default "none").
        opacity: Overall shape opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root frame.
    """
    frame_id = parent_id or ROOT_FRAME_ID
    # Auto-size based on text content
    if width is None:
        width = max(len(text) * font_size * 0.6, 50)
    if height is None:
        height = font_size * line_height * 1.5

    shape_id = new_uuid()
    geom = build_shape_geometry(x, y, width, height)

    content = build_text_content(
        text=text,
        font_family=font_family,
        font_size=str(font_size),
        font_weight=font_weight,
        font_style=font_style,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
        text_align=text_align,
        line_height=line_height,
        letter_spacing=letter_spacing,
        text_decoration=text_decoration,
    )

    obj: dict[str, Any] = {
        "id": shape_id,
        "type": "text",
        "name": name or text[:30],
        "frame-id": frame_id,
        "parent-id": parent_id or frame_id,
        "content": content,
        "grows-type": "auto-width",
        **geom,
    }

    if opacity != 1.0:
        obj["opacity"] = opacity

    change = change_add_obj(page_id, frame_id, obj)
    await apply_changes(file_id, [change])
    return {"id": shape_id, "name": obj["name"], "type": "text"}


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
) -> dict:
    """Create a vector path shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        segments: Path segments — list of dicts with keys: command ("M"=move, "L"=line, "C"=curve, "Z"=close), x, y, and optionally c1x, c1y, c2x, c2y for curves. Example: [{"command":"M","x":0,"y":0}, {"command":"L","x":100,"y":100}].
        name: Shape name (default "Path").
        fill_color: Optional fill color hex.
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Stroke color hex (default "#000000").
        stroke_width: Stroke width in pixels (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root frame.
    """
    frame_id = parent_id or ROOT_FRAME_ID

    # Calculate bounding box from segments
    xs = [s["x"] for s in segments if "x" in s]
    ys = [s["y"] for s in segments if "y" in s]
    min_x = min(xs) if xs else 0
    min_y = min(ys) if ys else 0
    max_x = max(xs) if xs else 100
    max_y = max(ys) if ys else 100
    w = max(max_x - min_x, 1)
    h = max(max_y - min_y, 1)

    # Convert segments to Penpot path content format
    content = []
    for seg in segments:
        cmd = seg.get("command", "L")
        entry: dict[str, Any] = {"command": cmd.lower()}
        if cmd.upper() in ("M", "L"):
            entry["params"] = {"x": seg["x"], "y": seg["y"]}
        elif cmd.upper() == "C":
            entry["params"] = {
                "c1x": seg.get("c1x", seg["x"]),
                "c1y": seg.get("c1y", seg["y"]),
                "c2x": seg.get("c2x", seg["x"]),
                "c2y": seg.get("c2y", seg["y"]),
                "x": seg["x"],
                "y": seg["y"],
            }
        elif cmd.upper() == "Z":
            entry["params"] = {}
        content.append(entry)

    shape_id = new_uuid()
    geom = build_shape_geometry(min_x, min_y, w, h)

    obj: dict[str, Any] = {
        "id": shape_id,
        "type": "path",
        "name": name,
        "frame-id": frame_id,
        "parent-id": parent_id or frame_id,
        "content": content,
        "strokes": [build_stroke(color=stroke_color, width=stroke_width)],
        **geom,
    }

    if fill_color:
        obj["fills"] = [build_fill(color=fill_color, opacity=fill_opacity)]
    else:
        obj["fills"] = []

    if opacity != 1.0:
        obj["opacity"] = opacity

    change = change_add_obj(page_id, frame_id, obj)
    await apply_changes(file_id, [change])
    return {"id": shape_id, "name": name, "type": "path"}


async def create_group(
    file_id: str,
    page_id: str,
    shape_ids: list[str],
    name: str = "Group",
    parent_id: str | None = None,
) -> dict:
    """Group existing shapes together.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_ids: List of shape UUIDs to include in the group.
        name: Group name (default "Group").
        parent_id: Parent shape ID. If omitted, uses root frame.
    """
    from penpot_mcp.services.changes import change_mov_objects

    frame_id = parent_id or ROOT_FRAME_ID

    # Create group shape — uses first shape's position as approximate bounds
    shape_id = new_uuid()
    geom = build_shape_geometry(0, 0, 100, 100)

    obj: dict[str, Any] = {
        "id": shape_id,
        "type": "group",
        "name": name,
        "frame-id": frame_id,
        "parent-id": parent_id or frame_id,
        "shapes": shape_ids,
        **geom,
    }

    changes = [
        change_add_obj(page_id, frame_id, obj),
        change_mov_objects(page_id, shape_id, shape_ids),
    ]
    await apply_changes(file_id, changes)
    return {"id": shape_id, "name": name, "type": "group"}


async def create_component(
    file_id: str,
    page_id: str,
    shape_id: str,
    name: str | None = None,
) -> dict:
    """Convert an existing shape/frame into a component.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to convert to a component.
        name: Component name. If omitted, keeps the shape's current name.
    """
    from penpot_mcp.services.changes import change_mod_obj, set_op

    component_id = new_uuid()
    ops = [
        set_op("component-id", component_id),
        set_op("component-file", file_id),
        set_op("component-root", True),
        set_op("main-instance", True),
    ]
    if name:
        ops.append(set_op("name", name))

    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"component_id": component_id, "shape_id": shape_id, "name": name}


async def create_page(
    file_id: str,
    name: str = "New Page",
) -> dict:
    """Add a new page to a file.

    Args:
        file_id: The file UUID.
        name: Page name (default "New Page").
    """
    page_id = new_uuid()
    change = change_add_page(page_id, name)
    await apply_changes(file_id, [change])
    return {"id": page_id, "name": name}
