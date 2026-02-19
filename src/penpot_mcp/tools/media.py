"""Media asset and font tools."""

from __future__ import annotations

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


async def list_media_assets(file_id: str) -> list[dict]:
    """List all media assets (images) in a file.

    Args:
        file_id: The file UUID.
    """
    rows = await db.list_media_assets(file_id)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "width": r["width"],
            "height": r["height"],
            "mtype": r["mtype"],
            "is_local": r["is_local"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def list_fonts(team_id: str) -> list[dict]:
    """List custom fonts uploaded to a team.

    Args:
        team_id: The team UUID.
    """
    rows = await db.list_fonts(team_id)
    return [
        {
            "font_id": str(r["font_id"]),
            "font_family": r["font_family"],
            "font_weight": r["font_weight"],
            "font_style": r["font_style"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def upload_media(file_id: str, name: str, url: str) -> dict:
    """Upload an image to a file from a URL.

    Args:
        file_id: The file UUID.
        name: Name for the media asset.
        url: Public URL of the image to upload.
    """
    return await api.upload_media(file_id, name, url)
