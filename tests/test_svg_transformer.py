"""Regression tests for local SVG rendering."""

from penpot_mcp.services.changes import build_text_content
from penpot_mcp.transformers.svg import shape_to_svg


def test_shape_to_svg_renders_fill_and_nested_text_style() -> None:
    rectangle = {
        "type": "rect",
        "x": 10,
        "y": 20,
        "width": 100,
        "height": 40,
        "fills": [{"fill-color": "#12191C", "fill-opacity": 1}],
    }
    text = {
        "type": "text",
        "x": 24,
        "y": 30,
        "width": 200,
        "height": 48,
        "fills": [{"fill-color": "#F4F6F5", "fill-opacity": 1}],
        "content": build_text_content(
            "Hearth TV",
            font_size="32",
            fill_color="#F4F6F5",
        ),
    }

    rectangle_svg = shape_to_svg(rectangle)
    text_svg = shape_to_svg(text)

    assert 'fill="#12191C"' in rectangle_svg
    assert 'font-size="32"' in text_svg
    assert "Hearth TV" in text_svg
