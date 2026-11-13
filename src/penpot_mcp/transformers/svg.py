"""Shape → SVG output transformer."""

from __future__ import annotations

from typing import Any


def shape_to_svg(shape: dict) -> str:
    """Convert a Penpot shape to an SVG element string.

    Supports rect, circle/ellipse, text, and frame shapes.
    For complex shapes, returns a placeholder with dimensions.
    """
    shape_type = shape.get("type", "")
    # Strip Penpot's : prefix if present
    if isinstance(shape_type, str) and shape_type.startswith(":"):
        shape_type = shape_type[1:]

    x = shape.get("x", 0)
    y = shape.get("y", 0)
    w = shape.get("width", 0)
    h = shape.get("height", 0)

    fill_attr = _fills_to_svg(shape.get("fills", []))
    stroke_attr = _strokes_to_svg(shape.get("strokes", []))
    opacity_attr = _opacity_attr(shape.get("opacity", 1))
    transform_attr = _transform_attr(shape)

    attrs = f"{fill_attr}{stroke_attr}{opacity_attr}{transform_attr}"

    if shape_type in ("rect", "frame"):
        rx = shape.get("rx", 0) or shape.get("r1", 0) or 0
        rx_attr = f' rx="{rx}"' if rx else ""
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{rx_attr}{attrs} />'

    if shape_type in ("circle", "ellipse"):
        cx = x + w / 2
        cy = y + h / 2
        rx_val = w / 2
        ry_val = h / 2
        return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx_val}" ry="{ry_val}"{attrs} />'

    if shape_type == "text":
        from penpot_mcp.tools.shapes import _extract_text_content

        text = _extract_text_content(shape.get("content", {}))
        font_size: float | str = 16
        font_weight: str = "400"
        content = shape.get("content", {})

        def find_text_style(node: Any) -> dict | None:
            if isinstance(node, dict):
                if "text" in node or "font-size" in node:
                    return node
                for child in node.get("children", []):
                    if (result := find_text_style(child)) is not None:
                        return result
            elif isinstance(node, list):
                for child in node:
                    if (result := find_text_style(child)) is not None:
                        return result
            return None

        text_style = find_text_style(content) or {}
        font_size = text_style.get("font-size", font_size)
        font_weight = str(text_style.get("font-weight", font_weight))
        numeric_font_size = float(font_size)
        text_fills = shape.get("fills", []) or text_style.get("fills", [])
        text_attrs = (
            f"{_fills_to_svg(text_fills)}"
            f"{_opacity_attr(shape.get('opacity', 1))}"
            f"{_transform_attr(shape)}"
        )
        return (
            f'<text x="{x}" y="{y + numeric_font_size}" '
            f'font-size="{font_size}" font-weight="{font_weight}"'
            f'{text_attrs}>{_escape_xml(text)}</text>'
        )

    if shape_type == "path":
        path_content = shape.get("content", [])
        d = _path_content_to_d(path_content)
        return f'<path d="{d}"{attrs} />'

    if shape_type == "image":
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="#ccc" stroke="#999" stroke-dasharray="4"{attrs} />'
        )

    # Fallback
    return f'<!-- {shape_type}: {shape.get("name", "?")} ({w}x{h}) at ({x},{y}) -->'


def shapes_to_svg_document(
    shapes: list[dict],
    width: float = 1920,
    height: float = 1080,
) -> str:
    """Wrap multiple shapes into a full SVG document."""
    elements = "\n  ".join(shape_to_svg(s) for s in shapes)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f"  {elements}\n"
        f"</svg>"
    )


def _fills_to_svg(fills: list[dict]) -> str:
    if not fills:
        return ' fill="none"'
    fill = fills[0]
    color = fill.get("fill-color")
    opacity = fill.get("fill-opacity", 1)
    if color:
        result = f' fill="{color}"'
        if opacity < 1:
            result += f' fill-opacity="{round(opacity, 3)}"'
        return result
    return ' fill="none"'


def _strokes_to_svg(strokes: list[dict]) -> str:
    if not strokes:
        return ""
    stroke = strokes[0]
    color = stroke.get("stroke-color", "#000000")
    width = stroke.get("stroke-width", 1)
    opacity = stroke.get("stroke-opacity", 1)
    result = f' stroke="{color}" stroke-width="{width}"'
    if opacity < 1:
        result += f' stroke-opacity="{round(opacity, 3)}"'
    return result


def _opacity_attr(opacity: float) -> str:
    if opacity < 1:
        return f' opacity="{round(opacity, 3)}"'
    return ""


def _transform_attr(shape: dict) -> str:
    rotation = shape.get("rotation", 0)
    if not rotation:
        return ""
    cx = shape.get("x", 0) + shape.get("width", 0) / 2
    cy = shape.get("y", 0) + shape.get("height", 0) / 2
    return f' transform="rotate({rotation} {cx} {cy})"'


def _path_content_to_d(content: Any) -> str:
    """Convert Penpot path content to SVG d attribute."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for seg in content:
        cmd = seg.get("command", "")
        params = seg.get("params", {})
        if cmd == "move-to":
            parts.append(f"M {params.get('x', 0)} {params.get('y', 0)}")
        elif cmd == "line-to":
            parts.append(f"L {params.get('x', 0)} {params.get('y', 0)}")
        elif cmd == "curve-to":
            parts.append(
                f"C {params.get('c1x', 0)} {params.get('c1y', 0)}, "
                f"{params.get('c2x', 0)} {params.get('c2y', 0)}, "
                f"{params.get('x', 0)} {params.get('y', 0)}"
            )
        elif cmd == "close-path":
            parts.append("Z")
    return " ".join(parts)


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
