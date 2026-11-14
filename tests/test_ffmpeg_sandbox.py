"""Unit tests for the sandboxed FFmpeg MCP server and gateway integration."""

from __future__ import annotations

import pytest

from penpot_mcp.agent_gateway import AGENT_FUNCTION_DECLARATIONS
from penpot_mcp.ffmpeg_mcp_server import (
    SANDBOX_ROOT,
    dispatch_media_action,
    resolve_sandbox_path,
)


def test_resolve_sandbox_path_valid() -> None:
    resolved = resolve_sandbox_path("uploads/demo.png")
    assert resolved == (SANDBOX_ROOT / "uploads" / "demo.png").resolve()


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "../../etc/passwd",
        "uploads/../../../secret.txt",
        "uploads\\evil.png",
    ],
)
def test_resolve_sandbox_path_blocks_traversal(bad_path: str) -> None:
    with pytest.raises(ValueError, match="Ruta inválida|Path traversal"):
        resolve_sandbox_path(bad_path)


@pytest.mark.asyncio
async def test_dispatch_media_action_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="no soportada"):
        await dispatch_media_action("rm_rf", {"input_path": "uploads/a.png"})


def test_gateway_exposes_media_sandbox_ffmpeg() -> None:
    names = {decl["name"] for decl in AGENT_FUNCTION_DECLARATIONS}
    assert "media_sandbox_ffmpeg" in names
