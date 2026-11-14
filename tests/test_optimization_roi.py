"""Optimization & Code Mode ROI benchmark tests (token & turn savings)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from penpot_mcp.aep_engine import project_fields
from penpot_mcp.tools.subtools import SUBTOOLS_CATALOG, playbook_run


def _token_savings(before_obj: object, after_obj: object) -> dict[str, float]:
    before_bytes = len(json.dumps(before_obj, ensure_ascii=False, default=str).encode("utf-8"))
    after_bytes = len(json.dumps(after_obj, ensure_ascii=False, default=str).encode("utf-8"))
    before_tok = max(1, round(before_bytes / 4))
    after_tok = max(1, round(after_bytes / 4))
    saved_pct = round(((before_tok - after_tok) / before_tok) * 100.0, 2)
    return {"before_tokens": before_tok, "after_tokens": after_tok, "saved_pct": saved_pct}


@pytest.mark.asyncio
async def test_playbook_summary_preserves_shape_ids_and_cuts_tokens() -> None:
    """Verify return_only='summary' retains shape_id/name while cutting bulky step payloads."""
    fake_outputs = {
        "frame": json.dumps(
            {
                "status": "OK",
                "shape_id": "11111111-1111-1111-1111-111111111111",
                "name": "ROI Hero Frame",
                "geometry": {"x": 80, "y": 80, "width": 640, "height": 360},
                "fills": [{"fillColor": "#0f172a", "fillOpacity": 1.0}],
                "extra_metadata": {"tree": ["a", "b", "c", "d", "e"] * 10},
            }
        ),
        "text": json.dumps(
            {
                "status": "OK",
                "shape_id": "22222222-2222-2222-2222-222222222222",
                "name": "ROI Headline",
                "content": "Enterprise ROI Dashboard",
                "typography": {"fontFamily": "Inter", "fontSize": "28", "fontWeight": "700"},
                "extra_metadata": {"glyphs": list(range(50))},
            }
        ),
    }

    async def _fake_create(**kwargs: object) -> str:
        act = str(kwargs.get("action") or "frame")
        return fake_outputs.get(act, fake_outputs["frame"])

    steps = [
        {
            "id": "card",
            "tool": "create",
            "params": {"action": "frame", "file_id": "f1", "page_id": "p1", "name": "ROI Hero Frame"},
        },
        {
            "id": "title",
            "tool": "create",
            "depends_on": ["card"],
            "params": {
                "action": "text",
                "file_id": "f1",
                "page_id": "p1",
                "parent_id": "$steps.card.shape_id",
                "content": "Enterprise ROI Dashboard",
            },
        },
    ]

    with patch(
        "penpot_mcp.tools.subtools._build_batch_dispatch",
        return_value={"create": AsyncMock(side_effect=_fake_create)},
    ):
        raw_all = await playbook_run(
            action="code",
            name="roi-bench",
            steps=steps,
            return_only="all",
            verbosity="detail",
        )
        raw_summary = await playbook_run(
            action="code",
            name="roi-bench",
            steps=steps,
            return_only="summary",
            verbosity="summary",
        )

    all_data = json.loads(raw_all)
    sum_data = json.loads(raw_summary)

    assert sum_data["status"] == "OK"
    assert sum_data["steps_run"] == 2
    assert len(sum_data["steps_summary"]) == 2
    assert sum_data["steps_summary"][0]["shape_id"] == "11111111-1111-1111-1111-111111111111"
    assert sum_data["steps_summary"][1]["shape_id"] == "22222222-2222-2222-2222-222222222222"

    savings = _token_savings(all_data, sum_data)
    assert savings["saved_pct"] >= 50.0
    assert len(raw_summary) < len(raw_all)


def test_toolset_compression_and_field_projection_roi() -> None:
    """Verify 77 legacy tools compress into 14 sub-tools and select_fields shrinks trees."""
    total_actions = sum(len(v["actions"]) for v in SUBTOOLS_CATALOG.values())
    assert len(SUBTOOLS_CATALOG) == 14
    assert total_actions >= 70  # 70+ domain actions compressed into 14 sub-tools

    full_tree = {
        "status": "OK",
        "shapes": [
            {
                "id": f"shape-{i}",
                "name": f"Card {i}",
                "type": "frame",
                "x": 80 * i,
                "y": 100,
                "width": 320,
                "height": 200,
                "fills": [{"fillColor": "#1e293b", "fillOpacity": 1}],
                "strokes": [{"strokeColor": "#334155", "strokeWidth": 1}],
                "selrect": {"x": 80 * i, "y": 100, "width": 320, "height": 200, "x1": 80 * i, "y1": 100},
                "points": [{"x": 0, "y": 0}, {"x": 320, "y": 0}, {"x": 320, "y": 200}, {"x": 0, "y": 200}],
            }
            for i in range(25)
        ],
    }
    projected = project_fields(full_tree, ["shapes.id", "shapes.name", "shapes.x", "shapes.y"])
    savings = _token_savings(full_tree, projected)
    assert savings["saved_pct"] >= 65.0
