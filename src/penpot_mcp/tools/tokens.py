"""Design system token application and binding tools."""

from __future__ import annotations

import logging
from penpot_mcp.tools.components import get_colors_library, get_typography_library
from penpot_mcp.tools.shapes import get_page_objects, get_shape_details
from penpot_mcp.tools.modify import modify_shape
from penpot_mcp.tools.files import get_file_pages

logger = logging.getLogger(__name__)

# Overlapping colors semantic resolution overrides
def resolve_color(hex_val: str, shape_name: str, parent_name: str, color_tokens: list[dict]) -> str | None:
    hex_val = hex_val.upper()
    matches = [t for t in color_tokens if t.get("color") and t["color"].upper() == hex_val]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]["id"]
        
    s_name = shape_name.lower()
    p_name = parent_name.lower()
    
    best_match = matches[0]
    best_score = -1
    
    # Common semantic keywords we search for in token and shape/parent names to match context
    keywords = ["offline", "warn", "warning", "play", "select", "success", "content", "text", "player"]
    
    for t in matches:
        t_name = (t.get("name") or "").lower()
        score = 0
        
        for kw in keywords:
            if kw in t_name:
                if kw in s_name or kw in p_name:
                    score += 10
                    
        # General substring matching for dynamic names
        if t_name:
            if t_name in s_name or t_name in p_name:
                score += 5
                
        if score > best_score:
            best_score = score
            best_match = t
            
    return best_match["id"]

def find_matching_typography(font_size: str | int, font_weight: str | int, typo_tokens: list[dict]) -> dict | None:
    try:
        size_val = int(float(font_size))
    except (ValueError, TypeError):
        return None
    
    weight_str = str(font_weight).lower()
    if weight_str == "regular":
        weight_str = "400"
    elif weight_str == "bold":
        weight_str = "700"
        
    for token in typo_tokens:
        try:
            token_size = int(float(token.get("font_size", 0)))
        except (ValueError, TypeError):
            continue
            
        token_weight = str(token.get("font_weight", "")).lower()
        if token_weight == "regular":
            token_weight = "400"
        elif token_weight == "bold":
            token_weight = "700"
            
        if token_size == size_val and token_weight == weight_str:
            return token
    return None

def set_color_token_in_fills(fills: list, token_id: str, file_id: str, token_color: str) -> list:
    new_fills = []
    if not fills:
        return [{"fill-color": token_color.lower(), "fill-color-ref-id": token_id, "fill-color-ref-file": file_id, "fill-opacity": 1.0}]
    for fill in fills:
        if isinstance(fill, dict):
            new_fill = fill.copy()
            new_fill["fill-color-ref-id"] = token_id
            new_fill["fill-color-ref-file"] = file_id
            new_fill["fill-color"] = token_color.lower()
            new_fills.append(new_fill)
        else:
            new_fills.append(fill)
    return new_fills

def set_color_token_in_strokes(strokes: list, token_id: str, file_id: str, token_color: str) -> list:
    new_strokes = []
    if not strokes:
        return [{"stroke-color": token_color.lower(), "stroke-color-ref-id": token_id, "stroke-color-ref-file": file_id, "stroke-width": 1.0, "stroke-opacity": 1.0, "stroke-style": "solid", "stroke-alignment": "center"}]
    for stroke in strokes:
        if isinstance(stroke, dict):
            new_stroke = stroke.copy()
            new_stroke["stroke-color-ref-id"] = token_id
            new_stroke["stroke-color-ref-file"] = file_id
            new_stroke["stroke-color"] = token_color.lower()
            new_strokes.append(new_stroke)
        else:
            new_strokes.append(stroke)
    return new_strokes

def set_typography_token_in_node(node: dict, token: dict, file_id: str) -> None:
    node["typography-ref-id"] = token["id"]
    node["typography-ref-file"] = file_id
    node["font-family"] = token.get("font_family")
    node["font-size"] = str(token.get("font_size"))
    node["font-weight"] = str(token.get("font_weight"))
    node["font-style"] = token.get("font_style")
    node["line-height"] = str(token.get("line_height"))
    node["letter-spacing"] = str(token.get("letter_spacing"))
    node["text-transform"] = token.get("text_transform")
    node["font-id"] = token.get("font_id")
    node["font-variant-id"] = token.get("font_variant_id")
    node["path"] = token.get("path")

