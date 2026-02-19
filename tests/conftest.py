"""Test fixtures for Penpot MCP server."""

from __future__ import annotations

import pytest
import pytest_asyncio

from penpot_mcp.services.api import api
from penpot_mcp.services.db import db


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def setup_services():
    """Connect to Penpot DB and API once for the entire test session."""
    await db.connect()
    await api.connect()
    yield
    await api.close()
    await db.close()
