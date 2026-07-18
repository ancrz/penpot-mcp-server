"""Tests for native Penpot typography asset mutations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from penpot_mcp.tools import typographies as typography_tools
from penpot_mcp.tools.components import _extract_typographies


FILE_ID = "11111111-1111-4111-8111-111111111111"
TYPOGRAPHY_ID = "22222222-2222-4222-8222-222222222222"
FILE_INFO = {
    "revn": 42,
    "vern": 0,
    "features": ["styles/v2", "components/v2"],
}
EXISTING_TYPOGRAPHY = {
    "id": TYPOGRAPHY_ID,
    "name": "Body",
    "font_id": "sourcesanspro",
    "font_family": "Source Sans Pro",
    "font_variant_id": "regular",
    "font_size": "16",
    "font_weight": "400",
    "font_style": "normal",
    "letter_spacing": "0",
    "line_height": "1.5",
    "text_transform": "none",
    "path": "Text",
    "plugin_data": {"example/plugin": {"locked": True}},
}


@pytest.mark.asyncio
async def test_create_typography_submits_native_add_typography_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(typography_tools, "new_uuid", lambda: TYPOGRAPHY_ID)
    monkeypatch.setattr(
        typography_tools, "get_file_info", AsyncMock(return_value=FILE_INFO)
    )
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(typography_tools.api, "update_file_transit", update_file)

    result = await typography_tools.create_typography(
        FILE_ID,
        name="Heading",
        font_id="inter",
        font_family="Inter",
        font_variant_id="700",
        font_size="32",
        font_weight="700",
        font_style="normal",
        line_height="1.2",
        letter_spacing="-0.5",
        text_transform="uppercase",
        path="Text/Display",
    )

    assert result == {
        "id": TYPOGRAPHY_ID,
        "name": "Heading",
        "font-id": "inter",
        "font-family": "Inter",
        "font-variant-id": "700",
        "font-size": "32",
        "font-weight": "700",
        "font-style": "normal",
        "line-height": "1.2",
        "letter-spacing": "-0.5",
        "text-transform": "uppercase",
        "path": "Text/Display",
    }
    kwargs = update_file.await_args.kwargs
    assert kwargs["file_id"] == FILE_ID
    assert kwargs["revn"] == 42
    assert kwargs["vern"] == 0
    assert kwargs["features"] == FILE_INFO["features"]
    assert kwargs["changes"] == [
        {"type": "add-typography", "typography": result}
    ]


@pytest.mark.asyncio
async def test_update_typography_merges_changes_into_existing_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        typography_tools,
        "get_typography_library",
        AsyncMock(return_value=[EXISTING_TYPOGRAPHY]),
    )
    monkeypatch.setattr(
        typography_tools, "get_file_info", AsyncMock(return_value=FILE_INFO)
    )
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(typography_tools.api, "update_file_transit", update_file)

    result = await typography_tools.update_typography(
        FILE_ID,
        TYPOGRAPHY_ID,
        name="Body Large",
        font_size="20",
        path="Text/Body",
    )

    assert result == {
        "id": TYPOGRAPHY_ID,
        "name": "Body Large",
        "font-id": "sourcesanspro",
        "font-family": "Source Sans Pro",
        "font-variant-id": "regular",
        "font-size": "20",
        "font-weight": "400",
        "font-style": "normal",
        "line-height": "1.5",
        "letter-spacing": "0",
        "text-transform": "none",
        "path": "Text/Body",
        "plugin-data": {"example/plugin": {"locked": True}},
    }
    assert update_file.await_args.kwargs["changes"] == [
        {"type": "mod-typography", "typography": result}
    ]


@pytest.mark.asyncio
async def test_delete_typography_submits_native_del_typography_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        typography_tools, "get_file_info", AsyncMock(return_value=FILE_INFO)
    )
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(typography_tools.api, "update_file_transit", update_file)

    result = await typography_tools.delete_typography(FILE_ID, TYPOGRAPHY_ID)

    assert result == {"id": TYPOGRAPHY_ID, "deleted": True}
    assert update_file.await_args.kwargs["changes"] == [
        {"type": "del-typography", "id": TYPOGRAPHY_ID}
    ]


@pytest.mark.asyncio
async def test_update_typography_rejects_unknown_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        typography_tools, "get_typography_library", AsyncMock(return_value=[])
    )

    with pytest.raises(ValueError, match="Typography asset .* not found"):
        await typography_tools.update_typography(FILE_ID, TYPOGRAPHY_ID, name="Body")


@pytest.mark.asyncio
async def test_create_typography_rejects_empty_required_field() -> None:
    with pytest.raises(ValueError, match="font_family must not be empty"):
        await typography_tools.create_typography(
            FILE_ID,
            name="Body",
            font_family=" ",
        )


def test_extract_typographies_includes_update_fields_and_plugin_data() -> None:
    raw = {
        "name": "Body",
        "font-id": "sourcesanspro",
        "font-family": "Source Sans Pro",
        "font-variant-id": "regular",
        "font-size": "16",
        "font-weight": "400",
        "font-style": "normal",
        "letter-spacing": "0",
        "line-height": "1.5",
        "text-transform": "none",
        "path": "Text",
        "plugin-data": {"example/plugin": {"locked": True}},
    }

    assert _extract_typographies({"typographies": {TYPOGRAPHY_ID: raw}}) == [
        EXISTING_TYPOGRAPHY
    ]
