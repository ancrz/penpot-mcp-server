"""Sandbox session lifecycle, persistence policy (ephemeral vs no-expiration), and artifact registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote

DEFAULT_EPHEMERAL_TTL_SEC = int(os.environ.get("SANDBOX_EPHEMERAL_TTL_SEC", "1800"))


def get_sandbox_root() -> Path:
    root = Path(os.environ.get("SANDBOX_ROOT", "/sandbox")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_sandbox_download(rel_path: str) -> Path:
    """Strictly resolve a sandbox file path for safe artifact download."""
    root = get_sandbox_root()
    cleaned = (rel_path or "").strip().lstrip("/")
    if not cleaned or "\\" in cleaned:
        raise ValueError(f"Ruta de artefacto inválida: {rel_path!r}")
    candidate = (root / cleaned).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Acceso denegado fuera del sandbox: {rel_path!r}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Artefacto no encontrado en sandbox: {rel_path!r}")
    return candidate


class SandboxSessionManager:
    """Tracks per-chat sandbox persistence (ephemeral TTL vs pinned no-expiration) in Valkey."""

    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    def _key(self, session_id: str) -> str:
        safe = "".join(c for c in (session_id or "default") if c.isalnum() or c in "-_.")[:64]
        return f"sandbox:session:{safe or 'default'}"

    async def _load(self, session_id: str) -> dict[str, Any]:
        key = self._key(session_id)
        try:
            from penpot_mcp.services.db import db

            client = db.cache._client
            if client is not None:
                raw = await client.get(key)
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        self._memory[key] = data
                        return data
        except Exception:
            pass
        return self._memory.get(
            key,
            {
                "session_id": session_id or "default",
                "persistent": False,
                "ttl_seconds": DEFAULT_EPHEMERAL_TTL_SEC,
                "artifacts": [],
                "updated_at": time.time(),
            },
        )

    async def _save(self, session_id: str, state: dict[str, Any]) -> None:
        key = self._key(session_id)
        state["updated_at"] = time.time()
        self._memory[key] = state
        try:
            from penpot_mcp.services.db import db

            client = db.cache._client
            if client is not None:
                payload = json.dumps(state)
                if state.get("persistent"):
                    # No expiration in Valkey for long-running sessions
                    await client.set(key, payload)
                else:
                    await client.setex(key, DEFAULT_EPHEMERAL_TTL_SEC, payload)
            await db.sync_chat_sandbox_state(
                session_id,
                persistent=bool(state.get("persistent")),
                artifacts=list(state.get("artifacts") or []),
            )
        except Exception:
            pass

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._load(session_id)

    async def set_policy(self, session_id: str, persistent: bool) -> dict[str, Any]:
        state = await self._load(session_id)
        state["persistent"] = bool(persistent)
        state["ttl_seconds"] = None if persistent else DEFAULT_EPHEMERAL_TTL_SEC
        await self._save(session_id, state)
        return state

    async def register_artifact(
        self,
        session_id: str,
        rel_path: str,
        *,
        tool: str = "sandbox",
    ) -> dict[str, Any]:
        state = await self._load(session_id)
        clean_rel = rel_path.strip().lstrip("/")
        entry = {
            "path": clean_rel,
            "name": Path(clean_rel).name,
            "tool": tool,
            "download_url": f"/agent/sandbox/download?path={quote(clean_rel, safe='/')}",
            "created_at": time.time(),
        }
        artifacts: list[dict[str, Any]] = [
            a for a in (state.get("artifacts") or []) if a.get("path") != clean_rel
        ]
        artifacts.append(entry)
        state["artifacts"] = artifacts[-50:]
        await self._save(session_id, state)
        return entry


sandbox_sessions = SandboxSessionManager()
