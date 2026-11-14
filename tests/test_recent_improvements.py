"""Comprehensive test battery for recent improvements:
- sqlite-vec auto-indexing from POST /agent/chat & SandboxSessionManager.register_artifact
- GET /agent/memory/search route
- Idempotent fresh-start database seeding in PenpotDB.ensure_schema()
- Manual-edit Canvas Delta detection in PenpotDB.get_canvas_delta_since()
- AgentStateStore upsert idempotency & AgentDbConfigSummary SDD contract
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

import penpot_mcp.services.agent_store as agent_store_mod
from penpot_mcp import server as server_mod
from penpot_mcp.agent.schemas import AgentDbConfigSummary
from penpot_mcp.services.agent_store import AgentStateStore
from penpot_mcp.services.db import PenpotDB
from penpot_mcp.services.sandbox_sessions import SandboxSessionManager


def _make_starlette_request(
    method: str,
    path: str,
    *,
    query_string: str = "",
    body: dict[str, Any] | None = None,
) -> Request:
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8787),
    }
    return Request(scope, receive)


@dataclass
class _FakeTurn:
    reply: str = "Botón primario #6366f1 creado en el frame Login."
    model_used: str = "gemini-2.5-flash"
    provider: str = "gemini"
    actions_executed: list[Any] = field(default_factory=list)
    tool_trace: list[Any] = field(default_factory=list)
    created_shapes: list[Any] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)
    latency_ms: int = 12
    canvas_delta: dict[str, Any] = field(default_factory=dict)
    user_id: str = "user-1"
    file_id: str = "file-123"
    sandbox_persistent: bool = False
    context_summary: str = "file=test"
    error: str | None = None


@pytest.mark.asyncio
async def test_agent_chat_auto_indexes_and_memory_search_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AgentStateStore(backend="sqlite", db_url=f"sqlite:///{tmp_path}/route_vec.db")
    monkeypatch.setattr(agent_store_mod, "agent_store", store)

    async def fake_chat(*args: Any, **kwargs: Any) -> _FakeTurn:
        return _FakeTurn()

    import penpot_mcp.agent_gateway as gw_mod

    monkeypatch.setattr(gw_mod.agent_gateway, "chat", fake_chat)

    chat_req = _make_starlette_request(
        "POST",
        "/agent/chat",
        body={
            "prompt": "Crea un botón primario #6366f1 para Login",
            "session_id": "sess-route-1",
            "file_id": "file-123",
        },
    )
    chat_resp = await server_mod.agent_chat_route(chat_req)
    assert chat_resp.status_code == 200

    # Verify persisted session and sqlite-vec auto-index via GET /agent/memory/search
    loaded = await store.get_chat_session("sess-route-1")
    assert loaded is not None
    assert len(loaded["messages"]) == 2

    search_req = _make_starlette_request(
        "GET",
        "/agent/memory/search",
        query_string="q=boton+primario+6366f1+Login&kind=chat_turn&limit=5",
    )
    search_resp = await server_mod.agent_memory_search_route(search_req)
    assert search_resp.status_code == 200
    data = json.loads(search_resp.body.decode("utf-8"))
    assert data["status"] == "OK"
    assert data["backend"] == "sqlite"
    assert len(data["hits"]) >= 1
    assert data["hits"][0]["kind"] == "chat_turn"
    assert data["hits"][0]["session_id"] == "sess-route-1"


@pytest.mark.asyncio
async def test_sandbox_register_artifact_indexes_sqlite_vec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AgentStateStore(backend="sqlite", db_url=f"sqlite:///{tmp_path}/sandbox_vec.db")
    monkeypatch.setattr(agent_store_mod, "agent_store", store)

    mgr = SandboxSessionManager()
    entry = await mgr.register_artifact(
        "sess-art-1",
        "outputs/hero_banner.svg",
        tool="design_suite_sandbox",
    )
    assert entry["name"] == "hero_banner.svg"
    assert "download_url" in entry

    hits = await store.search_agent_memory("hero_banner svg design_suite_sandbox", kind="artifact", limit=3)
    assert len(hits) == 1
    assert hits[0]["ref_id"] == "sess-art-1:outputs/hero_banner.svg"
    assert hits[0]["session_id"] == "sess-art-1"


@pytest.mark.asyncio
async def test_sqlite_vec_upsert_updates_without_duplicates(tmp_path: Path) -> None:
    store = AgentStateStore(backend="sqlite", db_url=f"sqlite:///{tmp_path}/upsert_vec.db")
    await store.index_agent_memory(
        "chat_turn",
        "s1:1",
        "Versión inicial paleta azul claro",
        session_id="s1",
    )
    await store.index_agent_memory(
        "chat_turn",
        "s1:1",
        "Versión actualizada paleta verde esmeralda #10b981",
        session_id="s1",
    )
    hits = await store.search_agent_memory("paleta verde esmeralda #10b981", kind="chat_turn", limit=5)
    matching = [h for h in hits if h["ref_id"] == "s1:1"]
    assert len(matching) == 1
    assert "#10b981" in matching[0]["summary"]


def test_agent_store_describe_matches_sdd_contract(tmp_path: Path) -> None:
    store = AgentStateStore(backend="sqlite", db_url=f"sqlite:///{tmp_path}/desc.db")
    desc = store.describe()
    parsed = AgentDbConfigSummary.model_validate(desc)
    assert parsed.backend == "sqlite"
    assert parsed.isolated is True
    assert desc["sqlite_vec"] is True
    assert isinstance(desc["vec_version"], str) and desc["vec_version"].startswith("v")


class _FakeConn:
    def __init__(self, fetch_rows: list[list[dict[str, Any]]] | None = None) -> None:
        self.executed: list[str] = []
        self._fetch_rows = list(fetch_rows or [])

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if self._fetch_rows:
            return self._fetch_rows.pop(0)
        return []


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.asyncio
async def test_penpot_db_ensure_schema_idempotent_seeding() -> None:
    conn = _FakeConn()
    db = PenpotDB()
    db._pool = _FakePool(conn)  # type: ignore[assignment]

    await db.ensure_mcp_extensions_and_tables()

    joined = "\n".join(conn.executed)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in joined
    assert "CREATE TABLE IF NOT EXISTS public.mcp_user_ai_profile" in joined
    assert "CREATE TABLE IF NOT EXISTS public.mcp_user_ai_secrets" in joined
    assert "CREATE TABLE IF NOT EXISTS public.mcp_canvas_vision_index" in joined
    assert "CREATE TABLE IF NOT EXISTS public.mcp_chat_sessions" in joined
    assert "ADD COLUMN IF NOT EXISTS artifacts" in joined
    assert "INSERT INTO public.mcp_chat_sessions" in joined
    assert "ON CONFLICT (session_id) DO NOTHING" in joined
    assert "INSERT INTO public.mcp_user_ai_profile" in joined
    assert "FROM public.profile" in joined
    assert "ON CONFLICT (profile_id) DO NOTHING" in joined


@pytest.mark.asyncio
async def test_penpot_db_get_canvas_delta_since() -> None:
    # Case 1: no changes since revn 10
    conn_same = _FakeConn(fetch_rows=[[{"revn": 10}]])
    db_same = PenpotDB()
    db_same._pool = _FakePool(conn_same)  # type: ignore[assignment]
    delta_same = await db_same.get_canvas_delta_since("file-abc", since_revn=10)
    assert delta_same == {"current_revn": 10, "changed": False, "recent_changes": []}

    # Case 2: manual edits pushed revn from 10 -> 12
    conn_changed = _FakeConn(
        fetch_rows=[
            [{"revn": 12}],
            [
                {
                    "revn": 12,
                    "label": "update-rect",
                    "created_at": "2026-11-14T12:00:00Z",
                    "author": "Designer",
                }
            ],
        ]
    )
    db_changed = PenpotDB()
    db_changed._pool = _FakePool(conn_changed)  # type: ignore[assignment]
    delta_changed = await db_changed.get_canvas_delta_since("file-abc", since_revn=10)
    assert delta_changed["current_revn"] == 12
    assert delta_changed["changed"] is True
    assert len(delta_changed["recent_changes"]) == 1
    assert delta_changed["recent_changes"][0]["author"] == "Designer"
