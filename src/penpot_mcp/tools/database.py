"""Direct database query tool for advanced Penpot exploration."""

from __future__ import annotations

import re

from penpot_mcp.services.db import db

_SQL_COMMENT_RE = re.compile(r"(--[^\n]*|/\*[\s\S]*?\*/)")
_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "copy",
    "call",
    "do",
)
_FORBIDDEN_IDENTIFIERS = (
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "lo_unlink",
    "lo_from_bytea",
    "lo_put",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "set_config",
    "setval",
    "nextval",
    "dblink",
    "http_session",
    "access_token",
    "secret_key",
    "password",
)


async def query_database(sql: str) -> list[dict]:
    """Execute a read-only SQL query against the Penpot database.

    This is a power tool for advanced queries not covered by other tools.
    Only SELECT statements are allowed, executed inside a read-only transaction
    with a statement timeout.

    Args:
        sql: SQL SELECT query to execute.
    """
    cleaned = _SQL_COMMENT_RE.sub(" ", sql).strip().rstrip(";").strip()
    if ";" in cleaned:
        return [{"error": "Multiple SQL statements are not allowed"}]

    normalized = cleaned.lower()
    if not normalized.startswith("select"):
        return [{"error": "Only SELECT queries are allowed for safety"}]

    tokens = re.findall(r"[a-z_][a-z0-9_]*", normalized)
    token_set = set(tokens)

    for word in _FORBIDDEN_KEYWORDS:
        if word in token_set:
            return [{"error": f"Forbidden SQL keyword: {word}"}]

    for ident in _FORBIDDEN_IDENTIFIERS:
        if ident in token_set:
            return [{"error": f"Forbidden SQL function or sensitive identifier: {ident}"}]

    rows = await db.fetch_readonly(cleaned)
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            elif isinstance(v, bytes):
                clean[k] = f"<binary {len(v)} bytes>"
            elif isinstance(v, (dict, list, str, int, float, bool)) or v is None:
                clean[k] = v
            else:
                clean[k] = str(v)
        result.append(clean)
    return result


async def get_webhooks(team_id: str) -> list[dict]:
    """List webhooks configured for a team.

    Args:
        team_id: The team UUID.
    """
    rows = await db.get_webhooks(team_id)
    return [
        {
            "id": str(r["id"]),
            "uri": r["uri"],
            "mtype": r["mtype"],
            "is_active": r["is_active"],
            "error_code": r.get("error_code"),
            "error_count": r.get("error_count"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
