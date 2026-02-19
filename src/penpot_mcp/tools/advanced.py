"""Advanced analysis tools — revision comparison, raw data inspection."""

from __future__ import annotations

from typing import Any

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


async def get_file_raw_data(file_id: str, page_id: str | None = None) -> dict:
    """Get decoded file data structure for inspection.

    Returns the file's internal data (pages, components, colors, typographies)
    after full transit decoding. Useful for debugging and advanced automation.

    Args:
        file_id: The file UUID.
        page_id: Optional — if provided, returns only that page's data.
    """
    file_data = await api.command("get-file", {"id": file_id, "components-v2": True})

    data = file_data.get("data", {})
    result: dict[str, Any] = {
        "file_id": file_id,
        "file_name": file_data.get("name"),
    }

    if page_id:
        # Return just one page's data
        page = data.get("pages-index", {}).get(page_id, {})
        if not page:
            return {"error": f"Page {page_id} not found in file {file_id}"}
        objects = page.get("objects", {})
        result["page_id"] = page_id
        result["page_name"] = page.get("name")
        result["object_count"] = len(objects)
        # Return first-level structure of each object (IDs + types)
        result["objects_summary"] = {
            oid: {
                "name": _safe_get(obj, "name"),
                "type": _safe_get(obj, "type"),
                "parent-id": _safe_get(obj, "parent-id"),
            }
            for oid, obj in objects.items()
            if isinstance(obj, dict)
        }
    else:
        # Return file-level summary
        pages_index = data.get("pages-index", {})
        result["pages"] = [
            {
                "id": pid,
                "name": p.get("name") if isinstance(p, dict) else None,
                "object_count": len(p.get("objects", {})) if isinstance(p, dict) else 0,
            }
            for pid, p in pages_index.items()
        ]
        # Components
        components = data.get("components", {})
        result["component_count"] = len(components)
        result["components"] = [
            {
                "id": cid,
                "name": c.get("name") if isinstance(c, dict) else None,
                "path": c.get("path") if isinstance(c, dict) else None,
            }
            for cid, c in list(components.items())[:50]
        ]
        # Colors
        colors = data.get("colors", {})
        result["color_count"] = len(colors)
        # Typographies
        typographies = data.get("typographies", {})
        result["typography_count"] = len(typographies)

    return result


async def compare_revisions(
    file_id: str,
    revn_from: int,
    revn_to: int | None = None,
) -> dict:
    """Compare two revisions of a file to see what changed.

    Shows revision metadata and summarizes change operations by scanning
    the Fressian-encoded binary for known operation type strings.

    Args:
        file_id: The file UUID.
        revn_from: Starting revision number.
        revn_to: Ending revision number (default: latest).
    """
    file_info_row = await db.fetchrow(
        "SELECT revn, name FROM file WHERE id = $1",
        file_id,
    )
    if not file_info_row:
        return {"error": f"File {file_id} not found"}

    latest_revn = file_info_row["revn"]
    if revn_to is None:
        revn_to = latest_revn

    if revn_from > revn_to:
        revn_from, revn_to = revn_to, revn_from

    rows = await db.fetch(
        """
        SELECT fc.revn, fc.created_at, fc.label, fc.created_by,
               pr.fullname as author, pr.email as author_email,
               length(fc.changes) as changes_bytes,
               fc.changes
        FROM file_change fc
        LEFT JOIN profile pr ON pr.id = fc.profile_id
        WHERE fc.file_id = $1 AND fc.revn > $2 AND fc.revn <= $3
        ORDER BY fc.revn ASC
        """,
        file_id,
        revn_from,
        revn_to,
    )

    changes_summary = []
    for row in rows:
        change_entry = {
            "revn": row["revn"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "author": row.get("author"),
            "created_by": row.get("created_by"),
            "label": row.get("label"),
            "changes_bytes": row.get("changes_bytes", 0),
        }
        # Extract operation types from Fressian binary by scanning for known strings
        raw = row.get("changes")
        if isinstance(raw, (bytes, bytearray)):
            change_entry["operation_types"] = _extract_op_types_from_fressian(raw)
        changes_summary.append(change_entry)

    return {
        "file_id": file_id,
        "file_name": file_info_row["name"],
        "revn_from": revn_from,
        "revn_to": revn_to,
        "latest_revn": latest_revn,
        "change_count": len(changes_summary),
        "changes": changes_summary,
    }


# Known Penpot change operation types to scan for in Fressian binary
_KNOWN_OPS = (
    b"add-obj", b"mod-obj", b"del-obj", b"mov-objects",
    b"add-page", b"del-page", b"mod-page",
    b"add-component", b"mod-component", b"del-component",
    b"add-color", b"mod-color", b"del-color",
    b"add-typography", b"mod-typography", b"del-typography",
    b"add-media", b"mod-media", b"del-media",
    b"reg-objects", b"set-option",
)


def _extract_op_types_from_fressian(data: bytes) -> list[str]:
    """Scan Fressian binary for known Penpot operation type strings."""
    found = []
    for op in _KNOWN_OPS:
        count = data.count(op)
        if count > 0:
            found.extend([op.decode("ascii")] * count)
    return found


def _safe_get(obj: Any, key: str) -> Any:
    """Safely get a key from a potentially tagged transit value."""
    if isinstance(obj, dict):
        return obj.get(key)
    if isinstance(obj, list) and len(obj) == 2 and isinstance(obj[1], dict):
        return obj[1].get(key)
    return None
