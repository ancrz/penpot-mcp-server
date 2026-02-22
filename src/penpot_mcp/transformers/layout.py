"""Layout analysis — converts Penpot layout structures to CSS flexbox/grid."""

from __future__ import annotations

from typing import Any


def analyze_layout(shape: dict) -> dict:
    """Analyze a shape's layout properties and return a structured description.

    Returns layout type, direction, alignment, gaps, and padding in both
    Penpot-native and CSS-equivalent formats.
    """
    layout = shape.get("layout")
    if not layout:
        return {"has_layout": False, "type": "none"}

    result: dict[str, Any] = {"has_layout": True}

    # Layout type
    if layout == "flex":
        result["type"] = "flex"
    elif layout == "grid":
        result["type"] = "grid"
    else:
        result["type"] = str(layout)

    # Direction
    flex_dir = shape.get("layout-flex-dir", "column")
    result["direction"] = flex_dir
    result["css_flex_direction"] = flex_dir

    # Gap
    gap = shape.get("layout-gap", {})
    result["row_gap"] = gap.get("row-gap", 0)
    result["column_gap"] = gap.get("column-gap", 0)
    if result["row_gap"] == result["column_gap"]:
        result["css_gap"] = f"{result['row_gap']}px"
    else:
        result["css_gap"] = f"{result['row_gap']}px {result['column_gap']}px"

    # Padding
    padding = shape.get("layout-padding", {})
    result["padding"] = {
        "top": padding.get("p1", 0),
        "right": padding.get("p2", 0),
        "bottom": padding.get("p3", 0),
        "left": padding.get("p4", 0),
    }
    p = result["padding"]
    if p["top"] == p["right"] == p["bottom"] == p["left"]:
        result["css_padding"] = f"{p['top']}px"
    elif p["top"] == p["bottom"] and p["right"] == p["left"]:
        result["css_padding"] = f"{p['top']}px {p['right']}px"
    else:
        result["css_padding"] = f"{p['top']}px {p['right']}px {p['bottom']}px {p['left']}px"

    # Alignment
    align = shape.get("layout-align-items")
    justify = shape.get("layout-justify-content")
    result["align_items"] = align
    result["justify_content"] = justify
    result["css_align_items"] = _penpot_align_to_css(align)
    result["css_justify_content"] = _penpot_justify_to_css(justify)

    # Wrap
    wrap = shape.get("layout-wrap-type")
    result["wrap"] = wrap
    result["css_flex_wrap"] = "wrap" if wrap == "wrap" else "nowrap"

    # Grid-specific
    if layout == "grid":
        result["grid_rows"] = shape.get("layout-grid-rows", [])
        result["grid_columns"] = shape.get("layout-grid-columns", [])
        result["css_grid_template_rows"] = _grid_tracks_to_css(result["grid_rows"])
        result["css_grid_template_columns"] = _grid_tracks_to_css(result["grid_columns"])

    return result


def layout_to_css_class(shape: dict) -> str:
    """Generate a complete CSS class for a shape's layout."""
    analysis = analyze_layout(shape)
    if not analysis["has_layout"]:
        return ""

    name = shape.get("name", "container").replace(" ", "-").lower()
    lines = [f".{name} {{"]

    if analysis["type"] == "flex":
        lines.append("  display: flex;")
        lines.append(f"  flex-direction: {analysis['css_flex_direction']};")
        lines.append(f"  gap: {analysis['css_gap']};")
        lines.append(f"  padding: {analysis['css_padding']};")
        if analysis.get("css_align_items"):
            lines.append(f"  align-items: {analysis['css_align_items']};")
        if analysis.get("css_justify_content"):
            lines.append(f"  justify-content: {analysis['css_justify_content']};")
        lines.append(f"  flex-wrap: {analysis['css_flex_wrap']};")
    elif analysis["type"] == "grid":
        lines.append("  display: grid;")
        if analysis.get("css_grid_template_rows"):
            lines.append(f"  grid-template-rows: {analysis['css_grid_template_rows']};")
        if analysis.get("css_grid_template_columns"):
            lines.append(f"  grid-template-columns: {analysis['css_grid_template_columns']};")
        lines.append(f"  gap: {analysis['css_gap']};")
        lines.append(f"  padding: {analysis['css_padding']};")

    lines.append("}")
    return "\n".join(lines)


def _penpot_align_to_css(align: str | None) -> str:
    if not align:
        return ""
    mapping = {
        "start": "flex-start",
        "center": "center",
        "end": "flex-end",
        "stretch": "stretch",
    }
    return mapping.get(align, align)


def _penpot_justify_to_css(justify: str | None) -> str:
    if not justify:
        return ""
    mapping = {
        "start": "flex-start",
        "center": "center",
        "end": "flex-end",
        "space-between": "space-between",
        "space-around": "space-around",
        "space-evenly": "space-evenly",
        "stretch": "stretch",
    }
    return mapping.get(justify, justify)


def _grid_tracks_to_css(tracks: list) -> str:
    """Convert Penpot grid track definitions to CSS grid template."""
    if not tracks:
        return ""
    parts = []
    for track in tracks:
        if isinstance(track, dict):
            t_type = track.get("type", "fixed")
            value = track.get("value", 0)
            if t_type == "fixed":
                parts.append(f"{value}px")
            elif t_type == "percent":
                parts.append(f"{value}%")
            elif t_type == "flex":
                parts.append(f"{value}fr")
            elif t_type == "auto":
                parts.append("auto")
            else:
                parts.append(f"{value}px")
        else:
            parts.append(str(track))
    return " ".join(parts)
