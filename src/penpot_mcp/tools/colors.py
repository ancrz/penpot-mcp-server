"""Native Penpot color asset mutation tools."""

from __future__ import annotations

import re

from penpot_mcp.services.api import api
from penpot_mcp.services.changes import get_file_info, new_uuid
from penpot_mcp.tools.components import get_colors_library

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")


def _normalize_hex_color(color: str) -> str:
    if not _HEX_COLOR_RE.fullmatch(color):
        raise ValueError(
            "color must be a 3- or 6-digit hexadecimal value such as #DFFB73"
        )
    return color.lower()


def _validate_opacity(opacity: float) -> float:
    if not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    return opacity


def _normalize_gradient(gradient: dict) -> dict:
    normalized = gradient.copy()
    g_type = normalized.get("type", "linear")
    if g_type not in ("linear", "radial"):
        raise ValueError("gradient type must be 'linear' or 'radial'")
    
    stops = normalized.get("stops", [])
    if not isinstance(stops, list) or not stops:
        raise ValueError("gradient must have a list of stops")
        
    normalized_stops = []
    for stop in stops:
        if not isinstance(stop, dict):
            raise ValueError("gradient stop must be a dictionary")
        new_stop = stop.copy()
        if "color" in stop:
            new_stop["color"] = _normalize_hex_color(stop["color"])
        if "opacity" in stop:
            new_stop["opacity"] = _validate_opacity(stop["opacity"])
        if "offset" in stop:
            offset = float(stop["offset"])
            if not 0 <= offset <= 1:
                raise ValueError("gradient stop offset must be between 0 and 1")
            new_stop["offset"] = offset
        normalized_stops.append(new_stop)
        
    normalized["stops"] = normalized_stops
    return normalized


async def create_color(
    file_id: str,
    name: str,
    color: str | None = None,
    opacity: float = 1.0,
    path: str = "",
    gradient: dict | None = None,
) -> dict:
    """Create a native color asset in a Penpot file."""
    if not name.strip():
        raise ValueError("name must not be empty")
    if color is not None and gradient is not None:
        raise ValueError("Cannot specify both color and gradient")
    if color is None and gradient is None:
        raise ValueError("Must specify either color or gradient")

    color_id = new_uuid()
    asset = {
        "id": color_id,
        "name": name.strip(),
        "path": path.strip("/"),
    }
    if color is not None:
        asset["color"] = _normalize_hex_color(color)
        asset["opacity"] = _validate_opacity(opacity)
    else:
        asset["gradient"] = _normalize_gradient(gradient)
        
    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "add-color", "color": asset}],
        features=info["features"],
    )
    return asset


async def update_color(
    file_id: str,
    color_id: str,
    name: str | None = None,
    color: str | None = None,
    opacity: float | None = None,
    path: str | None = None,
    gradient: dict | None = None,
) -> dict:
    """Update a native Penpot color asset, preserving unspecified fields."""
    colors = await get_colors_library(file_id)
    existing = next((item for item in colors if item["id"] == color_id), None)
    if existing is None:
        raise ValueError(f"Color asset {color_id} not found in file {file_id}")

    existing_opacity = existing.get("opacity")
    asset = {
        "id": color_id,
        "name": existing["name"] if name is None else name.strip(),
        "path": existing.get("path", "") if path is None else path.strip("/"),
    }
    if not asset["name"]:
        raise ValueError("name must not be empty")

    if color is not None and gradient is not None:
        raise ValueError("Cannot specify both color and gradient")

    if color is not None:
        asset["color"] = _normalize_hex_color(color)
        asset["opacity"] = (1 if existing_opacity is None else existing_opacity) if opacity is None else _validate_opacity(opacity)
    elif gradient is not None:
        asset["gradient"] = _normalize_gradient(gradient)
    else:
        # Preserve existing color/gradient/image
        if existing.get("color") is not None:
            asset["color"] = existing["color"]
            asset["opacity"] = (1 if existing_opacity is None else existing_opacity) if opacity is None else _validate_opacity(opacity)
        elif existing.get("gradient") is not None:
            asset["gradient"] = existing["gradient"]
        elif existing.get("image") is not None:
            asset["image"] = existing["image"]
        else:
            raise ValueError(
                f"Color asset {color_id} has no color, gradient, or image value"
            )

    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "mod-color", "color": asset}],
        features=info["features"],
    )
    return asset


async def delete_color(file_id: str, color_id: str) -> dict:
    """Delete a native color asset from a Penpot file."""
    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "del-color", "id": color_id}],
        features=info["features"],
    )
    return {"id": color_id, "deleted": True}

