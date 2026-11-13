"""Test fixtures for Penpot MCP server."""

from __future__ import annotations

import pytest
import pytest_asyncio

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def setup_services(request: pytest.FixtureRequest):
    """Connect to Penpot DB and API once when integration tests are selected."""
    if not any(item.get_closest_marker("integration") for item in request.session.items):
        yield
        return

    try:
        await db.connect()
    except Exception as e:
        print(f"Skipping DB connection: {e}")
    try:
        await api.connect()
    except Exception as e:
        print(f"Skipping API connection: {e}")
    yield
    try:
        await api.close()
    except Exception:
        pass
    try:
        await db.close()
    except Exception:
        pass
