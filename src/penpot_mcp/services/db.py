"""Direct PostgreSQL access to Penpot database."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any

import asyncpg

from penpot_mcp.config import settings
from penpot_mcp.services.cache import ValkeyCache

logger = logging.getLogger(__name__)


class PenpotDB:
    """Async PostgreSQL client for direct Penpot database queries."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self.cache = ValkeyCache(url=settings.valkey_url, default_ttl=settings.valkey_cache_ttl)

    async def connect(self) -> None:
        if settings.penpot_db_user == "penpot":
            logger.warning(
                "Connecting to PostgreSQL with default user 'penpot'. "
                "For hardened deployments, configure the read-only role PENPOT_DB_USER=penpot_mcp."
            )
        self._pool = await asyncpg.create_pool(
            host=settings.penpot_db_host,
            port=settings.penpot_db_port,
            database=settings.penpot_db_name,
            user=settings.penpot_db_user,
            password=settings.penpot_db_pass,
            min_size=2,
            max_size=10,
        )
        await self.ensure_mcp_extensions_and_tables()
        await self.cache.connect()

    async def ensure_mcp_extensions_and_tables(self) -> None:
        """Auto-initialize pgvector, pg_trgm, multi-user AI profile table, and canvas vision index."""
        if not self._pool:
            return
        try:
            async with self.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.mcp_user_ai_profile (
                        profile_id UUID PRIMARY KEY REFERENCES public.profile(id) ON DELETE CASCADE,
                        email TEXT NOT NULL DEFAULT '',
                        fullname TEXT NOT NULL DEFAULT '',
                        active_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                        credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
                        agent_instructions JSONB NOT NULL DEFAULT '{}'::jsonb,
                        last_file_id UUID,
                        last_page_id UUID,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.mcp_canvas_vision_index (
                        file_id UUID NOT NULL,
                        page_id UUID NOT NULL,
                        shape_id UUID NOT NULL,
                        shape_name TEXT NOT NULL DEFAULT '',
                        shape_type TEXT NOT NULL DEFAULT 'rect',
                        parent_id UUID,
                        frame_name TEXT NOT NULL DEFAULT '',
                        x DOUBLE PRECISION NOT NULL DEFAULT 0,
                        y DOUBLE PRECISION NOT NULL DEFAULT 0,
                        width DOUBLE PRECISION NOT NULL DEFAULT 0,
                        height DOUBLE PRECISION NOT NULL DEFAULT 0,
                        fill_color TEXT NOT NULL DEFAULT '',
                        text_content TEXT NOT NULL DEFAULT '',
                        semantic_summary TEXT NOT NULL DEFAULT '',
                        embedding vector(64),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                        PRIMARY KEY (file_id, page_id, shape_id)
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS mcp_canvas_vision_hnsw_idx
                        ON public.mcp_canvas_vision_index USING hnsw (embedding vector_cosine_ops);
                    """
                )
        except Exception as exc:
            logger.debug("Optional MCP pgvector/multi-user table init notice: %s", exc)

    async def close(self) -> None:
        await self.cache.close()
        if self._pool:
            await self._pool.close()

    @asynccontextmanager
    async def acquire(self):
        async with self._pool.acquire() as conn:
            yield conn

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        async with self.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    async def fetch_readonly(self, query: str, *args: Any) -> list[dict]:
        """Execute a free-form query inside a read-only transaction with a statement timeout."""
        async with self.acquire() as conn:
            async with conn.transaction(readonly=True):
                timeout_ms = max(1000, int(settings.db_statement_timeout_ms))
                await conn.execute("SELECT set_config('statement_timeout', $1, true)", f"{timeout_ms}ms")  # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
                rows = await conn.fetch(query, *args)
                return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        async with self.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    # ── Project queries ──────────────────────────────────────

    async def list_projects(self, team_id: str | None = None) -> list[dict]:
        if team_id:
            return await self.fetch(
                """
                SELECT p.id, p.name, p.is_default, p.team_id, t.name as team_name,
                       p.created_at, p.modified_at,
                       (SELECT COUNT(*) FROM file f WHERE f.project_id = p.id AND f.deleted_at IS NULL) as file_count
                FROM project p
                JOIN team t ON t.id = p.team_id
                WHERE p.deleted_at IS NULL AND p.team_id = $1
                ORDER BY p.modified_at DESC
                """,
                team_id,
            )
        return await self.fetch(
            """
            SELECT p.id, p.name, p.is_default, p.team_id, t.name as team_name,
                   p.created_at, p.modified_at,
                   (SELECT COUNT(*) FROM file f WHERE f.project_id = p.id AND f.deleted_at IS NULL) as file_count
            FROM project p
            JOIN team t ON t.id = p.team_id
            WHERE p.deleted_at IS NULL
            ORDER BY p.modified_at DESC
            """
        )

    # ── File queries ─────────────────────────────────────────

    async def list_files(self, project_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT f.id, f.name, f.project_id, f.is_shared, f.revn, f.vern,
                   f.created_at, f.modified_at, f.version,
                   p.name as project_name,
                   (SELECT COUNT(*) FROM file_media_object fmo WHERE fmo.file_id = f.id AND fmo.deleted_at IS NULL) as media_count,
                   (SELECT COUNT(*) FROM comment_thread ct WHERE ct.file_id = f.id) as comment_count
            FROM file f
            JOIN project p ON p.id = f.project_id
            WHERE f.project_id = $1 AND f.deleted_at IS NULL
            ORDER BY f.modified_at DESC
            """,
            project_id,
        )

    async def get_file_summary(self, file_id: str) -> dict | None:
        return await self.fetchrow(
            """
            SELECT f.id, f.name, f.project_id, f.is_shared, f.revn, f.vern,
                   f.created_at, f.modified_at, f.version, f.features,
                   p.name as project_name, t.name as team_name,
                   (SELECT COUNT(*) FROM file_media_object fmo WHERE fmo.file_id = f.id AND fmo.deleted_at IS NULL) as media_count,
                   (SELECT COUNT(*) FROM comment_thread ct WHERE ct.file_id = f.id) as comment_count,
                   (SELECT COUNT(*) FROM file_library_rel flr WHERE flr.file_id = f.id) as library_count
            FROM file f
            JOIN project p ON p.id = f.project_id
            JOIN team t ON t.id = p.team_id
            WHERE f.id = $1 AND f.deleted_at IS NULL
            """,
            file_id,
        )

    async def search_files(self, query: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT f.id, f.name, f.project_id, p.name as project_name,
                   f.modified_at, f.is_shared
            FROM file f
            JOIN project p ON p.id = f.project_id
            WHERE f.deleted_at IS NULL AND f.name ILIKE $1
            ORDER BY f.modified_at DESC
            LIMIT 50
            """,
            f"%{query}%",
        )

    async def get_file_history(self, file_id: str, limit: int = 20) -> list[dict]:
        return await self.fetch(
            """
            SELECT fc.id, fc.revn, fc.created_at, fc.label, fc.created_by,
                   pr.fullname as profile_name, pr.email as profile_email
            FROM file_change fc
            LEFT JOIN profile pr ON pr.id = fc.profile_id
            WHERE fc.file_id = $1
            ORDER BY fc.revn DESC
            LIMIT $2
            """,
            file_id,
            limit,
        )

    async def get_file_libraries(self, file_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT flr.library_file_id, f.name as library_name, f.is_shared,
                   flr.synced_at, f.modified_at as library_modified_at
            FROM file_library_rel flr
            JOIN file f ON f.id = flr.library_file_id
            WHERE flr.file_id = $1 AND f.deleted_at IS NULL
            """,
            file_id,
        )

    # ── Comments ─────────────────────────────────────────────

    async def get_comments(
        self, file_id: str, resolved: bool | None = None
    ) -> list[dict]:
        base = """
            SELECT ct.id as thread_id, ct.page_name, ct.is_resolved,
                   ct.position, ct.seqn, ct.created_at as thread_created,
                   c.id as comment_id, c.content, c.created_at as comment_created,
                   pr.fullname as author, pr.email as author_email
            FROM comment_thread ct
            JOIN comment c ON c.thread_id = ct.id
            JOIN profile pr ON pr.id = c.owner_id
            WHERE ct.file_id = $1          """
        if resolved is not None:
            base += " AND ct.is_resolved = $2"
            base += " ORDER BY c.created_at DESC"
            return await self.fetch(base, file_id, resolved)
        base += " ORDER BY c.created_at DESC"
        return await self.fetch(base, file_id)

    # ── Media & Fonts ────────────────────────────────────────

    async def list_media_assets(self, file_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT fmo.id, fmo.name, fmo.width, fmo.height, fmo.mtype, fmo.is_local,
                   fmo.created_at
            FROM file_media_object fmo
            WHERE fmo.file_id = $1 AND fmo.deleted_at IS NULL
            ORDER BY fmo.created_at DESC
            """,
            file_id,
        )

    async def list_fonts(self, team_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT DISTINCT ON (tfv.font_id)
                   tfv.font_id, tfv.font_family, tfv.font_weight, tfv.font_style,
                   tfv.created_at
            FROM team_font_variant tfv
            WHERE tfv.team_id = $1 AND tfv.deleted_at IS NULL
            ORDER BY tfv.font_id, tfv.created_at DESC
            """,
            team_id,
        )

    # ── Teams & Profiles ─────────────────────────────────────

    async def list_teams(self) -> list[dict]:
        return await self.fetch(
            """
            SELECT t.id, t.name, t.is_default, t.features, t.created_at,
                   (SELECT COUNT(*) FROM team_profile_rel tpr WHERE tpr.team_id = t.id) as member_count,
                   (SELECT COUNT(*) FROM project p WHERE p.team_id = t.id AND p.deleted_at IS NULL) as project_count
            FROM team t
            WHERE t.deleted_at IS NULL
            ORDER BY t.created_at
            """
        )

    async def get_active_users(self, file_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT p.file_id, p.profile_id, p.updated_at,
                   pr.fullname, pr.email
            FROM presence p
            JOIN profile pr ON pr.id = p.profile_id
            WHERE p.file_id = $1
            ORDER BY p.updated_at DESC
            """,
            file_id,
        )

    async def get_share_links(self, file_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT sl.id, sl.pages, sl.flags, sl.who_comment, sl.who_inspect,
                   sl.created_at, pr.fullname as owner
            FROM share_link sl
            JOIN profile pr ON pr.id = sl.owner_id
            WHERE sl.file_id = $1 AND sl.deleted_at IS NULL
            """,
            file_id,
        )

    async def get_webhooks(self, team_id: str) -> list[dict]:
        return await self.fetch(
            """
            SELECT w.id, w.uri, w.mtype, w.is_active, w.error_code,
                   w.error_count, w.created_at
            FROM webhook w
            WHERE w.team_id = $1
            """,
            team_id,
        )

    # ── Penpot Multi-User Sessions & Per-User AI Profiles ────

    async def list_penpot_users(self, file_id: str | None = None) -> list[dict]:
        """List authenticated/active Penpot users from public.profile with session & presence status."""
        if not self._pool:
            return []
        cache_suffix = f"users:{file_id or 'all'}"
        cached = await self.cache.get_json("penpot_users", cache_suffix)
        if isinstance(cached, list):
            return cached
        rows = await self.fetch(
            """
            SELECT p.id::text AS id,
                   p.fullname,
                   p.email,
                   COALESCE(p.lang, 'es') AS lang,
                   COALESCE(p.theme, 'default') AS theme,
                   p.is_active,
                   p.default_team_id::text AS default_team_id,
                   p.default_project_id::text AS default_project_id,
                   p.created_at,
                   (SELECT COUNT(*) FROM http_session_v2 s WHERE s.profile_id = p.id) AS active_sessions,
                   (SELECT MAX(s.modified_at) FROM http_session_v2 s WHERE s.profile_id = p.id) AS last_session_at,
                   (SELECT COUNT(*) FROM presence pr WHERE pr.profile_id = p.id) AS active_presence_count,
                   EXISTS(SELECT 1 FROM public.mcp_user_ai_profile uai WHERE uai.profile_id = p.id) AS has_ai_profile
            FROM public.profile p
            WHERE p.deleted_at IS NULL AND p.is_blocked IS NOT TRUE
            ORDER BY last_session_at DESC NULLS LAST, p.created_at ASC
            """
        )
        await self.cache.set_json("penpot_users", cache_suffix, rows, ttl_seconds=60)
        return rows

    async def resolve_authenticated_penpot_user(
        self,
        *,
        user_id: str | None = None,
        email: str | None = None,
        session_id: str | None = None,
        file_id: str | None = None,
    ) -> dict | None:
        """Resolve a Penpot user by explicit user_id/email, session_id in http_session_v2, presence on file_id, or latest session."""
        if not self._pool:
            return None

        if session_id:
            try:
                row = await self.fetchrow(
                    """
                    SELECT p.id::text AS id, p.fullname, p.email, COALESCE(p.lang, 'es') AS lang,
                           COALESCE(p.theme, 'default') AS theme, p.default_team_id::text AS default_team_id,
                           p.default_project_id::text AS default_project_id,
                           'http_session_v2' AS resolved_via
                    FROM public.http_session_v2 s
                    JOIN public.profile p ON p.id = s.profile_id
                    WHERE s.id::text = $1 AND p.deleted_at IS NULL
                    LIMIT 1
                    """,
                    session_id.strip(),
                )
                if row:
                    return row
            except Exception:
                pass

        if user_id:
            try:
                row = await self.fetchrow(
                    """
                    SELECT p.id::text AS id, p.fullname, p.email, COALESCE(p.lang, 'es') AS lang,
                           COALESCE(p.theme, 'default') AS theme, p.default_team_id::text AS default_team_id,
                           p.default_project_id::text AS default_project_id,
                           'profile_id' AS resolved_via
                    FROM public.profile p
                    WHERE p.id::text = $1 AND p.deleted_at IS NULL
                    LIMIT 1
                    """,
                    user_id.strip(),
                )
                if row:
                    return row
            except Exception:
                pass

        if email:
            try:
                row = await self.fetchrow(
                    """
                    SELECT p.id::text AS id, p.fullname, p.email, COALESCE(p.lang, 'es') AS lang,
                           COALESCE(p.theme, 'default') AS theme, p.default_team_id::text AS default_team_id,
                           p.default_project_id::text AS default_project_id,
                           'email' AS resolved_via
                    FROM public.profile p
                    WHERE LOWER(p.email) = LOWER($1) AND p.deleted_at IS NULL
                    LIMIT 1
                    """,
                    email.strip(),
                )
                if row:
                    return row
            except Exception:
                pass

        if file_id:
            try:
                row = await self.fetchrow(
                    """
                    SELECT p.id::text AS id, p.fullname, p.email, COALESCE(p.lang, 'es') AS lang,
                           COALESCE(p.theme, 'default') AS theme, p.default_team_id::text AS default_team_id,
                           p.default_project_id::text AS default_project_id,
                           'canvas_presence' AS resolved_via
                    FROM public.presence pr
                    JOIN public.profile p ON p.id = pr.profile_id
                    WHERE pr.file_id::text = $1 AND p.deleted_at IS NULL
                    ORDER BY pr.updated_at DESC
                    LIMIT 1
                    """,
                    file_id.strip(),
                )
                if row:
                    return row
            except Exception:
                pass

        try:
            row = await self.fetchrow(
                """
                SELECT p.id::text AS id, p.fullname, p.email, COALESCE(p.lang, 'es') AS lang,
                       COALESCE(p.theme, 'default') AS theme, p.default_team_id::text AS default_team_id,
                       p.default_project_id::text AS default_project_id,
                       'latest_session' AS resolved_via
                FROM public.profile p
                LEFT JOIN public.http_session_v2 s ON s.profile_id = p.id
                WHERE p.deleted_at IS NULL AND p.is_active IS TRUE
                ORDER BY s.modified_at DESC NULLS LAST, p.created_at ASC
                LIMIT 1
                """
            )
            return row
        except Exception:
            return None

    async def get_user_ai_state(self, profile_id: str) -> dict | None:
        """Load per-user AI profile, credentials, and instructions from public.mcp_user_ai_profile."""
        if not self._pool or not profile_id:
            return None
        try:
            row = await self.fetchrow(
                """
                SELECT profile_id::text AS profile_id,
                       email,
                       fullname,
                       active_profile,
                       credentials,
                       agent_instructions,
                       last_file_id::text AS last_file_id,
                       last_page_id::text AS last_page_id,
                       updated_at
                FROM public.mcp_user_ai_profile
                WHERE profile_id::text = $1
                """,
                profile_id.strip(),
            )
            if not row:
                return None
            import json as _json

            for json_col in ("active_profile", "credentials", "agent_instructions"):
                val = row.get(json_col)
                if isinstance(val, str):
                    try:
                        row[json_col] = _json.loads(val)
                    except Exception:
                        row[json_col] = {}
            return row
        except Exception as exc:
            logger.debug("get_user_ai_state warning: %s", exc)
            return None

    async def save_user_ai_state(
        self,
        *,
        profile_id: str,
        email: str,
        fullname: str,
        active_profile: dict,
        credentials: dict,
        agent_instructions: dict,
        last_file_id: str | None = None,
        last_page_id: str | None = None,
    ) -> bool:
        """Persist per-user AI profile, credentials, and instructions into public.mcp_user_ai_profile."""
        if not self._pool or not profile_id:
            return False
        import json as _json
        import uuid as _uuid

        try:
            uid = _uuid.UUID(str(profile_id).strip())
            fid = _uuid.UUID(str(last_file_id).strip()) if last_file_id else None
            pid = _uuid.UUID(str(last_page_id).strip()) if last_page_id else None
            async with self.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO public.mcp_user_ai_profile (
                        profile_id, email, fullname, active_profile, credentials,
                        agent_instructions, last_file_id, last_page_id, updated_at
                    )
                    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8, clock_timestamp())
                    ON CONFLICT (profile_id) DO UPDATE SET
                        email = EXCLUDED.email,
                        fullname = EXCLUDED.fullname,
                        active_profile = EXCLUDED.active_profile,
                        credentials = EXCLUDED.credentials,
                        agent_instructions = EXCLUDED.agent_instructions,
                        last_file_id = COALESCE(EXCLUDED.last_file_id, public.mcp_user_ai_profile.last_file_id),
                        last_page_id = COALESCE(EXCLUDED.last_page_id, public.mcp_user_ai_profile.last_page_id),
                        updated_at = clock_timestamp()
                    """,
                    uid,
                    email or "",
                    fullname or "",
                    _json.dumps(active_profile or {}),
                    _json.dumps(credentials or {}),
                    _json.dumps(agent_instructions or {}),
                    fid,
                    pid,
                )
            await self.cache.bump_namespace("penpot_users")
            return True
        except Exception as exc:
            logger.debug("save_user_ai_state warning: %s", exc)
            return False

    # ── pgvector Canvas Vision Index ─────────────────────────

    async def get_pgvector_status(self) -> dict[str, Any]:
        """Return installed PostgreSQL version, pgvector/pg_trgm versions, and indexed shape count."""
        if not self._pool:
            return {"connected": False, "pgvector_installed": False}
        try:
            ext_rows = await self.fetch(
                "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector', 'pg_trgm') ORDER BY extname"
            )
            exts = {r["extname"]: r["extversion"] for r in ext_rows}
            indexed_count = await self.fetchval("SELECT COUNT(*) FROM public.mcp_canvas_vision_index")
            users_with_ai = await self.fetchval("SELECT COUNT(*) FROM public.mcp_user_ai_profile")
            return {
                "connected": True,
                "pgvector_installed": "vector" in exts,
                "pgvector_version": exts.get("vector"),
                "pg_trgm_version": exts.get("pg_trgm"),
                "indexed_shapes_count": int(indexed_count or 0),
                "users_with_ai_profiles": int(users_with_ai or 0),
            }
        except Exception as exc:
            return {"connected": True, "pgvector_installed": False, "error": str(exc)}

    async def index_page_vision_rows(
        self, file_id: str, page_id: str, rows: list[dict[str, Any]]
    ) -> int:
        """Synchronize live page shapes into public.mcp_canvas_vision_index with vector(64) embeddings."""
        if not self._pool:
            return 0
        import uuid as _uuid

        fid = _uuid.UUID(str(file_id).strip())
        pid = _uuid.UUID(str(page_id).strip())
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.mcp_canvas_vision_index WHERE file_id = $1 AND page_id = $2",
                    fid,
                    pid,
                )
                for r in rows:
                    try:
                        sid = _uuid.UUID(str(r["shape_id"]).strip())
                    except Exception:
                        continue
                    parent_uuid = None
                    if r.get("parent_id"):
                        try:
                            parent_uuid = _uuid.UUID(str(r["parent_id"]).strip())
                        except Exception:
                            parent_uuid = None
                    await conn.execute(
                        """
                        INSERT INTO public.mcp_canvas_vision_index (
                            file_id, page_id, shape_id, shape_name, shape_type,
                            parent_id, frame_name, x, y, width, height,
                            fill_color, text_content, semantic_summary, embedding, updated_at
                        )
                        VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15::vector(64), clock_timestamp()
                        )
                        """,
                        fid,
                        pid,
                        sid,
                        str(r.get("shape_name") or ""),
                        str(r.get("shape_type") or "rect"),
                        parent_uuid,
                        str(r.get("frame_name") or ""),
                        float(r.get("x") or 0.0),
                        float(r.get("y") or 0.0),
                        float(r.get("width") or 0.0),
                        float(r.get("height") or 0.0),
                        str(r.get("fill_color") or ""),
                        str(r.get("text_content") or ""),
                        str(r.get("semantic_summary") or ""),
                        str(r.get("embedding")),
                    )
        await self.cache.bump_namespace("vision")
        return len(rows)

    async def search_page_vision_pgvector(
        self,
        *,
        file_id: str,
        page_id: str,
        query_vector_literal: str,
        query_text: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Query public.mcp_canvas_vision_index using pgvector cosine distance (<=>) + pg_trgm similarity."""
        if not self._pool:
            return []
        import uuid as _uuid

        fid = _uuid.UUID(str(file_id).strip())
        pid = _uuid.UUID(str(page_id).strip())
        clean_q = (query_text or "").strip()
        cache_suffix = f"q:{fid}:{pid}:{clean_q}:{max(1, int(top_k))}"
        cached = await self.cache.get_json("vision", cache_suffix)
        if isinstance(cached, list):
            return cached
        rows = await self.fetch(
            """
            SELECT shape_id::text AS shape_id,
                   shape_name,
                   shape_type,
                   frame_name,
                   ROUND(x::numeric, 1)::float AS x,
                   ROUND(y::numeric, 1)::float AS y,
                   ROUND(width::numeric, 1)::float AS width,
                   ROUND(height::numeric, 1)::float AS height,
                   fill_color,
                   text_content,
                   semantic_summary,
                   ROUND(
                       (
                           (1.0 - (embedding <=> $3::vector(64))) * 0.78
                           + COALESCE(similarity(semantic_summary, $4), 0.0) * 0.22
                       )::numeric,
                       4
                   )::float AS similarity
            FROM public.mcp_canvas_vision_index
            WHERE file_id = $1 AND page_id = $2
            ORDER BY similarity DESC, y ASC, x ASC
            LIMIT $5
            """,
            fid,
            pid,
            query_vector_literal,
            clean_q,
            max(1, int(top_k)),
        )
        await self.cache.set_json("vision", cache_suffix, rows)
        return rows

    async def list_team_members(self, team_id: str) -> list[dict]:
        """List members and roles for a team from team_profile_rel + profile."""
        if not self._pool:
            return []
        return await self.fetch(
            """
            SELECT p.id::text AS id,
                   p.fullname,
                   p.email,
                   tpr.is_owner,
                   tpr.is_admin,
                   tpr.can_edit,
                   tpr.created_at
            FROM public.team_profile_rel tpr
            JOIN public.profile p ON p.id = tpr.profile_id
            WHERE tpr.team_id::text = $1 AND p.deleted_at IS NULL
            ORDER BY tpr.is_owner DESC, tpr.is_admin DESC, p.fullname ASC
            """,
            team_id.strip(),
        )

    async def list_team_fonts(self, team_id: str) -> list[dict]:
        """List custom fonts registered for a team from team_font_variant."""
        if not self._pool:
            return []
        return await self.fetch(
            """
            SELECT id::text AS id,
                   font_id::text AS font_id,
                   font_family,
                   font_weight,
                   font_style,
                   woff1_file_id::text AS woff1_file_id,
                   woff2_file_id::text AS woff2_file_id,
                   created_at
            FROM public.team_font_variant
            WHERE team_id::text = $1
            ORDER BY font_family ASC, font_weight ASC
            """,
            team_id.strip(),
        )

    async def list_file_share_links(self, file_id: str) -> list[dict]:
        """List active share links for a file from share_link."""
        if not self._pool:
            return []
        return await self.fetch(
            """
            SELECT id::text AS id,
                   who_comment,
                   who_inspect,
                   flags,
                   pages::text AS pages,
                   created_at
            FROM public.share_link
            WHERE file_id::text = $1
            ORDER BY created_at DESC
            """,
            file_id.strip(),
        )

    async def list_file_presence(self, file_id: str) -> list[dict]:
        """List live designers currently present in a file from presence + profile."""
        if not self._pool:
            return []
        return await self.fetch(
            """
            SELECT pr.id::text AS session_id,
                   p.id::text AS profile_id,
                   p.fullname,
                   p.email,
                   pr.updated_at
            FROM public.presence pr
            JOIN public.profile p ON p.id = pr.profile_id
            WHERE pr.file_id::text = $1
            ORDER BY pr.updated_at DESC
            """,
            file_id.strip(),
        )

    async def list_audit_logs(self, limit: int = 25) -> list[dict]:
        """List recent Penpot audit_log events."""
        if not self._pool:
            return []
        return await self.fetch(
            """
            SELECT id::text AS id,
                   name,
                   type,
                   profile_id::text AS profile_id,
                   created_at
            FROM public.audit_log
            ORDER BY created_at DESC
            LIMIT $1
            """,
            max(1, min(int(limit), 100)),
        )


# Singleton
db = PenpotDB()
