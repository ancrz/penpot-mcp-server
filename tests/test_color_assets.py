"""Tests for native Penpot color asset mutations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from penpot_mcp.tools import colors as color_tools
from penpot_mcp.tools.components import _extract_colors


FILE_ID = "11111111-1111-4111-8111-111111111111"
COLOR_ID = "22222222-2222-4222-8222-222222222222"
FILE_INFO = {
    "revn": 42,
    "vern": 0,
    "features": ["styles/v2", "components/v2"],
}


@pytest.mark.asyncio
async def test_create_color_submits_native_add_color_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(color_tools, "new_uuid", lambda: COLOR_ID)
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    result = await color_tools.create_color(
        FILE_ID,
        name="Primary",
        color="#DFFB73",
        opacity=0.8,
        path="Accent",
    )

    assert result == {
        "id": COLOR_ID,
        "name": "Primary",
        "color": "#dffb73",
        "opacity": 0.8,
        "path": "Accent",
    }
    kwargs = update_file.await_args.kwargs
    assert kwargs["file_id"] == FILE_ID
    assert kwargs["revn"] == 42
    assert kwargs["vern"] == 0
    assert kwargs["features"] == FILE_INFO["features"]
    assert kwargs["changes"] == [
        {
            "type": "add-color",
            "color": {
                "id": COLOR_ID,
                "name": "Primary",
                "path": "Accent",
                "color": "#dffb73",
                "opacity": 0.8,
            },
        }
    ]


@pytest.mark.asyncio
async def test_update_color_merges_changes_into_existing_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {
        "id": COLOR_ID,
        "name": "Accent",
        "path": "",
        "color": "#dffb73",
        "opacity": 1,
        "gradient": None,
    }
    monkeypatch.setattr(
        color_tools, "get_colors_library", AsyncMock(return_value=[existing])
    )
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    result = await color_tools.update_color(
        FILE_ID,
        COLOR_ID,
        name="Primary",
        path="Accent",
    )

    assert result == {
        "id": COLOR_ID,
        "name": "Primary",
        "path": "Accent",
        "color": "#dffb73",
        "opacity": 1,
    }
    assert update_file.await_args.kwargs["changes"] == [
        {"type": "mod-color", "color": result}
    ]


@pytest.mark.asyncio
async def test_delete_color_submits_native_del_color_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    result = await color_tools.delete_color(FILE_ID, COLOR_ID)

    assert result == {"id": COLOR_ID, "deleted": True}
    assert update_file.await_args.kwargs["changes"] == [
        {"type": "del-color", "id": COLOR_ID}
    ]


def test_extract_colors_preserves_image_backed_assets() -> None:
    image = {"id": "media-id", "width": 16, "height": 16}

    colors = _extract_colors(
        {"colors": {COLOR_ID: {"name": "Pattern", "path": "Media", "image": image}}}
    )

    assert colors == [
        {
            "id": COLOR_ID,
            "name": "Pattern",
            "color": None,
            "opacity": None,
            "gradient": None,
            "image": image,
            "path": "Media",
        }
    ]


@pytest.mark.asyncio
async def test_update_color_defaults_missing_opacity_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {
        "id": COLOR_ID,
        "name": "Primary",
        "path": "Accent",
        "color": "#dffb73",
        "opacity": None,
    }
    monkeypatch.setattr(
        color_tools, "get_colors_library", AsyncMock(return_value=[existing])
    )
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    result = await color_tools.update_color(FILE_ID, COLOR_ID, name="Renamed")

    assert result["opacity"] == 1
    assert update_file.await_args.kwargs["changes"] == [
        {"type": "mod-color", "color": result}
    ]


@pytest.mark.asyncio
async def test_create_color_with_gradient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(color_tools, "new_uuid", lambda: COLOR_ID)
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    gradient_data = {
        "type": "linear",
        "stops": [
            {"color": "#FFFFFF", "opacity": 1.0, "offset": 0.0},
            {"color": "#000000", "opacity": 0.5, "offset": 1.0},
        ],
    }

    result = await color_tools.create_color(
        FILE_ID,
        name="LinearGrad",
        gradient=gradient_data,
        path="Gradients",
    )

    assert result["id"] == COLOR_ID
    assert result["name"] == "LinearGrad"
    assert result["path"] == "Gradients"
    assert "color" not in result
    assert result["gradient"]["type"] == "linear"
    assert result["gradient"]["stops"][0]["color"] == "#ffffff"
    assert result["gradient"]["stops"][1]["opacity"] == 0.5

    kwargs = update_file.await_args.kwargs
    assert kwargs["changes"] == [
        {"type": "add-color", "color": result}
    ]


@pytest.mark.asyncio
async def test_update_color_with_gradient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {
        "id": COLOR_ID,
        "name": "LinearGrad",
        "path": "Gradients",
        "gradient": {
            "type": "linear",
            "stops": [{"color": "#ffffff", "offset": 0}, {"color": "#000000", "offset": 1}],
        },
    }
    monkeypatch.setattr(
        color_tools, "get_colors_library", AsyncMock(return_value=[existing])
    )
    monkeypatch.setattr(color_tools, "get_file_info", AsyncMock(return_value=FILE_INFO))
    update_file = AsyncMock(return_value={"revn": 43})
    monkeypatch.setattr(color_tools.api, "update_file_transit", update_file)

    new_gradient = {
        "type": "radial",
        "stops": [
            {"color": "#FF0000", "offset": 0.0},
            {"color": "#0000FF", "offset": 1.0},
        ],
    }

    result = await color_tools.update_color(
        FILE_ID,
        COLOR_ID,
        gradient=new_gradient,
    )

    assert result["gradient"]["type"] == "radial"
    assert result["gradient"]["stops"][0]["color"] == "#ff0000"
    assert "color" not in result

    assert update_file.await_args.kwargs["changes"] == [
        {"type": "mod-color", "color": result}
    ]


@pytest.mark.asyncio
async def test_create_color_validates_mutually_exclusive_inputs() -> None:
    gradient_data = {
        "type": "linear",
        "stops": [{"color": "#FFFFFF", "offset": 0.0}],
    }
    with pytest.raises(ValueError, match="Cannot specify both color and gradient"):
        await color_tools.create_color(FILE_ID, "Invalid", color="#FFFFFF", gradient=gradient_data)

    with pytest.raises(ValueError, match="Must specify either color or gradient"):
        await color_tools.create_color(FILE_ID, "Invalid")

