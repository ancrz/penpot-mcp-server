"""File detail, history, and page tools."""

from __future__ import annotations


from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


async def get_file_summary(file_id: str) -> dict | None:
    """Get detailed metadata for a file including counts and library info.

    Args:
        file_id: The file UUID.
    """
    r = await db.get_file_summary(file_id)
    if not r:
        return {"error": f"File {file_id} not found"}
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "project_id": str(r["project_id"]),
        "project_name": r["project_name"],
        "team_name": r["team_name"],
        "is_shared": r["is_shared"],
        "revn": r["revn"],
        "vern": r["vern"],
        "version": r["version"],
        "features": r.get("features"),
        "media_count": r["media_count"],
        "comment_count": r["comment_count"],
        "library_count": r["library_count"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
    }


async def get_file_pages(file_id: str) -> list[dict]:
    """Get all pages in a file with their shape counts.

    Uses the RPC API to retrieve the file data structure
    and extract page information.

    Args:
        file_id: The file UUID.
    """
    file_data = await api.command(
        "get-file",
        {"id": file_id, "components-v2": True},
    )
    if not isinstance(file_data, dict):
        return [{"error": "Could not parse file data"}]

    pages = []
    pages_index = file_data.get("data", {}).get("pages-index", {})
    page_order = file_data.get("data", {}).get("pages", [])

    for idx, page_id in enumerate(page_order):
        page = pages_index.get(page_id, {})
        objects = page.get("objects", {})
        pages.append(
            {
                "id": page_id,
                "name": page.get("name", f"Page {idx + 1}"),
                "index": idx,
                "object_count": len(objects),
            }
        )
    return pages


async def get_file_history(file_id: str, limit: int = 20) -> list[dict]:
    """Get the revision history of a file.

    Args:
        file_id: The file UUID.
        limit: Maximum number of history entries to return (default 20).
    """
    rows = await db.get_file_history(file_id, limit)
    return [
        {
            "id": str(r["id"]),
            "revn": r["revn"],
            "label": r.get("label"),
            "profile_name": r.get("profile_name"),
            "profile_email": r.get("profile_email"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def get_file_libraries(file_id: str) -> list[dict]:
    """List shared libraries linked to a file.

    Args:
        file_id: The file UUID.
    """
    rows = await db.get_file_libraries(file_id)
    return [
        {
            "library_file_id": str(r["library_file_id"]),
            "library_name": r["library_name"],
            "is_shared": r["is_shared"],
            "synced_at": r["synced_at"].isoformat() if r["synced_at"] else None,
            "library_modified_at": (
                r["library_modified_at"].isoformat()
                if r["library_modified_at"]
                else None
            ),
        }
        for r in rows
    ]


async def create_project(team_id: str, name: str) -> dict:
    """Create a new project in a team.

    Args:
        team_id: The team UUID where the project will be created.
        name: Name for the new project.
    """
    return await api.create_project(team_id, name)


async def create_file(project_id: str, name: str) -> dict:
    """Create a new file in a project.

    Args:
        project_id: The project UUID where the file will be created.
        name: Name for the new file.
    """
    return await api.create_file(project_id, name)


async def rename_file(file_id: str, name: str) -> dict:
    """Rename an existing file.

    Args:
        file_id: The file UUID to rename.
        name: New name for the file.
    """
    return await api.rename_file(file_id, name)


async def duplicate_file(file_id: str, name: str | None = None) -> dict:
    """Duplicate an existing file.

    Args:
        file_id: The file UUID to duplicate.
        name: Optional name for the copy. If omitted, Penpot generates one.
    """
    return await api.duplicate_file(file_id, name)


async def delete_file(file_id: str) -> dict:
    """Delete a file (soft delete — moves to trash).

    Args:
        file_id: The file UUID to delete.
    """
    return await api.delete_file(file_id)
