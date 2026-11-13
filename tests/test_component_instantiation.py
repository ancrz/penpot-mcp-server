"""Unit tests for shape decoding, component serialization, and instantiation."""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from penpot_mcp.tools import create as create_tools
from penpot_mcp.tools import shapes as shape_tools


FILE_ID = "11111111-1111-4111-8111-111111111111"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
COMPONENT_ID = "55555555-5555-4555-8555-555555555555"
MAIN_SHAPE_PAGE = "77777777-7777-4777-8777-777777777777"
MAIN_SHAPE_ID = "88888888-8888-4888-8888-888888888888"
CHILD_SHAPE_ID = "99999999-9999-4999-9999-999999999999"


def test_decode_shape_obj_handles_transit_map() -> None:
    # A raw Transit list map representing {"x": 10, "type": "rect"}
    raw_list = ["^ ", "x", 10, "type", "rect"]
    decoded = shape_tools._decode_shape_obj(raw_list)
    assert decoded == {"x": 10, "type": "rect"}


def test_serialize_shape_preserves_component_metadata() -> None:
    shape = {
        "id": "my-shape-id",
        "name": "My Component Instance",
        "type": "frame",
        "x": 100,
        "y": 200,
        "width": 50,
        "height": 50,
        "component-id": COMPONENT_ID,
        "component-file": FILE_ID,
        "component-root": True,
        "shape-ref": "master-shape-id",
        "group-type": "component",
    }
    serialized = shape_tools._serialize_shape(shape)
    assert serialized["component_id"] == COMPONENT_ID
    assert serialized["component_file"] == FILE_ID
    assert serialized["component_root"] is True
    assert serialized["shape_ref"] == "master-shape-id"
    assert serialized["group_type"] == "component"


@pytest.mark.asyncio
async def test_create_component_instance_clones_and_shifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Component definition in library
    component_definition = {
        "id": COMPONENT_ID,
        "name": "MyButton",
        "main-instance-id": MAIN_SHAPE_ID,
        "main-instance-page": MAIN_SHAPE_PAGE,
    }

    # Main shape and child shape in file
    file_data = {
        "data": {
            "components": {COMPONENT_ID: component_definition},
            "pages-index": {
                MAIN_SHAPE_PAGE: {
                    "objects": {
                        MAIN_SHAPE_ID: {
                            "type": "frame",
                            "name": "MyButtonMain",
                            "x": 10,
                            "y": 20,
                            "width": 100,
                            "height": 40,
                            "shapes": [CHILD_SHAPE_ID],
                            "selrect": {"x": 10, "y": 20, "width": 100, "height": 40, "x1": 10, "y1": 20, "x2": 110, "y2": 60},
                            "points": [{"x": 10, "y": 20}, {"x": 110, "y": 20}, {"x": 110, "y": 60}, {"x": 10, "y": 60}],
                        },
                        CHILD_SHAPE_ID: {
                            "type": "rect",
                            "name": "Background",
                            "parent-id": MAIN_SHAPE_ID,
                            "x": 10,
                            "y": 20,
                            "width": 100,
                            "height": 40,
                        },
                    }
                }
            },
        }
    }

    # Mock API call to get-file
    mock_command = AsyncMock(return_value=file_data)
    monkeypatch.setattr(create_tools.api, "command", mock_command)

    # Mock apply_changes
    mock_apply = AsyncMock()
    monkeypatch.setattr(create_tools, "apply_changes", mock_apply)

    # Generate predictable UUIDs for the instantiation
    uuid_counter = 0
    new_root_id = "new-root-uuid"
    new_child_id = "new-child-uuid"
    uuids = [new_root_id, new_child_id]

    def mock_uuid():
        nonlocal uuid_counter
        val = uuids[uuid_counter]
        uuid_counter += 1
        return val

    monkeypatch.setattr(create_tools, "new_uuid", mock_uuid)

    # Instantiate the component at target (x=100, y=150). Delta: dx=90, dy=130
    result = await create_tools.create_component_instance(
        file_id=FILE_ID,
        page_id=PAGE_ID,
        component_id=COMPONENT_ID,
        x=100,
        y=150,
    )

    assert result["instance_id"] == new_root_id
    assert result["component_id"] == COMPONENT_ID
    assert result["child_count"] == 1

    # Verify command was called correctly
    mock_command.assert_called_once_with("get-file", {"id": FILE_ID, "components-v2": True})

    # Verify changes applied
    mock_apply.assert_called_once()
    changes_sent = mock_apply.call_args[0][1]
    assert len(changes_sent) == 2

    # Verify root clone properties
    root_change = next(c for c in changes_sent if c["obj"]["id"] == new_root_id)
    root_shape = root_change["obj"]
    assert root_shape["x"] == 100
    assert root_shape["y"] == 150
    assert root_shape["component-root"] is True
    assert root_shape["component-id"] == COMPONENT_ID
    assert root_shape["component-file"] == FILE_ID
    assert root_shape["shape-ref"] == MAIN_SHAPE_ID
    assert root_shape["shapes"] == [new_child_id]
    assert root_shape["selrect"]["x"] == 100
    assert root_shape["selrect"]["y"] == 150
    assert root_shape["points"][0]["x"] == 100
    assert root_shape["points"][0]["y"] == 150

    # Verify child clone properties
    child_change = next(c for c in changes_sent if c["obj"]["id"] == new_child_id)
    child_shape = child_change["obj"]
    assert child_shape["parent-id"] == new_root_id
    assert child_shape["x"] == 100
    assert child_shape["y"] == 150
    assert child_shape["component-id"] == COMPONENT_ID
    assert child_shape["component-file"] == FILE_ID
    assert child_shape["shape-ref"] == CHILD_SHAPE_ID
