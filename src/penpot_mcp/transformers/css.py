"""Shape → CSS property transformer (inspired by Framelink's approach)."""

from __future__ import annotations


def shape_to_css(shape: dict) -> dict[str, str]:
    """Convert a Penpot shape's visual properties to CSS properties.

    Returns a dict of CSS property-name → value suitable for inline styles
    or CSS class generation.
    """
    css: dict[str, str] = {}

    # Position & dimensions
    if shape.get("x") is not None and shape.get("y") is not None:
        css["position"] = "absolute"
        css["left"] = f"{shape['x']}px"
        css["top"] = f"{shape['y']}px"
    if shape.get("width"):
        css["width"] = f"{shape['width']}px"
    if shape.get("height"):
        css["height"] = f"{shape['height']}px"

    # Rotation
    rotation = shape.get("rotation", 0)
    if rotation:
        css["transform"] = f"rotate({rotation}deg)"

    # Opacity
    opacity = shape.get("opacity", 1)
    if opacity < 1:
        css["opacity"] = str(round(opacity, 3))

    # Border radius
    r1 = shape.get("r1")
    r2 = shape.get("r2")
    r3 = shape.get("r3")
    r4 = shape.get("r4")
    rx = shape.get("rx")

    if r1 is not None and r2 is not None and r3 is not None and r4 is not None:
        if r1 == r2 == r3 == r4:
            css["border-radius"] = f"{r1}px"
        else:
            css["border-radius"] = f"{r1}px {r2}px {r3}px {r4}px"
    elif rx is not None:
        css["border-radius"] = f"{rx}px"

    # Fills
    fills = shape.get("fills", [])
    if fills:
        fill = fills[0]  # Primary fill
        color = fill.get("fill-color")
        fill_opacity = fill.get("fill-opacity", 1)
        gradient = fill.get("fill-color-gradient")

        if gradient:
            css["background"] = _gradient_to_css(gradient)
        elif color:
            if fill_opacity < 1:
                css["background-color"] = _hex_to_rgba(color, fill_opacity)
            else:
                css["background-color"] = color

    # Strokes
    strokes = shape.get("strokes", [])
    if strokes:
        stroke = strokes[0]
        s_color = stroke.get("stroke-color", "#000000")
        s_width = stroke.get("stroke-width", 1)
        s_style = stroke.get("stroke-style", "solid")
        s_alignment = stroke.get("stroke-alignment", "center")
        s_opacity = stroke.get("stroke-opacity", 1)

        if s_opacity < 1:
            s_color = _hex_to_rgba(s_color, s_opacity)

        css_style = "solid" if s_style == "solid" else s_style
        css["border"] = f"{s_width}px {css_style} {s_color}"

        if s_alignment == "inner":
            css["box-sizing"] = "border-box"

    # Shadows
    shadows = shape.get("shadow", [])
    shadow_values = []
    for shadow in shadows:
        if shadow.get("hidden"):
            continue
        sx = shadow.get("offset-x", 0)
        sy = shadow.get("offset-y", 0)
        blur = shadow.get("blur", 0)
        spread = shadow.get("spread", 0)
        s_color = shadow.get("color", {})
        color_str = _hex_to_rgba(
            s_color.get("color", "#000000"),
            s_color.get("opacity", 0.25),
        )
        style = shadow.get("style", "drop-shadow")
        inset = "inset " if style == "inner-shadow" else ""
        shadow_values.append(f"{inset}{sx}px {sy}px {blur}px {spread}px {color_str}")
    if shadow_values:
        css["box-shadow"] = ", ".join(shadow_values)

    # Blur
    blur = shape.get("blur")
    if blur and not blur.get("hidden"):
        css["filter"] = f"blur({blur.get('value', 0)}px)"

    # Blend mode
    blend = shape.get("blend-mode")
    if blend and blend != "normal":
        css["mix-blend-mode"] = blend

    # Layout (flexbox)
    layout = shape.get("layout")
    if layout:
        css.update(_layout_to_css(shape))

    return css


def shape_to_css_string(shape: dict) -> str:
    """Convert shape to a CSS string representation."""
    props = shape_to_css(shape)
    lines = [f"  {prop}: {value};" for prop, value in props.items()]
    name = shape.get("name", "element").replace(" ", "-").lower()
    return f".{name} {{\n" + "\n".join(lines) + "\n}"


def _hex_to_rgba(hex_color: str, opacity: float) -> str:
    """Convert hex color + opacity to rgba()."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = (
            int(hex_color[:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
        return f"rgba({r}, {g}, {b}, {round(opacity, 3)})"
    return hex_color


def _gradient_to_css(gradient: dict) -> str:
    """Convert Penpot gradient to CSS gradient."""
    g_type = gradient.get("type", "linear")
    stops = gradient.get("stops", [])

    stop_strs = []
    for stop in stops:
        color = stop.get("color", "#000000")
        opacity = stop.get("opacity", 1)
        offset = stop.get("offset", 0)
        if opacity < 1:
            color_str = _hex_to_rgba(color, opacity)
        else:
            color_str = color
        stop_strs.append(f"{color_str} {round(offset * 100)}%")

    if g_type == "linear":
        start_x = gradient.get("start-x", 0.5)
        start_y = gradient.get("start-y", 0)
        end_x = gradient.get("end-x", 0.5)
        end_y = gradient.get("end-y", 1)
        import math

        angle = math.degrees(math.atan2(end_y - start_y, end_x - start_x)) - 90
        return f"linear-gradient({round(angle)}deg, {', '.join(stop_strs)})"
    else:
        return f"radial-gradient(circle, {', '.join(stop_strs)})"


def _layout_to_css(shape: dict) -> dict[str, str]:
    """Convert Penpot layout properties to CSS flexbox."""
    css: dict[str, str] = {}
    css["display"] = "flex"

    flex_dir = shape.get("layout-flex-dir", "column")
    direction_map = {
        "row": "row",
        "row-reverse": "row-reverse",
        "column": "column",
        "column-reverse": "column-reverse",
    }
    css["flex-direction"] = direction_map.get(flex_dir, "column")

    gap = shape.get("layout-gap", {})
    row_gap = gap.get("row-gap", 0)
    col_gap = gap.get("column-gap", 0)
    if row_gap == col_gap:
        css["gap"] = f"{row_gap}px"
    else:
        css["gap"] = f"{row_gap}px {col_gap}px"

    padding = shape.get("layout-padding", {})
    p1 = padding.get("p1", 0)
    p2 = padding.get("p2", 0)
    p3 = padding.get("p3", 0)
    p4 = padding.get("p4", 0)
    if p1 == p2 == p3 == p4:
        css["padding"] = f"{p1}px"
    else:
        css["padding"] = f"{p1}px {p2}px {p3}px {p4}px"

    align = shape.get("layout-align-items")
    if align:
        align_map = {
            "start": "flex-start",
            "center": "center",
            "end": "flex-end",
            "stretch": "stretch",
        }
        css["align-items"] = align_map.get(align, align)

    justify = shape.get("layout-justify-content")
    if justify:
        justify_map = {
            "start": "flex-start",
            "center": "center",
            "end": "flex-end",
            "space-between": "space-between",
            "space-around": "space-around",
            "space-evenly": "space-evenly",
            "stretch": "stretch",
        }
        css["justify-content"] = justify_map.get(justify, justify)

    wrap = shape.get("layout-wrap-type")
    if wrap == "wrap":
        css["flex-wrap"] = "wrap"

    return css
