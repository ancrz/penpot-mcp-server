"""Dual-DB persistence abstraction for the agentic layer (SQLite, dedicated Postgres, or Penpot Postgres).

Decouples agentic state (chat sessions, sandbox policies, AI profiles) from Penpot's
core schema so Penpot upgrades and agentic upgrades can evolve independently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from penpot_mcp.config import settings

if TYPE_CHECKING:
    import sqlite3


class AgentStateStore:
    """Routes agentic chat/sandbox persistence to SQLite, a dedicated 2nd Postgres, or PenpotDB."""

    def __init__(self, backend: str | None = None, db_url: str | None = None) -> None:
        self.backend = (backend or settings.agent_db_backend or "penpot_postgres").strip().lower()
        self.db_url = (db_url if db_url is not None else settings.agent_db_url).strip()
        self._pg_pool: Any = None
        self._sqlite_path: Path | None = None
        if self.backend == "sqlite":
            raw_path = self.db_url.removeprefix("sqlite:///") or str(
                Path(settings.sandbox_root) / "agent_state.db"
            )
            self._sqlite_path = Path(raw_path)

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "isolated": self.backend in ("sqlite", "postgres"),
            "target": str(self._sqlite_path) if self._sqlite_path else (self.db_url or settings.penpot_db_host),
        }

    def _ensure_sqlite(self) -> sqlite3.Connection:
        import sqlite3

        assert self._sqlite_path is not None
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_chat_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT '',
                file_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT 'Sesión principal',
                messages TEXT NOT NULL DEFAULT '[]',
                sandbox_persistent INTEGER NOT NULL DEFAULT 0,
                last_revn INTEGER NOT NULL DEFAULT 0,
                artifacts TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO mcp_chat_sessions (session_id, title, messages, sandbox_persistent)
            VALUES (
                'default',
                'Sesión principal',
                '[{"id":"welcome","role":"assistant","text":"Penpot AI Studio listo (SQLite aislado).","timestamp":"inicio"}]',
                0
            );
            """
        )
        conn.commit()
        return conn

    async def list_chat_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        if self.backend == "sqlite":
            conn = self._ensure_sqlite()
            try:
                rows = conn.execute(
                    "SELECT session_id, user_id, file_id, title, messages, sandbox_persistent, last_revn, updated_at "
                    "FROM mcp_chat_sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [
                    {
                        "session_id": r["session_id"],
                        "user_id": r["user_id"],
                        "file_id": r["file_id"],
                        "title": r["title"],
                        "sandbox_persistent": bool(r["sandbox_persistent"]),
                        "last_revn": int(r["last_revn"] or 0),
                        "message_count": len(json.loads(r["messages"] or "[]")),
                        "updated_at": str(r["updated_at"]),
                    }
                    for r in rows
                ]
            finally:
                conn.close()

        from penpot_mcp.services.db import db

        return await db.list_chat_sessions(limit=limit)

    async def get_chat_session(self, session_id: str) -> dict[str, Any] | None:
        if self.backend == "sqlite":
            conn = self._ensure_sqlite()
            try:
                r = conn.execute(
                    "SELECT session_id, user_id, file_id, title, messages, sandbox_persistent, last_revn, artifacts, updated_at "
                    "FROM mcp_chat_sessions WHERE session_id = ?",
                    (session_id.strip(),),
                ).fetchone()
                if not r:
                    return None
                return {
                    "session_id": r["session_id"],
                    "user_id": r["user_id"],
                    "file_id": r["file_id"],
                    "title": r["title"],
                    "messages": json.loads(r["messages"] or "[]"),
                    "sandbox_persistent": bool(r["sandbox_persistent"]),
                    "last_revn": int(r["last_revn"] or 0),
                    "artifacts": json.loads(r["artifacts"] or "[]"),
                    "updated_at": str(r["updated_at"]),
                }
            finally:
                conn.close()

        from penpot_mcp.services.db import db

        return await db.get_chat_session(session_id)

    async def upsert_chat_session(
        self,
        session_id: str,
        *,
        user_id: str = "",
        file_id: str = "",
        title: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        sandbox_persistent: bool | None = None,
        last_revn: int | None = None,
    ) -> dict[str, Any]:
        if self.backend == "sqlite":
            existing = await self.get_chat_session(session_id)
            eff_title = title or (existing.get("title") if existing else "Nuevo chat") or "Nuevo chat"
            eff_msgs = list(messages) if messages is not None else list((existing or {}).get("messages") or [])
            eff_pers = bool(sandbox_persistent) if sandbox_persistent is not None else bool((existing or {}).get("sandbox_persistent"))
            eff_revn = max(int(last_revn or 0), int((existing or {}).get("last_revn") or 0))
            eff_uid = user_id or (existing or {}).get("user_id") or ""
            eff_fid = file_id or (existing or {}).get("file_id") or ""
            conn = self._ensure_sqlite()
            try:
                conn.execute(
                    """
                    INSERT INTO mcp_chat_sessions (session_id, user_id, file_id, title, messages, sandbox_persistent, last_revn, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id = CASE WHEN excluded.user_id <> '' THEN excluded.user_id ELSE mcp_chat_sessions.user_id END,
                        file_id = CASE WHEN excluded.file_id <> '' THEN excluded.file_id ELSE mcp_chat_sessions.file_id END,
                        title = excluded.title,
                        messages = excluded.messages,
                        sandbox_persistent = excluded.sandbox_persistent,
                        last_revn = MAX(mcp_chat_sessions.last_revn, excluded.last_revn),
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (session_id.strip(), eff_uid, eff_fid, eff_title, json.dumps(eff_msgs[-100:]), int(eff_pers), eff_revn),
                )
                conn.commit()
            finally:
                conn.close()
            return (await self.get_chat_session(session_id)) or {"session_id": session_id, "title": eff_title}

        from penpot_mcp.services.db import db

        return await db.upsert_chat_session(
            session_id,
            user_id=user_id,
            file_id=file_id,
            title=title,
            messages=messages,
            sandbox_persistent=sandbox_persistent,
            last_revn=last_revn,
        )

    async def delete_chat_session(self, session_id: str) -> bool:
        if not session_id or session_id == "default":
            return False
        if self.backend == "sqlite":
            conn = self._ensure_sqlite()
            try:
                conn.execute("DELETE FROM mcp_chat_sessions WHERE session_id = ?", (session_id.strip(),))
                conn.commit()
                return True
            finally:
                conn.close()

        from penpot_mcp.services.db import db

        return await db.delete_chat_session(session_id)


agent_store = AgentStateStore()
