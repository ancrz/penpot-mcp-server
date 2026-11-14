"""Dual-DB persistence abstraction for the agentic layer (SQLite + sqlite-vec, dedicated Postgres, or Penpot Postgres).

Decouples agentic state (chat sessions, semantic memory, sandbox policies, artifacts)
from Penpot's core schema so Penpot upgrades and agentic upgrades can evolve independently.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from penpot_mcp.config import settings
from penpot_mcp.services.vision import compute_query_embedding

if TYPE_CHECKING:
    import sqlite3


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class AgentStateStore:
    """Routes agentic chat/memory persistence to SQLite+sqlite-vec, 2nd Postgres, or PenpotDB."""

    def __init__(self, backend: str | None = None, db_url: str | None = None) -> None:
        self.backend = (backend or settings.agent_db_backend or "penpot_postgres").strip().lower()
        self.db_url = (db_url if db_url is not None else settings.agent_db_url).strip()
        self._sqlite_path: Path | None = None
        self._vec_version: str | None = None
        if self.backend == "sqlite":
            raw_path = self.db_url.removeprefix("sqlite:///") or str(
                Path(settings.sandbox_root) / "agent_state.db"
            )
            self._sqlite_path = Path(raw_path)

    def _probe_sqlite_vec(self) -> tuple[bool, str | None]:
        try:
            import sqlite3
            import sqlite_vec  # type: ignore[import-untyped]

            conn = sqlite3.connect(":memory:")
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                ver = str(conn.execute("SELECT vec_version()").fetchone()[0])
                return True, ver
            finally:
                conn.close()
        except Exception:
            return False, None

    def describe(self) -> dict[str, Any]:
        vec_ok, vec_ver = self._probe_sqlite_vec()
        return {
            "backend": self.backend,
            "isolated": self.backend in ("sqlite", "postgres"),
            "target": str(self._sqlite_path) if self._sqlite_path else (self.db_url or settings.penpot_db_host),
            "sqlite_vec": vec_ok,
            "vec_version": vec_ver,
        }

    def _ensure_sqlite(self) -> tuple[sqlite3.Connection, bool]:
        import sqlite3

        assert self._sqlite_path is not None
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.row_factory = sqlite3.Row

        vec_loaded = False
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            vec_loaded = True
        except Exception:
            vec_loaded = False

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
            CREATE TABLE IF NOT EXISTS mcp_agent_memory_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL,
                embedding_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(kind, ref_id)
            );
            """
        )
        if vec_loaded:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS mcp_agent_memory_vec USING vec0(
                    embedding float[64]
                );
                """
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO mcp_chat_sessions (session_id, title, messages, sandbox_persistent)
            VALUES (
                'default',
                'Sesión principal',
                '[{"id":"welcome","role":"assistant","text":"Penpot AI Studio listo (SQLite + sqlite-vec).","timestamp":"inicio"}]',
                0
            );
            """
        )
        conn.commit()
        return conn, vec_loaded

    async def index_agent_memory(
        self,
        kind: str,
        ref_id: str,
        text: str,
        *,
        session_id: str = "",
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Index a chat turn, sandbox artifact, or design decision into sqlite-vec semantic memory."""
        if self.backend != "sqlite":
            return {"indexed": False, "reason": "backend_not_sqlite"}
        clean_text = (text or "").strip()
        if not clean_text:
            return {"indexed": False, "reason": "empty_text"}

        vec = compute_query_embedding(clean_text)
        eff_summary = (summary or clean_text)[:400]
        conn, vec_loaded = self._ensure_sqlite()
        try:
            conn.execute(
                """
                INSERT INTO mcp_agent_memory_meta (kind, ref_id, session_id, summary, embedding_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(kind, ref_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    summary = excluded.summary,
                    embedding_json = excluded.embedding_json,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (kind, ref_id, session_id, eff_summary, json.dumps(vec)),
            )
            row = conn.execute(
                "SELECT id FROM mcp_agent_memory_meta WHERE kind = ? AND ref_id = ?",
                (kind, ref_id),
            ).fetchone()
            row_id = int(row["id"])
            if vec_loaded:
                import sqlite_vec

                conn.execute("DELETE FROM mcp_agent_memory_vec WHERE rowid = ?", (row_id,))
                conn.execute(
                    "INSERT INTO mcp_agent_memory_vec(rowid, embedding) VALUES (?, ?)",
                    (row_id, sqlite_vec.serialize_float32(vec)),
                )
            conn.commit()
            return {"indexed": True, "id": row_id, "vec0": vec_loaded}
        finally:
            conn.close()

    async def search_agent_memory(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic vector search over agentic memory using sqlite-vec (vec0) with cosine fallback."""
        if self.backend != "sqlite" or not (query or "").strip():
            return []
        qvec = compute_query_embedding(query)
        conn, vec_loaded = self._ensure_sqlite()
        try:
            if vec_loaded:
                import sqlite_vec

                sql = """
                    SELECT m.id, m.kind, m.ref_id, m.session_id, m.summary, m.updated_at, v.distance
                    FROM mcp_agent_memory_vec v
                    JOIN mcp_agent_memory_meta m ON m.id = v.rowid
                    WHERE v.embedding MATCH ? AND k = ?
                """
                params: list[Any] = [sqlite_vec.serialize_float32(qvec), max(1, limit * 2)]
                rows = conn.execute(sql, params).fetchall()
                results: list[dict[str, Any]] = []
                for r in rows:
                    if kind and r["kind"] != kind:
                        continue
                    dist = float(r["distance"])
                    results.append(
                        {
                            "id": int(r["id"]),
                            "kind": r["kind"],
                            "ref_id": r["ref_id"],
                            "session_id": r["session_id"],
                            "summary": r["summary"],
                            "distance": round(dist, 4),
                            "engine": "sqlite-vec",
                        }
                    )
                return results[:limit]

            rows = conn.execute(
                "SELECT id, kind, ref_id, session_id, summary, embedding_json FROM mcp_agent_memory_meta"
            ).fetchall()
            scored: list[dict[str, Any]] = []
            for r in rows:
                if kind and r["kind"] != kind:
                    continue
                sim = _cosine_sim(qvec, json.loads(r["embedding_json"] or "[]"))
                scored.append(
                    {
                        "id": int(r["id"]),
                        "kind": r["kind"],
                        "ref_id": r["ref_id"],
                        "session_id": r["session_id"],
                        "summary": r["summary"],
                        "similarity": round(sim, 4),
                        "engine": "python-cosine-fallback",
                    }
                )
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:limit]
        finally:
            conn.close()

    async def list_chat_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        if self.backend == "sqlite":
            conn, _ = self._ensure_sqlite()
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
            conn, _ = self._ensure_sqlite()
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
            conn, _ = self._ensure_sqlite()
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
            conn, _ = self._ensure_sqlite()
            try:
                conn.execute("DELETE FROM mcp_chat_sessions WHERE session_id = ?", (session_id.strip(),))
                conn.commit()
                return True
            finally:
                conn.close()

        from penpot_mcp.services.db import db

        return await db.delete_chat_session(session_id)


agent_store = AgentStateStore()
