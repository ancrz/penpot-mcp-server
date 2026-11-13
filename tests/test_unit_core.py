"""Offline unit and security regression tests for core Penpot MCP components."""

from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from penpot_mcp.config import Settings, settings
from penpot_mcp.server import mcp, plugin_config
from penpot_mcp.services.api import PenpotAPI, _encode_transit_value
from penpot_mcp.services.db import PenpotDB
from penpot_mcp.services.transit import decode_transit
from penpot_mcp.tools.database import query_database
from penpot_mcp.transformers.css import shape_to_css
from penpot_mcp.ws_controller import PenpotWSController


def _parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


def test_env_examples_cover_all_settings() -> None:
    root = Path(__file__).resolve().parent.parent
    expected = {name.upper() for name in Settings.model_fields}
    for template_name in (".env.example", ".env.local.example"):
        keys = _parse_env_keys(root / template_name)
        missing = expected - keys
        assert not missing, f"{template_name} missing Settings keys: {sorted(missing)}"


def test_settings_properties_and_security_defaults() -> None:
    cfg = Settings(
        _env_file=None,
        penpot_access_token="tok-123",
        penpot_db_pass="secret",
        penpot_db_host="localhost",
        penpot_db_port=5432,
        penpot_db_name="penpot",
    )
    assert cfg.has_access_token is True
    assert cfg.has_credentials is False
    assert cfg.penpot_db_user == "penpot_mcp"
    assert cfg.db_statement_timeout_ms == 30000
    assert cfg.ws_enabled is False
    assert cfg.ws_host == "127.0.0.1"
    assert cfg.ws_token == ""
    assert "127.0.0.1" in cfg.mcp_allowed_hosts
    assert cfg.db_dsn == "postgresql://penpot_mcp:secret@localhost:5432/penpot"


def test_decode_transit_map_and_cache() -> None:
    raw = '["^ ","~:name","Login","~:count",2,"~:nested",["^ ","^0","Nested"]]'
    decoded = decode_transit(raw)
    assert decoded == {"name": "Login", "count": 2, "nested": {"name": "Nested"}}


def test_encode_transit_color_and_typography_change_keywords() -> None:
    encoded = _encode_transit_value(
        {
            "changes": [
                {"type": "add-color"},
                {"type": "mod-typography"},
                {"type": "add-component"},
            ]
        }
    )
    assert [c["~:type"] for c in encoded["~:changes"]] == [
        "~:add-color",
        "~:mod-typography",
        "~:add-component",
    ]


def test_shape_to_css_basic() -> None:
    css = shape_to_css(
        {
            "x": 10,
            "y": 20,
            "width": 100,
            "height": 40,
            "r1": 8,
            "r2": 8,
            "r3": 8,
            "r4": 8,
            "fills": [{"fill-color": "#ffffff", "fill-opacity": 1}],
        }
    )
    assert css["position"] == "absolute"
    assert css["left"] == "10px"
    assert css["border-radius"] == "8px"
    assert css["background-color"] == "#ffffff"


@pytest.mark.asyncio
async def test_query_database_rejects_non_select_and_dangerous_sql() -> None:
    res = await query_database("DELETE FROM file")
    assert res[0]["error"].startswith("Only SELECT")

    multi = await query_database("SELECT 1; DELETE FROM file")
    assert "Multiple SQL statements" in multi[0]["error"]

    file_read = await query_database("SELECT pg_read_file('/etc/passwd')")
    assert "pg_read_file" in file_read[0]["error"]

    setval_call = await query_database("SELECT setval('some_seq', 1)")
    assert "setval" in setval_call[0]["error"]

    secret_col = await query_database("SELECT id, password FROM profile")
    assert "password" in secret_col[0]["error"]

    token_tbl = await query_database("SELECT * FROM access_token")
    assert "access_token" in token_tbl[0]["error"]


@pytest.mark.asyncio
async def test_db_fetch_readonly_uses_readonly_transaction_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = PenpotDB()
    executed: list[str] = []
    tx_kwargs: dict[str, object] = {}

    class DummyConn:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            tx_kwargs.update(kwargs)
            yield

        async def execute(self, sql: str) -> None:
            executed.append(sql)

        async def fetch(self, sql: str, *args):
            executed.append(sql)
            return [{"id": "proj-1", "name": "Demo"}]

    @asynccontextmanager
    async def fake_acquire():
        yield DummyConn()

    monkeypatch.setattr(database, "acquire", fake_acquire)
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 15000)

    rows = await database.fetch_readonly("SELECT id, name FROM project")
    assert rows == [{"id": "proj-1", "name": "Demo"}]
    assert tx_kwargs == {"readonly": True}
    assert executed[0] == "SET LOCAL statement_timeout = 15000"
    assert executed[1] == "SELECT id, name FROM project"


def test_ws_controller_token_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl = PenpotWSController(host="127.0.0.1", port=4402)
    monkeypatch.setattr(settings, "ws_token", "super-secret-token")

    responses: list[tuple[int, str]] = []

    class DummyConn:
        def respond(self, status, body: str):
            responses.append((int(status), body))
            return (status, body)

    conn = DummyConn()
    assert ctrl._check_token(conn, SimpleNamespace(path="/")) is not None
    assert responses[-1][0] == int(HTTPStatus.FORBIDDEN)

    assert ctrl._check_token(conn, SimpleNamespace(path="/?token=wrong")) is not None
    assert responses[-1][0] == int(HTTPStatus.FORBIDDEN)

    assert (
        ctrl._check_token(
            conn, SimpleNamespace(path="/?token=super-secret-token")
        )
        is None
    )


@pytest.mark.asyncio
async def test_plugin_config_has_no_wildcard_cors_and_embeds_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "plugin_ws_url", "ws://localhost:4402")
    monkeypatch.setattr(settings, "ws_token", "tok_abc")

    resp = await plugin_config(SimpleNamespace(method="GET"))
    assert "access-control-allow-origin" not in {
        k.lower() for k in resp.headers.keys()
    }
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["ws_url"] == "ws://localhost:4402?token=tok_abc"


def test_dns_rebinding_protection_enabled() -> None:
    ts = mcp.settings.transport_security
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in ts.allowed_hosts
    assert "localhost:*" in ts.allowed_hosts


@pytest.mark.asyncio
async def test_export_object_falls_back_to_credentials_login_for_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_api = PenpotAPI()
    monkeypatch.setattr(settings, "penpot_access_token", "tok-1")
    monkeypatch.setattr(settings, "penpot_email", "user@example.com")
    monkeypatch.setattr(settings, "penpot_password", "pass-1")

    login_called = 0

    async def fake_login() -> None:
        nonlocal login_called
        login_called += 1
        assert client_api._client is not None
        client_api._client.cookies.set("auth-token", "cookie-xyz")

    monkeypatch.setattr(client_api, "_login", fake_login)
    monkeypatch.setattr(
        client_api, "get_profile", AsyncMock(return_value={"id": "prof-1"})
    )

    await client_api.connect()
    assert login_called == 1

    # Clear cookie jar to simulate expired/missing cookie at export time
    assert client_api._client is not None
    client_api._client.cookies.clear()

    async def fake_post(url: str, **kwargs):
        assert kwargs["cookies"] == {"auth-token": "cookie-xyz"}
        return SimpleNamespace(
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n",
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(client_api._client, "post", fake_post)
    data = await client_api.export_object("f1", "p1", "o1", "png")
    assert data == b"\x89PNG\r\n"
    assert login_called == 2
    await client_api.close()


@pytest.mark.asyncio
async def test_mcp_tool_count() -> None:
    tools = await mcp.list_tools()
    assert len(tools) == 77
