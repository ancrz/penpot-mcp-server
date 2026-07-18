"""Native Penpot typography asset mutation tools."""

from __future__ import annotations

from penpot_mcp.services.api import api
from penpot_mcp.services.changes import get_file_info, new_uuid
from penpot_mcp.tools.components import get_typography_library

_REQUIRED_STRING_FIELDS = (
    "name",
    "font-id",
    "font-family",
    "font-variant-id",
    "font-size",
    "font-weight",
    "font-style",
    "line-height",
    "letter-spacing",
    "text-transform",
)


def _validate_string(field: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field.replace('-', '_')} must not be empty")
    return value.strip()


def _build_typography(
    typography_id: str,
    *,
    name: str,
    font_id: str,
    font_family: str,
    font_variant_id: str,
    font_size: str,
    font_weight: str,
    font_style: str,
    line_height: str,
    letter_spacing: str,
    text_transform: str,
    path: str,
) -> dict:
    typography = {
        "id": typography_id,
        "name": name,
        "font-id": font_id,
        "font-family": font_family,
        "font-variant-id": font_variant_id,
        "font-size": font_size,
        "font-weight": font_weight,
        "font-style": font_style,
        "line-height": line_height,
        "letter-spacing": letter_spacing,
        "text-transform": text_transform,
        "path": path.strip("/"),
    }
    for field in _REQUIRED_STRING_FIELDS:
        typography[field] = _validate_string(field, typography[field])
    return typography


async def create_typography(
    file_id: str,
    name: str,
    font_id: str = "sourcesanspro",
    font_family: str = "sourcesanspro",
    font_variant_id: str = "regular",
    font_size: str = "14",
    font_weight: str = "480",
    font_style: str = "normal",
    line_height: str = "1.2",
    letter_spacing: str = "0",
    text_transform: str = "none",
    path: str = "",
) -> dict:
    """Create a native Penpot typography asset."""
    typography = _build_typography(
        new_uuid(),
        name=name,
        font_id=font_id,
        font_family=font_family,
        font_variant_id=font_variant_id,
        font_size=font_size,
        font_weight=font_weight,
        font_style=font_style,
        line_height=line_height,
        letter_spacing=letter_spacing,
        text_transform=text_transform,
        path=path,
    )
    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "add-typography", "typography": typography}],
        features=info["features"],
    )
    return typography


async def update_typography(
    file_id: str,
    typography_id: str,
    name: str | None = None,
    font_id: str | None = None,
    font_family: str | None = None,
    font_variant_id: str | None = None,
    font_size: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    line_height: str | None = None,
    letter_spacing: str | None = None,
    text_transform: str | None = None,
    path: str | None = None,
) -> dict:
    """Update a native Penpot typography asset, preserving unspecified fields."""
    typographies = await get_typography_library(file_id)
    existing = next(
        (item for item in typographies if item["id"] == typography_id), None
    )
    if existing is None:
        raise ValueError(
            f"Typography asset {typography_id} not found in file {file_id}"
        )

    typography = _build_typography(
        typography_id,
        name=existing["name"] if name is None else name,
        font_id=existing["font_id"] if font_id is None else font_id,
        font_family=existing["font_family"] if font_family is None else font_family,
        font_variant_id=(
            existing["font_variant_id"]
            if font_variant_id is None
            else font_variant_id
        ),
        font_size=existing["font_size"] if font_size is None else font_size,
        font_weight=(
            existing["font_weight"] if font_weight is None else font_weight
        ),
        font_style=existing["font_style"] if font_style is None else font_style,
        line_height=(
            existing["line_height"] if line_height is None else line_height
        ),
        letter_spacing=(
            existing["letter_spacing"]
            if letter_spacing is None
            else letter_spacing
        ),
        text_transform=(
            existing["text_transform"]
            if text_transform is None
            else text_transform
        ),
        path=(existing.get("path") or "") if path is None else path,
    )
    plugin_data = existing.get("plugin_data")
    if plugin_data is not None:
        typography["plugin-data"] = plugin_data

    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "mod-typography", "typography": typography}],
        features=info["features"],
    )
    return typography


async def delete_typography(file_id: str, typography_id: str) -> dict:
    """Delete a native Penpot typography asset."""
    info = await get_file_info(file_id)
    await api.update_file_transit(
        file_id=file_id,
        session_id=new_uuid(),
        revn=info["revn"],
        vern=info["vern"],
        changes=[{"type": "del-typography", "id": typography_id}],
        features=info["features"],
    )
    return {"id": typography_id, "deleted": True}
