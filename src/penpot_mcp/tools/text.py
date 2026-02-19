"""Text-specific modification tools."""

from __future__ import annotations

from penpot_mcp.services.changes import (
    apply_changes,
    build_text_content,
    change_mod_obj,
    set_op,
)


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
) -> dict:
    """Replace the text content of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        text: New text content.
        font_family: Optional font family override.
        font_size: Optional font size override.
        font_weight: Optional font weight override ("400", "700", etc.).
        fill_color: Optional text color override (hex).
        text_align: Optional text alignment override ("left", "center", "right", "justify").
    """
    content = build_text_content(
        text=text,
        font_family=font_family or "sourcesanspro",
        font_size=str(font_size or 16),
        font_weight=font_weight or "400",
        fill_color=fill_color or "#000000",
        text_align=text_align or "left",
    )
    ops = [set_op("content", content)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "text": text}


async def set_font(
    file_id: str,
    page_id: str,
    shape_id: str,
    font_family: str,
) -> dict:
    """Change the font family of a text shape.

    Note: This rebuilds the text content. For complex text with multiple
    styles, use set_text_content instead.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_family: Font family name (e.g., "sourcesanspro", "roboto").
    """
    # We need to get the existing text first
    from penpot_mcp.tools.shapes import get_shape_details
    shape = await get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return shape

    existing_text = _extract_first_text(shape.get("content", {}))

    content = build_text_content(
        text=existing_text or "Text",
        font_family=font_family,
    )
    ops = [set_op("content", content)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "font_family": font_family}


async def set_font_size(
    file_id: str,
    page_id: str,
    shape_id: str,
    font_size: int,
) -> dict:
    """Change the font size of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_size: Font size in pixels.
    """
    from penpot_mcp.tools.shapes import get_shape_details
    shape = await get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return shape

    existing_text = _extract_first_text(shape.get("content", {}))

    content = build_text_content(
        text=existing_text or "Text",
        font_size=str(font_size),
    )
    ops = [set_op("content", content)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "font_size": font_size}


async def set_text_align(
    file_id: str,
    page_id: str,
    shape_id: str,
    align: str,
) -> dict:
    """Set text alignment of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        align: Text alignment — "left", "center", "right", or "justify".
    """
    from penpot_mcp.tools.shapes import get_shape_details
    shape = await get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return shape

    existing_text = _extract_first_text(shape.get("content", {}))

    content = build_text_content(
        text=existing_text or "Text",
        text_align=align,
    )
    ops = [set_op("content", content)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {"shape_id": shape_id, "text_align": align}


async def set_text_style(
    file_id: str,
    page_id: str,
    shape_id: str,
    font_weight: str | None = None,
    font_style: str | None = None,
    text_decoration: str | None = None,
) -> dict:
    """Set text styling (bold, italic, underline) of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_weight: Font weight — "400" (normal), "700" (bold), etc. (optional).
        font_style: Font style — "normal" or "italic" (optional).
        text_decoration: Text decoration — "none", "underline", "line-through" (optional).
    """
    from penpot_mcp.tools.shapes import get_shape_details
    shape = await get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return shape

    existing_text = _extract_first_text(shape.get("content", {}))

    kwargs: dict = {"text": existing_text or "Text"}
    if font_weight:
        kwargs["font_weight"] = font_weight
    if font_style:
        kwargs["font_style"] = font_style
    if text_decoration:
        kwargs["text_decoration"] = text_decoration

    content = build_text_content(**kwargs)
    ops = [set_op("content", content)]
    change = change_mod_obj(page_id, shape_id, ops)
    await apply_changes(file_id, [change])
    return {
        "shape_id": shape_id,
        "font_weight": font_weight,
        "font_style": font_style,
        "text_decoration": text_decoration,
    }


def _extract_first_text(content: dict | None) -> str | None:
    """Extract plain text from the first paragraph of a Penpot text content structure."""
    if not content or not isinstance(content, dict):
        return None
    for child in content.get("children", []):
        if isinstance(child, dict):
            for pset in child.get("children", []):
                if isinstance(pset, dict):
                    for para in pset.get("children", []):
                        if isinstance(para, dict) and "text" in para:
                            return para["text"]
    return None