def apply_color_token_to_nested_content(node: dict, token_id: str, file_id: str, token_color: str) -> bool:
    if not isinstance(node, dict):
        return False
    changed = False
    if "fills" in node:
        node["fills"] = set_color_token_in_fills(node.get("fills", []), token_id, file_id, token_color)
        changed = True
    if "children" in node:
        children = node["children"]
        if isinstance(children, list):
            for child in children:
                child_changed = apply_color_token_to_nested_content(child, token_id, file_id, token_color)
                if child_changed:
                    changed = True
    return changed

def apply_typography_token_to_nested_content(node: dict, token: dict, file_id: str) -> bool:
    if not isinstance(node, dict):
        return False
    changed = False
    if "font-size" in node and "font-weight" in node:
        set_typography_token_in_node(node, token, file_id)
        changed = True
    if "children" in node:
        children = node["children"]
        if isinstance(children, list):
            for child in children:
                child_changed = apply_typography_token_to_nested_content(child, token, file_id)
                if child_changed:
                    changed = True
    return changed

def auto_bind_nested_nodes(node: dict, shape_name: str, parent_name: str, color_tokens: list[dict], typo_tokens: list[dict], file_id: str) -> bool:
    if not isinstance(node, dict):
        return False
    changed = False
    
    # 1. Typography Auto-Bind
    if "font-size" in node and "font-weight" in node:
        curr_typo_ref = node.get("typography-ref-id")
        if not curr_typo_ref:
            match_token = find_matching_typography(node.get("font-size"), node.get("font-weight"), typo_tokens)
            if match_token:
                set_typography_token_in_node(node, match_token, file_id)
                changed = True
                logger.info("Auto-bound nested typography to %s", match_token.get("name"))
                
    # 2. Color Fills Auto-Bind
    if "fills" in node:
        fills = node["fills"]
        if isinstance(fills, list):
            new_fills = []
            fills_changed = False
            for fill in fills:
                if isinstance(fill, dict):
                    fill_color = fill.get("fill-color")
                    ref_id = fill.get("fill-color-ref-id")
                    if fill_color and not ref_id:
                        token_id = resolve_color(fill_color, shape_name, parent_name, color_tokens)
                        if token_id:
                            new_fill = fill.copy()
                            new_fill["fill-color-ref-id"] = token_id
                            new_fill["fill-color-ref-file"] = file_id
                            new_fill["fill-color"] = fill_color.lower()
                            new_fills.append(new_fill)
                            fills_changed = True
                            changed = True
                        else:
                            new_fills.append(fill)
                    else:
                        new_fills.append(fill)
                else:
                    new_fills.append(fill)
            if fills_changed:
                node["fills"] = new_fills
                
    # 3. Recursive children traversal
    if "children" in node:
        children = node["children"]
        if isinstance(children, list):
            for child in children:
                child_changed = auto_bind_nested_nodes(child, shape_name, parent_name, color_tokens, typo_tokens, file_id)
                if child_changed:
                    changed = True
    return changed


async def apply_design_token(
    file_id: str,
    page_id: str,
    shape_ids: list[str],
    token_id: str,
    token_type: str,
    target_property: str = "fill",
) -> dict:
    """Apply a design token (color asset or typography asset) to a list of shapes.

    Args:
        file_id: The file UUID.
        page_id: The page UUID containing the shapes.
        shape_ids: List of shape UUIDs to modify.
        token_id: The UUID of the color or typography library token.
        token_type: Token type - "color" or "typography".
        target_property: Target property - "fill" or "stroke" (applies only to color tokens).
    """
    logger.info("Applying token %s (%s) to shapes: %s", token_id, token_type, shape_ids)
    
    if token_type == "color":
        color_tokens = await get_colors_library(file_id)
        token = next((t for t in color_tokens if t["id"] == token_id), None)
        if not token:
            raise ValueError(f"Color token {token_id} not found in library.")
        token_color = token["color"]
        
        for shape_id in shape_ids:
            details = await get_shape_details(file_id, page_id, shape_id)
            if "error" in details:
                continue
                
            attrs = {}
            if details.get("type") == "text":
                content = details.get("content", {})
                if content:
                    apply_color_token_to_nested_content(content, token_id, file_id, token_color)
                    attrs["content"] = content
            else:
                if target_property == "fill":
                    attrs["fills"] = set_color_token_in_fills(details.get("fills", []), token_id, file_id, token_color)
                elif target_property == "stroke":
                    attrs["strokes"] = set_color_token_in_strokes(details.get("strokes", []), token_id, file_id, token_color)
            
            if attrs:
                await modify_shape(file_id, page_id, shape_id, attrs)
                
    elif token_type == "typography":
        typo_tokens = await get_typography_library(file_id)
        token = next((t for t in typo_tokens if t["id"] == token_id), None)
        if not token:
            raise ValueError(f"Typography token {token_id} not found in library.")
            
        for shape_id in shape_ids:
            details = await get_shape_details(file_id, page_id, shape_id)
            if "error" in details:
                continue
            if details.get("type") != "text":
                logger.warning("Shape %s is not a text shape, skipping typography.", shape_id)
                continue
                
            content = details.get("content", {})
            if content:
                apply_typography_token_to_nested_content(content, token, file_id)
                await modify_shape(file_id, page_id, shape_id, {"content": content})
                
    return {"status": "success", "token_id": token_id, "applied_to": shape_ids}


