"""Project and team listing tools."""

from __future__ import annotations

import json
from typing import Any

from penpot_mcp.services.db import db


async def list_teams() -> list[dict]:
    """List all teams in the Penpot instance.

    Returns team name, member count, project count, and features.
    """
    rows = await db.list_teams()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "is_default": r["is_default"],
            "member_count": r["member_count"],
            "project_count": r["project_count"],
            "features": r.get("features"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def list_projects(team_id: str | None = None) -> list[dict]:
    """List projects, optionally filtered by team.

    Args:
        team_id: Filter projects by team ID. If omitted, returns all projects.
    """
    rows = await db.list_projects(team_id)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "team_id": str(r["team_id"]),
            "team_name": r["team_name"],
            "is_default": r["is_default"],
            "file_count": r["file_count"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
        }
        for r in rows
    ]


async def list_files(project_id: str) -> list[dict]:
    """List all files in a project.

    Args:
        project_id: The project UUID to list files from.
    """
    rows = await db.list_files(project_id)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "project_id": str(r["project_id"]),
            "project_name": r["project_name"],
            "is_shared": r["is_shared"],
            "revn": r["revn"],
            "media_count": r["media_count"],
            "comment_count": r["comment_count"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
        }
        for r in rows
    ]


async def search_files(query: str) -> list[dict]:
    """Search files by name across all projects.

    Args:
        query: Search term (case-insensitive partial match).
    """
    rows = await db.search_files(query)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "project_id": str(r["project_id"]),
            "project_name": r["project_name"],
            "is_shared": r["is_shared"],
            "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
        }
        for r in rows
    ]
