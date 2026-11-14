"""Unit tests for AEP Code Mode engine and the unified Design & Document Suite MCP."""

from __future__ import annotations

from pathlib import Path
import pytest

from penpot_mcp import suite_mcp_server
from penpot_mcp.agent_gateway import AGENT_FUNCTION_DECLARATIONS
from penpot_mcp.code_mode import execute_code_pipeline
from penpot_mcp.services.attachments import parse_uploaded_attachment


@pytest.mark.asyncio
async def test_code_mode_pipeline_pipes_steps_and_compacts() -> None:
    seen: list[tuple[str, dict]] = []

    async def _fake_dispatch(action: str, params: dict) -> dict:
        seen.append((action, params))
        if action == "step_a":
            return {"status": "OK", "output_path": "outputs/a.png", "extra_noise": "x" * 500}
        return {"status": "OK", "output_path": "outputs/b.webp", "used": params.get("input_path")}

    res = await execute_code_pipeline(
        [
            {"id": "s1", "action": "step_a", "input_path": "uploads/raw.png"},
            {"id": "s2", "action": "step_b", "input_path": "$steps.s1.output_path"},
        ],
        _fake_dispatch,
        verbosity="minimal",
    )
    assert res["status"] == "OK"
    assert res["steps_run"] == 2
    assert res["output_path"] == "outputs/b.webp"
    assert "steps" not in res  # stripped by minimal verbosity
    assert seen[1][1]["input_path"] == "outputs/a.png"


@pytest.mark.asyncio
async def test_wcag_contrast_and_svg_optimize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suite_mcp_server, "SANDBOX_ROOT", tmp_path.resolve())
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    svg_in = uploads / "raw.svg"
    svg_in.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
        "<metadata>junk</metadata><g inkscape:label=\"empty\"></g>"
        '<rect width="10" height="10" fill="#000"/></svg>',
        encoding="utf-8",
    )

    wcag = await suite_mcp_server.wcag_contrast("#FFFFFF", "#0F172A")
    assert wcag["ratio"] > 15.0
    assert wcag["aaa_normal"] is True

    opt = await suite_mcp_server.svg_optimize("uploads/raw.svg", "clean.svg")
    assert opt["status"] == "OK"
    cleaned = (tmp_path / opt["output_path"]).read_text(encoding="utf-8")
    assert "metadata" not in cleaned
    assert "<rect" in cleaned


@pytest.mark.asyncio
async def test_data_sandbox_query_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suite_mcp_server, "SANDBOX_ROOT", tmp_path.resolve())
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "users.csv").write_text("name,role\nAna,admin\nLuis,viewer\n", encoding="utf-8")

    res = await suite_mcp_server.data_sandbox_query("uploads/users.csv", filter_col="role", filter_contains="adm")
    assert res["total_matching"] == 1
    assert res["rows"][0]["name"] == "Ana"


def test_pdf_attachment_accepted_and_suite_declared() -> None:
    att = parse_uploaded_attachment(filename="brief.pdf", content_bytes=b"%PDF-1.4\n%%EOF")
    assert att["kind"] == "pdf"
    assert "design_suite_sandbox" in att["context_text"]

    names = {d["name"] for d in AGENT_FUNCTION_DECLARATIONS}
    assert "media_sandbox_ffmpeg" in names
    assert "design_suite_sandbox" in names
