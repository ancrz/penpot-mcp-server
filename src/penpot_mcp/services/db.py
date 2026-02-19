"""Direct PostgreSQL access to Penpot database."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from penpot_mcp.config import settings


class PenpotDB:
    """Async PostgreSQL client for direct Penpot database queries."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            host=settings.penpot_db_host,
            port=settings.penpot_db_port,
            database=settings.penpot_db_name,
            user=settings.penpot_db_user,
            password=settings.penpot_db_pass,
            min_size=2,
            max_size=10,
        )

    async def close(self) -> None:
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


# Singleton
db = PenpotDB()
