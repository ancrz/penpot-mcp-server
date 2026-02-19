"""Integration tests for file detail tools."""

from __future__ import annotations

import json

import pytest

from penpot_mcp.server import (
    list_projects,
    list_files,
    get_file_summary,
    get_file_pages,
    get_file_history,
    query_database,
)

pytestmark = pytest.mark.integration


async def _get_first_file_id() -> str | None:
    """Helper to find any file in the instance."""
    projects = json.loads(await list_projects())
    for proj in projects:
        files = json.loads(await list_files(proj["id"]))
        if files:
            return files[0]["id"]
    return None


@pytest.mark.asyncio
async def test_get_file_summary():
    file_id = await _get_first_file_id()
    if not file_id:
        pytest.skip("No files found")

    result = json.loads(await get_file_summary(file_id))
    assert "id" in result
    assert "name" in result
    assert "revn" in result


@pytest.mark.asyncio
async def test_get_file_pages():
    file_id = await _get_first_file_id()
    if not file_id:
        pytest.skip("No files found")

    result = json.loads(await get_file_pages(file_id))
    assert isinstance(result, list)
    if result:
        assert "id" in result[0]
        assert "name" in result[0]
        assert "object_count" in result[0]


@pytest.mark.asyncio
async def test_get_file_history():
    file_id = await _get_first_file_id()
    if not file_id:
        pytest.skip("No files found")

    result = json.loads(await get_file_history(file_id, 5))
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_query_database_select():
    result = json.loads(
        await query_database("SELECT id, name FROM team WHERE deleted_at IS NULL LIMIT 5")
    )
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_query_database_rejects_insert():
    result = json.loads(
        await query_database("INSERT INTO team (name) VALUES ('hacked')")
    )
    assert result[0].get("error")