async def auto_bind_library_tokens(
    file_id: str,
    page_id: str,
    shape_ids: list[str] | None = None,
) -> dict:
    """Automatically search shape styles/colors and bind matching library assets (color or typography tokens).

    Args:
        file_id: The file UUID.
        page_id: The page UUID to process.
        shape_ids: Optional list of shape UUIDs. If omitted, binds all shapes on the page.
    """
    logger.info("Running auto-bind of design tokens on page: %s", page_id)
    
    color_tokens = await get_colors_library(file_id)
    typo_tokens = await get_typography_library(file_id)
    
    if not shape_ids:
        objs = await get_page_objects(file_id, page_id)
        shape_ids = [obj["id"] for obj in objs if obj["id"] != "00000000-0000-0000-0000-000000000000"]
        
    bound_count = 0
    for shape_id in shape_ids:
        details = await get_shape_details(file_id, page_id, shape_id)
        if "error" in details:
            continue
            
        shape_type = details.get("type")
        shape_name = details.get("name", "")
        attrs_to_modify = {}
        
        # A. Fills for non-text shapes
        if shape_type != "text":
            fills = details.get("fills", [])
            modified_fills = []
            fills_changed = False
            for fill in fills:
                fill_color = fill.get("fill-color")
                ref_id = fill.get("fill-color-ref-id")
                if fill_color and not ref_id:
                    token_id = resolve_color(fill_color, shape_name, "", color_tokens)
                    if token_id:
                        new_fill = fill.copy()
                        new_fill["fill-color-ref-id"] = token_id
                        new_fill["fill-color-ref-file"] = file_id
                        new_fill["fill-color"] = fill_color.lower()
                        modified_fills.append(new_fill)
                        fills_changed = True
                    else:
                        modified_fills.append(fill)
                else:
                    modified_fills.append(fill)
            if fills_changed:
                attrs_to_modify["fills"] = modified_fills
                
        # B. Strokes for all shapes
        strokes = details.get("strokes", [])
        modified_strokes = []
        strokes_changed = False
        for stroke in strokes:
            stroke_color = stroke.get("stroke-color")
            ref_id = stroke.get("stroke-color-ref-id")
            if stroke_color and not ref_id:
                token_id = resolve_color(stroke_color, shape_name, "", color_tokens)
                if token_id:
                    new_stroke = stroke.copy()
                    new_stroke["stroke-color-ref-id"] = token_id
                    new_stroke["stroke-color-ref-file"] = file_id
                    new_stroke["stroke-color"] = stroke_color.lower()
                    modified_strokes.append(new_stroke)
                    strokes_changed = True
                else:
                    modified_strokes.append(stroke)
            else:
                modified_strokes.append(stroke)
        if strokes_changed:
            attrs_to_modify["strokes"] = modified_strokes
            
        # C. Text shapes (colors and typography resolved inside nested content)
        if shape_type == "text":
            content = details.get("content", {})
            if content:
                content_changed = auto_bind_nested_nodes(content, shape_name, "", color_tokens, typo_tokens, file_id)
                if content_changed:
                    attrs_to_modify["content"] = content
                    
        # Submit if updated
        if attrs_to_modify:
            await modify_shape(file_id, page_id, shape_id, attrs_to_modify)
            bound_count += 1
            
    return {"status": "success", "bound_shapes_count": bound_count}
