"""Unit tests for sandbox session persistence policy (ephemeral vs no-expiration) and artifact downloads."""

from __future__ import annotations

from pathlib import Path
import pytest

from penpot_mcp.services.sandbox_sessions import (
    DEFAULT_EPHEMERAL_TTL_SEC,
    SandboxSessionManager,
    resolve_sandbox_download,
)


@pytest.mark.asyncio
async def test_sandbox_session_policy_and_artifact_registration() -> None:
    mgr = SandboxSessionManager()

    ephemeral = await mgr.set_policy("chat-1", persistent=False)
    assert ephemeral["persistent"] is False
    assert ephemeral["ttl_seconds"] == DEFAULT_EPHEMERAL_TTL_SEC

    pinned = await mgr.set_policy("chat-1", persistent=True)
    assert pinned["persistent"] is True
    assert pinned["ttl_seconds"] is None

    art = await mgr.register_artifact("chat-1", "outputs/logo_opt.svg", tool="design_suite_sandbox")
    assert art["name"] == "logo_opt.svg"
    assert art["download_url"] == "/agent/sandbox/download?path=outputs/logo_opt.svg"

    state = await mgr.get_session("chat-1")
    assert len(state["artifacts"]) == 1
    assert state["persistent"] is True


def test_resolve_sandbox_download_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_ROOT", str(tmp_path))
    out = tmp_path / "outputs"
    out.mkdir()
    target = out / "asset.webp"
    target.write_bytes(b"RIFFdemo")

    assert resolve_sandbox_download("outputs/asset.webp") == target.resolve()

    with pytest.raises(ValueError, match="Acceso denegado|Ruta de artefacto inválida"):
        resolve_sandbox_download("../../etc/passwd")

    with pytest.raises(FileNotFoundError):
        resolve_sandbox_download("outputs/missing.webp")
