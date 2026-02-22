"""Component, design token, color, and typography reading tools."""

from __future__ import annotations


from penpot_mcp.services.api import api


async def _get_file_data(file_id: str) -> dict:
    return await api.command("get-file", {"id": file_id, "components-v2": True})


async def get_component_instances(file_id: str) -> list[dict]:
    """List all components defined in a file.

    Args:
        file_id: The file UUID.
    """
    file_data = await _get_file_data(file_id)
    data = file_data.get("data", {})

    components = data.get("components", {})
    result = []
    for comp_id, comp in components.items():
        result.append(
            {
                "id": comp_id,
                "name": comp.get("name"),
                "path": comp.get("path"),
                "main_instance_id": comp.get("main-instance-id"),
                "main_instance_page": comp.get("main-instance-page"),
                "annotation": comp.get("annotation"),
            }
        )
    return result


async def get_design_tokens(file_id: str) -> dict:
    """Get all design tokens (colors, typographies, components) from a file.

    Provides a consolidated view of the file's design system.

    Args:
        file_id: The file UUID.
    """
    file_data = await _get_file_data(file_id)
    data = file_data.get("data", {})

    return {
        "colors": _extract_colors(data),
        "typographies": _extract_typographies(data),
        "components_count": len(data.get("components", {})),
        "media_count": len(data.get("media", {})),
    }


async def get_colors_library(file_id: str) -> list[dict]:
    """Get all colors defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    file_data = await _get_file_data(file_id)
    data = file_data.get("data", {})
    return _extract_colors(data)


async def get_typography_library(file_id: str) -> list[dict]:
    """Get all typographies defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    file_data = await _get_file_data(file_id)
    data = file_data.get("data", {})
    return _extract_typographies(data)


def _extract_colors(data: dict) -> list[dict]:
    """Extract color definitions from file data."""
    colors = data.get("colors", {})
    result = []
    for color_id, color in colors.items():
        result.append(
            {
                "id": color_id,
                "name": color.get("name"),
                "color": color.get("color"),
                "opacity": color.get("opacity"),
                "gradient": color.get("gradient"),
                "path": color.get("path"),
            }
        )
    return result


def _extract_typographies(data: dict) -> list[dict]:
    """Extract typography definitions from file data."""
    typographies = data.get("typographies", {})
    result = []
    for typo_id, typo in typographies.items():
        result.append(
            {
                "id": typo_id,
                "name": typo.get("name"),
                "font_family": typo.get("font-family"),
                "font_id": typo.get("font-id"),
                "font_size": typo.get("font-size"),
                "font_style": typo.get("font-style"),
                "font_variant_id": typo.get("font-variant-id"),
                "font_weight": typo.get("font-weight"),
                "letter_spacing": typo.get("letter-spacing"),
                "line_height": typo.get("line-height"),
                "text_transform": typo.get("text-transform"),
                "path": typo.get("path"),
            }
        )
    return result
