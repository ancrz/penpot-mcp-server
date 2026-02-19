"""Comment and collaboration tools."""

from __future__ import annotations

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


async def get_comments(
    file_id: str, resolved: bool | None = None
) -> list[dict]:
    """Get all comments on a file, grouped by thread.

    Args:
        file_id: The file UUID.
        resolved: Filter by resolution status. True=resolved only, False=unresolved only, None=all.
    """
    rows = await db.get_comments(file_id, resolved)
    return [
        {
            "thread_id": str(r["thread_id"]),
            "page_name": r["page_name"],
            "is_resolved": r["is_resolved"],
            "position": r.get("position"),
            "comment_id": str(r["comment_id"]),
            "content": r["content"],
            "author": r["author"],
            "author_email": r["author_email"],
            "thread_created": (
                r["thread_created"].isoformat() if r["thread_created"] else None
            ),
            "comment_created": (
                r["comment_created"].isoformat() if r["comment_created"] else None
            ),
        }
        for r in rows
    ]


async def get_active_users(file_id: str) -> list[dict]:
    """Get users currently present in a file (real-time collaboration).

    Args:
        file_id: The file UUID.
    """
    rows = await db.get_active_users(file_id)
    return [
        {
            "profile_id": str(r["profile_id"]),
            "fullname": r["fullname"],
            "email": r["email"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


async def get_share_links(file_id: str) -> list[dict]:
    """List share links for a file.

    Args:
        file_id: The file UUID.
    """
    rows = await db.get_share_links(file_id)
    return [
        {
            "id": str(r["id"]),
            "pages": r.get("pages"),
            "flags": r.get("flags"),
            "who_comment": r.get("who_comment"),
            "who_inspect": r.get("who_inspect"),
            "owner": r.get("owner"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def create_comment(
    file_id: str,
    page_id: str,
    content: str,
    x: float,
    y: float,
    frame_id: str | None = None,
) -> dict:
    """Create a new comment thread on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID where the comment is placed.
        content: Comment text.
        x: X position on the page.
        y: Y position on the page.
        frame_id: Optional frame UUID the comment is attached to.
    """
    return await api.create_comment_thread(
        file_id=file_id,
        page_id=page_id,
        position={"x": x, "y": y},
        content=content,
        frame_id=frame_id,
    )


async def reply_to_comment(thread_id: str, content: str) -> dict:
    """Reply to an existing comment thread.

    Args:
        thread_id: The comment thread UUID.
        content: Reply text.
    """
    return await api.create_comment(thread_id, content)


async def resolve_comment(thread_id: str, resolved: bool = True) -> dict:
    """Resolve or unresolve a comment thread.

    Args:
        thread_id: The comment thread UUID.
        resolved: True to resolve, False to unresolve.
    """
    return await api.update_comment_thread(thread_id, resolved)
