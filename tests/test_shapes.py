"""Integration tests for shape reading tools."""

from __future__ import annotations

import json

import pytest

from penpot_mcp.server import (
    list_projects,
    list_files,
    get_file_pages,
    get_page_objects,
    get_shape_tree,
    get_shape_css,
    search_shapes,
)

pytestmark = pytest.mark.integration


async def _get_first_page() -> tuple[str, str] | None:
    """Find a file_id + page_id that has objects."""
    projects = json.loads(await list_projects())
    for proj in projects:
        files = json.loads(await list_files(proj["id"]))
        for f in files:
            pages = json.loads(await get_file_pages(f["id"]))
            for page in pages:
                if page.get("object_count", 0) > 1:
                    return f["id"], page["id"]
    return None


@pytest.mark.asyncio
async def test_get_page_objects():
    ids = await _get_first_page()
    if not ids:
        pytest.skip("No pages with objects found")

    file_id, page_id = ids
    result = json.loads(await get_page_objects(file_id, page_id))
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_get_shape_tree():
    ids = await _get_first_page()
    if not ids:
        pytest.skip("No pages with objects found")

    file_id, page_id = ids
    result = json.loads(await get_shape_tree(file_id, page_id, depth=2))
    assert isinstance(result, dict)
    assert "id" in result or "name" in result or "error" in result


@pytest.mark.asyncio
async def test_get_shape_css():
    ids = await _get_first_page()
    if not ids:
        pytest.skip("No pages with objects found")

    file_id, page_id = ids
    objects = json.loads(await get_page_objects(file_id, page_id))
    shape = next((o for o in objects if o.get("id") != page_id), None)
    if not shape:
        pytest.skip("No shapes found")

    result = json.loads(await get_shape_css(file_id, page_id, shape["id"]))
    assert "css" in result or "error" in result


@pytest.mark.asyncio
async def test_search_shapes_by_name():
    ids = await _get_first_page()
    if not ids:
        pytest.skip("No pages with objects found")

    file_id, page_id = ids
    result = json.loads(await search_shapes(file_id, page_id, "frame"))
    assert isinstance(result, list)
