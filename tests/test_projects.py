"""Integration tests for project/team/file listing tools."""

from __future__ import annotations

import json

import pytest

from penpot_mcp.server import (
    list_teams,
    list_projects,
    list_files,
    search_files,
    get_profile,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_list_teams():
    result = json.loads(await list_teams())
    assert isinstance(result, list)
    if result:
        assert "id" in result[0]
        assert "name" in result[0]
        assert "member_count" in result[0]


@pytest.mark.asyncio
async def test_list_projects():
    result = json.loads(await list_projects())
    assert isinstance(result, list)
    if result:
        assert "id" in result[0]
        assert "name" in result[0]
        assert "team_id" in result[0]


@pytest.mark.asyncio
async def test_list_projects_by_team():
    teams = json.loads(await list_teams())
    if not teams:
        pytest.skip("No teams found")

    team_id = teams[0]["id"]
    result = json.loads(await list_projects(team_id))
    assert isinstance(result, list)
    for proj in result:
        assert proj["team_id"] == team_id


@pytest.mark.asyncio
async def test_list_files():
    projects = json.loads(await list_projects())
    if not projects:
        pytest.skip("No projects found")

    result = json.loads(await list_files(projects[0]["id"]))
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_search_files():
    result = json.loads(await search_files("test"))
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_profile():
    result = json.loads(await get_profile())
    assert isinstance(result, dict)
    assert "id" in result or "email" in result
