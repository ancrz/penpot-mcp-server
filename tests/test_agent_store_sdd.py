"""Unit tests for dual-DB AgentStateStore (SQLite isolation) and SDD schemas/tokens."""

from __future__ import annotations

from pathlib import Path

import pytest

from penpot_mcp.agent.schemas import (
    AgentChatRequestSchema,
    AgentDbConfigSummary,
    ProviderCatalogEntry,
    QuickActionSpec,
)
from penpot_mcp.agent.tokens import DESIGN_TOKENS, PROVIDER_CATALOG, QUICK_ACTIONS_CATALOG
from penpot_mcp.services.agent_store import AgentStateStore


def test_sdd_tokens_and_schemas() -> None:
    assert "accent" in DESIGN_TOKENS
    for key, entry in PROVIDER_CATALOG.items():
        parsed = ProviderCatalogEntry.model_validate(entry)
        assert parsed.label
        assert len(parsed.models) >= 1, key
    for qa in QUICK_ACTIONS_CATALOG:
        parsed_qa = QuickActionSpec.model_validate(qa)
        assert parsed_qa.id and parsed_qa.prompt
    req = AgentChatRequestSchema(prompt="Hola", session_id="s-1")
    assert req.session_id == "s-1"
    cfg = AgentDbConfigSummary(backend="sqlite", isolated=True, target="/sandbox/agent_state.db")
    assert cfg.isolated is True


@pytest.mark.asyncio
async def test_agent_store_sqlite_isolation_and_seed(tmp_path: Path) -> None:
    db_file = tmp_path / "agent_state.db"
    store = AgentStateStore(backend="sqlite", db_url=f"sqlite:///{db_file}")
    desc = store.describe()
    assert desc["backend"] == "sqlite"
    assert desc["isolated"] is True

    # Fresh start seeds 'default'
    sessions = await store.list_chat_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "default"

    # Upsert & read
    await store.upsert_chat_session(
        "s-sqlite-1",
        title="SQLite Session",
        messages=[{"id": "u-0", "role": "user", "text": "test"}],
        sandbox_persistent=True,
        last_revn=42,
    )
    loaded = await store.get_chat_session("s-sqlite-1")
    assert loaded is not None
    assert loaded["sandbox_persistent"] is True
    assert loaded["last_revn"] == 42
    assert loaded["messages"][0]["text"] == "test"

    # Protect 'default' from deletion, allow deleting custom session
    assert await store.delete_chat_session("default") is False
    assert await store.delete_chat_session("s-sqlite-1") is True
    assert await store.get_chat_session("s-sqlite-1") is None
