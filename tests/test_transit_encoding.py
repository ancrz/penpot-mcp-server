"""Regression tests for typed Transit change encoding."""

import json

from penpot_mcp.services.api import _encode_transit_value
from penpot_mcp.services.transit import decode_transit


def test_encode_transit_value_preserves_nested_uuid_types() -> None:
    payload = {
        "id": "11111111-1111-4111-8111-111111111111",
        "features": ["components/v2", "styles/v2"],
        "changes": [
            {
                "type": "mod-obj",
                "operations": [
                    {
                        "type": "set",
                        "attr": "component-id",
                        "val": "22222222-2222-4222-8222-222222222222",
                    }
                ],
            }
        ],
    }

    encoded = _encode_transit_value(payload)

    assert encoded["~:id"] == "~u11111111-1111-4111-8111-111111111111"
    assert encoded["~:features"] == ["components/v2", "styles/v2"]
    operation = encoded["~:changes"][0]["~:operations"][0]
    assert operation["~:type"] == "~:set"
    assert operation["~:attr"] == "~:component-id"
    assert operation["~:val"] == "~u22222222-2222-4222-8222-222222222222"


def test_encode_transit_value_keeps_uuid_shaped_text_as_text() -> None:
    value = "33333333-3333-4333-8333-333333333333"

    encoded = _encode_transit_value({"name": value, "path": value})

    assert encoded["~:name"] == value
    assert encoded["~:path"] == value


def test_encode_transit_value_escapes_reserved_string_prefixes() -> None:
    payload = {"name": "~:literal", "path": "^0"}

    encoded = _encode_transit_value(payload)

    assert encoded["~:name"] == "~~:literal"
    assert encoded["~:path"] == "~^0"
    assert decode_transit(json.dumps(encoded)) == payload
