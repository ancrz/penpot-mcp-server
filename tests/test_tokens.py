"""Tests for design system token application and binding tools."""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from penpot_mcp.tools import tokens as token_tools


FILE_ID = "11111111-1111-4111-8111-111111111111"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
SHAPE_ID = "44444444-4444-4444-8444-444444444444"


@pytest.mark.asyncio
async def test_apply_color_token_to_rect(monkeypatch: pytest.MonkeyPatch) -> None:
    color_tokens = [
        {"id": "token-123", "name": "Primary", "color": "#ff0000", "opacity": 1.0}
    ]
    shape_details = {
        "id": SHAPE_ID,
        "type": "rect",
        "name": "MyRect",
        "fills": [{"fill-color": "#ffffff"}],
    }

    monkeypatch.setattr(token_tools, "get_colors_library", AsyncMock(return_value=color_tokens))
    monkeypatch.setattr(token_tools, "get_shape_details", AsyncMock(return_value=shape_details))
    
    modify_mock = AsyncMock()
    monkeypatch.setattr(token_tools, "modify_shape", modify_mock)

    result = await token_tools.apply_design_token(
        FILE_ID,
        PAGE_ID,
        [SHAPE_ID],
        token_id="token-123",
        token_type="color",
        target_property="fill",
    )

    assert result["status"] == "success"
    assert modify_mock.call_count == 1
    call_args = modify_mock.call_args[0]
    # Arguments: file_id, page_id, shape_id, attrs
    assert call_args[0] == FILE_ID
    assert call_args[1] == PAGE_ID
    assert call_args[2] == SHAPE_ID
    
    attrs = call_args[3]
    assert attrs["fills"][0]["fill-color"] == "#ff0000"
    assert attrs["fills"][0]["fill-color-ref-id"] == "token-123"
    assert attrs["fills"][0]["fill-color-ref-file"] == FILE_ID


@pytest.mark.asyncio
async def test_apply_typography_token_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    typo_tokens = [
        {
            "id": "typo-123",
            "name": "Heading 1",
            "font_family": "Inter",
            "font_size": 24,
            "font_weight": "700",
            "font_style": "normal",
            "line_height": 1.2,
            "letter_spacing": 0,
        }
    ]
    shape_details = {
        "id": SHAPE_ID,
        "type": "text",
        "name": "Headline",
        "content": {
            "type": "root",
            "children": [
                {
                    "font-size": "16",
                    "font-weight": "400",
                }
            ],
        },
    }

    monkeypatch.setattr(token_tools, "get_typography_library", AsyncMock(return_value=typo_tokens))
    monkeypatch.setattr(token_tools, "get_shape_details", AsyncMock(return_value=shape_details))
    
    modify_mock = AsyncMock()
    monkeypatch.setattr(token_tools, "modify_shape", modify_mock)

    result = await token_tools.apply_design_token(
        FILE_ID,
        PAGE_ID,
        [SHAPE_ID],
        token_id="typo-123",
        token_type="typography",
    )

    assert result["status"] == "success"
    assert modify_mock.call_count == 1
    attrs = modify_mock.call_args[0][3]
    content_child = attrs["content"]["children"][0]
    assert content_child["typography-ref-id"] == "typo-123"
    assert content_child["font-family"] == "Inter"
    assert content_child["font-size"] == "24"
    assert content_child["font-weight"] == "700"


def test_resolve_color_keyword_scoring() -> None:
    color_tokens = [
        {"id": "default-accent", "name": "Accent", "color": "#F4B860"},
        {"id": "warning-accent", "name": "Warning Color", "color": "#F4B860"},
        {"id": "offline-accent", "name": "Offline Mode Accent", "color": "#F4B860"},
    ]

    # Offline context should score higher for the offline token
    resolved = token_tools.resolve_color("#F4B860", "offline-indicator", "", color_tokens)
    assert resolved == "offline-accent"

    # Warning context should score higher for the warning token
    resolved = token_tools.resolve_color("#f4b860", "warning_badge", "", color_tokens)
    assert resolved == "warning-accent"

    # Default fallback when context has no matching keywords
    resolved = token_tools.resolve_color("#F4B860", "some-neutral-shape", "", color_tokens)
    assert resolved in ("default-accent", "warning-accent", "offline-accent")


@pytest.mark.asyncio
async def test_auto_bind_library_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    color_tokens = [
        {"id": "col-red", "name": "Danger", "color": "#FF0000"},
        {"id": "col-blue", "name": "Info", "color": "#0000FF"},
    ]
    typo_tokens = [
        {
            "id": "typo-body",
            "name": "Body Text",
            "font_family": "Roboto",
            "font_size": 14,
            "font_weight": "400",
        }
    ]

    # Two shapes on the page: a rect with hardcoded red fill, and a text shape with Roboto 14px 400weight
    page_objects = [
        {"id": "shape-rect", "type": "rect"},
        {"id": "shape-text", "type": "text"},
    ]
    rect_details = {
        "id": "shape-rect",
        "type": "rect",
        "name": "Danger Box",
        "fills": [{"fill-color": "#FF0000"}],
        "strokes": [{"stroke-color": "#0000FF"}],
    }
    text_details = {
        "id": "shape-text",
        "type": "text",
        "name": "Body Paragraph",
        "content": {
            "font-size": "14",
            "font-weight": "400",
        },
    }

    monkeypatch.setattr(token_tools, "get_colors_library", AsyncMock(return_value=color_tokens))
    monkeypatch.setattr(token_tools, "get_typography_library", AsyncMock(return_value=typo_tokens))
    monkeypatch.setattr(token_tools, "get_page_objects", AsyncMock(return_value=page_objects))
    
    async def mock_get_shape_details(file, page, shape_id):
        if shape_id == "shape-rect":
            return rect_details
        if shape_id == "shape-text":
            return text_details
        return {"error": "not found"}
        
    monkeypatch.setattr(token_tools, "get_shape_details", mock_get_shape_details)

    modify_mock = AsyncMock()
    monkeypatch.setattr(token_tools, "modify_shape", modify_mock)

    result = await token_tools.auto_bind_library_tokens(FILE_ID, PAGE_ID)

    assert result["status"] == "success"
    assert result["bound_shapes_count"] == 2
    assert modify_mock.call_count == 2
    
    # Verify calls to modify_shape
    calls = modify_mock.call_args_list
    # First call was shape-rect or shape-text depending on loop order
    shapes_modified = {call[0][2]: call[0][3] for call in calls}
    
    assert "shape-rect" in shapes_modified
    assert shapes_modified["shape-rect"]["fills"][0]["fill-color-ref-id"] == "col-red"
    assert shapes_modified["shape-rect"]["strokes"][0]["stroke-color-ref-id"] == "col-blue"

    assert "shape-text" in shapes_modified
    assert shapes_modified["shape-text"]["content"]["typography-ref-id"] == "typo-body"
