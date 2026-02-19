"""Penpot file change operations builder.

All shape mutations in Penpot go through the update-file RPC command.
This module builds the change operation payloads and handles the
session/revision bookkeeping.

Change types:
  - add-obj: Create a new shape
  - mod-obj: Modify shape attributes
  - del-obj: Delete a shape
  - add-page: Add a new page
  - del-page: Delete a page
  - mod-page: Rename a page
  - mov-objects: Move shapes between parents or reorder
"""

from __future__ import annotations

import uuid
from typing import Any

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db

ROOT_FRAME_ID = "00000000-0000-0000-0000-000000000000"


def new_uuid() -> str:
    """Generate a new UUID v4 string."""
    return str(uuid.uuid4())


def _build_selrect(x: float, y: float, w: float, h: float) -> dict:
    """Build the selection rectangle for a shape."""
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "x1": x,
        "y1": y,
        "x2": x + w,
        "y2": y + h,
    }


def _build_points(x: float, y: float, w: float, h: float) -> list[dict]:
    """Build the 4-point polygon for a shape (clockwise from top-left)."""
    return [
        {"x": x, "y": y},
        {"x": x + w, "y": y},
        {"x": x + w, "y": y + h},
        {"x": x, "y": y + h},
    ]


def _identity_matrix() -> dict:
    """Identity affine transform matrix."""
    return {"a": 1.0, "b": 0.0, "c": 0.0, "d": 1.0, "e": 0.0, "f": 0.0}


def build_shape_geometry(x: float, y: float, w: float, h: float) -> dict:
    """Build all geometry fields required for a shape."""
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "selrect": _build_selrect(x, y, w, h),
        "points": _build_points(x, y, w, h),
        "transform": _identity_matrix(),
        "transform-inverse": _identity_matrix(),
    }


def build_fill(
    color: str = "#B1B2B5",
    opacity: float = 1.0,
    gradient: dict | None = None,
    image: dict | None = None,
) -> dict:
    """Build a single fill entry."""
    if gradient:
        return {"fill-color-gradient": gradient}
    if image:
        return {"fill-image": image}
    return {"fill-color": color, "fill-opacity": opacity}


def build_stroke(
    color: str = "#000000",
    width: float = 1.0,
    opacity: float = 1.0,
    style: str = "solid",
    alignment: str = "center",
) -> dict:
    """Build a single stroke entry."""
    return {
        "stroke-color": color,
        "stroke-width": width,
        "stroke-opacity": opacity,
        "stroke-style": style,
        "stroke-alignment": alignment,
    }


def build_text_content(
    text: str,
    font_family: str = "sourcesanspro",
    font_size: str = "16",
    font_weight: str = "400",
    font_style: str = "normal",
    fill_color: str = "#000000",
    fill_opacity: float = 1.0,
    text_align: str = "left",
    line_height: float = 1.2,
    letter_spacing: float = 0,
    text_decoration: str = "none",
) -> dict:
    """Build text content structure for text shapes."""
    return {
        "type": "root",
        "children": [
            {
                "type": "paragraph-set",
                "children": [
                    {
                        "type": "paragraph",
                        "children": [
                            {
                                "text": text,
                                "fills": [
                                    {
                                        "fill-color": fill_color,
                                        "fill-opacity": fill_opacity,
                                    }
                                ],
                                "font-size": font_size,
                                "font-family": font_family,
                                "font-weight": font_weight,
                                "font-style": font_style,
                                "text-decoration": text_decoration,
                                "letter-spacing": str(letter_spacing),
                                "line-height": line_height,
                                "text-align": text_align,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def change_add_obj(
    page_id: str,
    frame_id: str,
    obj: dict,
    parent_id: str | None = None,
) -> dict:
    """Build an add-obj change operation."""
    return {
        "type": "add-obj",
        "page-id": page_id,
        "id": obj["id"],
        "frame-id": frame_id,
        "parent-id": parent_id or frame_id,
        "obj": obj,
    }


def change_mod_obj(
    page_id: str,
    shape_id: str,
    operations: list[dict],
) -> dict:
    """Build a mod-obj change operation.

    Each operation: {"type": "set", "attr": "attr-name", "val": value}
    """
    return {
        "type": "mod-obj",
        "page-id": page_id,
        "id": shape_id,
        "operations": operations,
    }


def change_del_obj(page_id: str, shape_id: str) -> dict:
    """Build a del-obj change operation."""
    return {
        "type": "del-obj",
        "page-id": page_id,
        "id": shape_id,
    }


def change_mov_objects(
    page_id: str,
    parent_id: str,
    shapes: list[str],
    index: int | None = None,
) -> dict:
    """Build a mov-objects change operation."""
    change: dict[str, Any] = {
        "type": "mov-objects",
        "page-id": page_id,
        "parent-id": parent_id,
        "shapes": shapes,
    }
    if index is not None:
        change["index"] = index
    return change


def change_add_page(page_id: str, name: str) -> dict:
    """Build an add-page change operation."""
    return {
        "type": "add-page",
        "id": page_id,
        "name": name,
    }


def change_del_page(page_id: str) -> dict:
    """Build a del-page change operation."""
    return {
        "type": "del-page",
        "id": page_id,
    }


def change_mod_page(page_id: str, name: str) -> dict:
    """Build a mod-page (rename) change operation."""
    return {
        "type": "mod-page",
        "id": page_id,
        "name": name,
    }


def set_op(attr: str, val: Any) -> dict:
    """Build a set operation for mod-obj."""
    return {"type": "set", "attr": attr, "val": val}


async def get_file_info(file_id: str) -> dict:
    """Get current file revision, version, and features from DB."""
    row = await db.fetchrow(
        "SELECT revn, vern, features FROM file WHERE id = $1", file_id
    )
    if not row:
        raise ValueError(f"File {file_id} not found")
    return {
        "revn": row["revn"] or 0,
        "vern": row["vern"] or 0,
        "features": list(row["features"]) if row["features"] else [],
    }


async def apply_changes(
    file_id: str,
    changes: list[dict],
    session_id: str | None = None,
) -> dict:
    """Apply a list of changes to a file.

    Handles session ID generation and revision tracking.
    """
    if not session_id:
        session_id = new_uuid()

    info = await get_file_info(file_id)

    result = await api.update_file(
        file_id=file_id,
        session_id=session_id,
        revn=info["revn"],
        vern=info["vern"],
        changes=changes,
        features=info["features"],
    )
    return result
