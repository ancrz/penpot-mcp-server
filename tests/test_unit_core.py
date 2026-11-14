"""Offline unit, security, and AEP compression/code-mode regression tests for Penpot MCP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from penpot_mcp.aep_engine import (
    GLOBAL_SESSION_POOL,
    ToposGuard,
    ToposVerdict,
    hydrate_struct,
    project_fields,
    resolve_step_refs,
)
from penpot_mcp.config import Settings, settings
from penpot_mcp.server import (
    configure_mcp_tools,
    mcp,
    plugin_config,
    root,
)
from penpot_mcp.services.api import (
    PenpotAPI,
    _encode_transit_value,
    validate_export_redirect_uri,
    validate_external_media_url,
    validate_penpot_endpoint_url,
    validate_rpc_method,
)
from penpot_mcp.services.db import PenpotDB
from penpot_mcp.services.transit import decode_transit
from penpot_mcp.tools.database import query_database
from penpot_mcp.tools.media import upload_media
from penpot_mcp.tools.subtools import (
    assets,
    comments,
    create,
    discover,
    export,
    files,
    media,
    modify,
    playbook_run,
    plugin,
    projects,
    shapes,
    system,
    text,
)
from penpot_mcp.tools.tokens import apply_design_token
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
    repo_root = Path(__file__).resolve().parent.parent
    expected = {name.upper() for name in Settings.model_fields}
    for template_name in (".env.example", ".env.local.example"):
        keys = _parse_env_keys(repo_root / template_name)
        missing = expected - keys
        assert not missing, f"{template_name} missing Settings keys: {sorted(missing)}"


def test_settings_properties_and_backward_compatible_defaults() -> None:
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
    # Backward-compatible default is 'penpot' so existing .env files without PENPOT_DB_USER work
    assert cfg.penpot_db_user == "penpot"
    assert cfg.penpot_mcp_version == "0.3.0"
    assert cfg.mcp_tool_mode == "compressed"
    assert cfg.ssrf_allow_private_media is False
    assert cfg.db_statement_timeout_ms == 30000
    assert cfg.ws_enabled is False
    assert cfg.ws_host == "127.0.0.1"
    assert cfg.ws_token == ""
    assert "127.0.0.1" in cfg.mcp_allowed_hosts
    assert cfg.db_dsn == "postgresql://penpot:secret@localhost:5432/penpot"


@pytest.mark.asyncio
async def test_db_connect_warns_when_using_default_penpot_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = PenpotDB()
    warn_mock = MagicMock()
    monkeypatch.setattr(settings, "penpot_db_user", "penpot")
    monkeypatch.setattr("penpot_mcp.services.db.logger.warning", warn_mock)
    monkeypatch.setattr("penpot_mcp.services.db.asyncpg.create_pool", AsyncMock(return_value="pool"))
    await database.connect()
    assert warn_mock.called
    assert "PENPOT_DB_USER=penpot_mcp" in warn_mock.call_args[0][0]


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
    executed: list[tuple[str, ...]] = []
    tx_kwargs: dict[str, object] = {}

    class DummyConn:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            tx_kwargs.update(kwargs)
            yield

        async def execute(self, sql: str, *args: object) -> None:
            executed.append((sql, *(str(a) for a in args)))

        async def fetch(self, sql: str, *args: object):
            executed.append((sql, *(str(a) for a in args)))
            return [{"id": "proj-1", "name": "Demo"}]

    @asynccontextmanager
    async def fake_acquire():
        yield DummyConn()

    monkeypatch.setattr(database, "acquire", fake_acquire)
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 15000)

    rows = await database.fetch_readonly("SELECT id, name FROM project")
    assert rows == [{"id": "proj-1", "name": "Demo"}]
    assert tx_kwargs == {"readonly": True}
    assert executed[0] == (
        "SELECT set_config('statement_timeout', $1, true)",
        "15000ms",
    )
    assert executed[1] == ("SELECT id, name FROM project",)


def test_ssrf_and_rpc_validation_guards() -> None:
    # 1. RPC command validation
    assert validate_rpc_method("get-profile") == "get-profile"
    for bad_method in ("../admin", "get-file?id=1", "get-file#frag", "http://evil", "GET_FILE"):
        with pytest.raises(ValueError, match="Invalid Penpot RPC command"):
            validate_rpc_method(bad_method)

    # 2. Endpoint URL validation
    assert (
        validate_penpot_endpoint_url("http://penpot-frontend:8080/")
        == "http://penpot-frontend:8080"
    )
    with pytest.raises(ValueError, match="only http/https allowed"):
        validate_penpot_endpoint_url("file:///etc/passwd")
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_penpot_endpoint_url("http://admin:secret@localhost:9001")

    # 3. External media URL SSRF guard
    assert (
        validate_external_media_url("https://images.example.com/banner.png")
        == "https://images.example.com/banner.png"
    )
    for blocked_url in (
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
        "http://127.0.0.1:8080/secret",
        "http://localhost:9001/api",
        "http://[::1]/test.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://10.0.0.5/internal.png",
        "http://192.168.1.20/router.png",
        "http://172.16.0.2/container.png",
        "http://penpot-postgres:5432/db",
        "http://service.internal/asset.png",
        "https://user:pass@images.example.com/x.png",
    ):
        with pytest.raises(ValueError, match="SSRF guard|Invalid media URL"):
            validate_external_media_url(blocked_url)

    # Loopback and cloud metadata remain blocked even when allow_private=True
    assert (
        validate_external_media_url("http://10.0.0.5/asset.png", allow_private=True)
        == "http://10.0.0.5/asset.png"
    )
    with pytest.raises(ValueError, match="SSRF guard blocked restricted IP"):
        validate_external_media_url("http://169.254.169.254/latest", allow_private=True)
    with pytest.raises(ValueError, match="SSRF guard blocked restricted IP"):
        validate_external_media_url("http://127.0.0.1/test", allow_private=True)

    # 4. Exporter redirect URI validation
    rewritten = validate_export_redirect_uri(
        "http://localhost:9001/assets/by-id/123",
        "http://penpot-frontend:8080",
        "http://localhost:9001",
    )
    assert rewritten == "http://penpot-frontend:8080/assets/by-id/123"
    assert (
        validate_export_redirect_uri(
            "/assets/by-id/123",
            "http://penpot-frontend:8080",
            "http://localhost:9001",
        )
        == "/assets/by-id/123"
    )
    with pytest.raises(ValueError, match="SSRF guard blocked"):
        validate_export_redirect_uri(
            "http://169.254.169.254/latest/meta-data/",
            "http://penpot-frontend:8080",
            "http://localhost:9001",
        )
    with pytest.raises(ValueError, match="SSRF guard blocked"):
        validate_export_redirect_uri(
            "//evil.example.com/steal",
            "http://penpot-frontend:8080",
            "http://localhost:9001",
        )


@pytest.mark.asyncio
async def test_upload_media_and_tokens_input_validation() -> None:
    with pytest.raises(ValueError, match="SSRF guard"):
        await upload_media("f1", "bad", "http://169.254.169.254/latest")

    with pytest.raises(ValueError, match="Invalid token_type"):
        await apply_design_token("f1", "p1", ["s1"], "t1", "invalid_type")

    with pytest.raises(ValueError, match="Invalid target_property"):
        await apply_design_token("f1", "p1", ["s1"], "t1", "color", "invalid_prop")

    with pytest.raises(ValueError, match="at least one shape UUID"):
        await apply_design_token("f1", "p1", [], "t1", "color", "fill")


def test_ws_controller_token_gate_and_hardening(monkeypatch: pytest.MonkeyPatch) -> None:
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
        ctrl._check_token(conn, SimpleNamespace(path="/?token=super-secret-token"))
        is None
    )

    ctrl._handle_plugin_message({"type": "selectionchange", "ids": ["id-1", 123, "id-2"]})
    assert ctrl.active_selection == ["id-1", "id-2"]


@pytest.mark.asyncio
async def test_plugin_config_and_root_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "plugin_ws_url", "ws://localhost:4402")
    monkeypatch.setattr(settings, "ws_token", "tok_abc")
    monkeypatch.setattr(settings, "penpot_mcp_version", "0.3.0")
    monkeypatch.setattr(settings, "mcp_tool_mode", "compressed")

    resp = await plugin_config(SimpleNamespace(method="GET"))
    assert "access-control-allow-origin" not in {
        k.lower() for k in resp.headers.keys()
    }
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["ws_url"] == "ws://localhost:4402?token=tok_abc"

    health = await root(SimpleNamespace(method="GET"))
    health_data = json.loads(health.body.decode("utf-8"))
    assert health_data["version"] == "0.3.0"
    assert health_data["tool_mode"] == "compressed"


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
async def test_mcp_tool_modes_compressed_legacy_and_hybrid() -> None:
    # Default mode: 14 compressed AEP-style polymorphic sub-tools
    assert configure_mcp_tools("compressed") == 14
    compressed_tools = await mcp.list_tools()
    assert len(compressed_tools) == 14
    assert {t.name for t in compressed_tools} == {
        "discover",
        "projects",
        "files",
        "shapes",
        "create",
        "modify",
        "text",
        "assets",
        "comments",
        "media",
        "export",
        "system",
        "plugin",
        "playbook_run",
    }

    # Legacy mode: 77 granular tools
    assert configure_mcp_tools("legacy") == 77
    legacy_tools = await mcp.list_tools()
    assert len(legacy_tools) == 77

    # Hybrid mode: 14 + 77 = 91 tools
    assert configure_mcp_tools("hybrid") == 91

    # Restore default compressed mode
    assert configure_mcp_tools("compressed") == 14


def test_aep_engine_primitives_projection_piping_and_dag() -> None:
    # 1. Topos DAG validation
    v_ok, _ = ToposGuard.audit_task_dag(["s1", "s2"], {"s2": ["s1"]})
    assert v_ok == ToposVerdict.APPROVED

    v_cycle, logs = ToposGuard.audit_task_dag(["s1", "s2"], {"s1": ["s2"], "s2": ["s1"]})
    assert v_cycle == ToposVerdict.BLOCKED
    assert "Circular co-dependency" in logs[0]

    # 2. Variable piping ($prev, $steps, $cache) and ${var} hydration
    hydrated = hydrate_struct(
        {"width": "${w}", "label": "Card-${w}"},
        {"w": 360},
    )
    assert hydrated == {"width": 360, "label": "Card-360"}

    resolved = resolve_step_refs(
        {
            "parent_id": "$steps.frame.shape_id",
            "x": "$prev.x",
            "cached_team": "$cache.team_info.id",
            "title": "Frame $steps.frame.shape_id",
        },
        step_outputs={"frame": {"status": "OK", "shape_id": "uuid-frame-1"}},
        prev_output={"x": 42},
        slot_cache={"team_info": {"id": "uuid-team-9"}},
    )
    assert resolved == {
        "parent_id": "uuid-frame-1",
        "x": 42,
        "cached_team": "uuid-team-9",
        "title": "Frame uuid-frame-1",
    }

    # 3. Server-side dot-path field projection
    projected = project_fields(
        {
            "status": "OK",
            "items": [{"id": "p1", "name": "Design System", "extra": "verbose"}],
            "ignored": "large_blob",
        },
        ["status", "items.0.id", "missing.field"],
    )
    assert projected["status"] == "OK"
    assert projected["items"] == [{"id": "p1"}]
    assert projected["unresolved_fields"] == ["missing.field"]
    assert "ignored" not in projected


@pytest.mark.asyncio
async def test_discover_and_polymorphic_subtools_and_code_mode_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    GLOBAL_SESSION_POOL.flush()

    # 1. discover(capabilities) + RAM cache hit + terse mode
    caps_fresh = json.loads(
        await discover(action="capabilities", session_id="test-sess", verbosity="summary")
    )
    assert caps_fresh["status"] == "OK"
    assert caps_fresh["cache_status"] == "FRESH"
    assert caps_fresh["compressed_tools_count"] == 14
    assert caps_fresh["underlying_actions_count"] == 77

    caps_terse = json.loads(
        await discover(action="capabilities", session_id="test-sess", verbosity="terse")
    )
    assert caps_terse["cache_status"] == "CACHED"
    assert caps_terse["token_savings_pct"] == 95.0

    # 2. discover(discover) routing & discover(schema) & discover(playbook)
    routed = json.loads(await discover(action="discover", use_case="create frame layout"))
    assert routed["status"] == "OK"
    assert len(routed["matches"]) >= 1

    schema_res = json.loads(await discover(action="schema", target="create"))
    assert schema_res["tool"] == "create"
    assert "frame" in schema_res["schema"]["actions"]

    pb_res = json.loads(await discover(action="playbook"))
    assert "WF-1: Create Screen & Auto-Layout" in pb_res["playbooks"]

    # 3. Sub-tool invalid action & missing param handling
    bad_act = json.loads(await projects(action="nonexistent"))
    assert bad_act["status"] == "INVALID_ACTION"
    assert "list_projects" in bad_act["valid_actions"]

    missing_p = json.loads(await files(action="summary"))
    assert missing_p["status"] == "PARAM_ERROR"
    assert "file_id" in missing_p["required_params"]

    # 4. Mock underlying tools to test playbook_run(action='batch' / 'code') piping + field projection
    monkeypatch.setattr(
        "penpot_mcp.tools.create.create_frame",
        AsyncMock(return_value={"shape_id": "frame-uuid-100", "name": "HeroFrame"}),
    )
    monkeypatch.setattr(
        "penpot_mcp.tools.modify.set_layout",
        AsyncMock(return_value={"frame_id": "frame-uuid-100", "layout": "flex"}),
    )
    monkeypatch.setattr(
        "penpot_mcp.tools.create.create_text",
        AsyncMock(
            return_value={
                "shape_id": "text-uuid-200",
                "parent_id": "frame-uuid-100",
                "text": "Hello AEP",
            }
        ),
    )

    batch_out = json.loads(
        await playbook_run(
            action="code",
            name="build-hero-card",
            vars={"file_id": "f-1", "page_id": "p-1", "headline": "Hello AEP"},
            return_only="last",
            select_fields=["status", "turns_saved", "output.shape_id", "output.parent_id"],
            steps=[
                {
                    "id": "s1",
                    "tool": "create",
                    "store_as": "hero_frame",
                    "params": {
                        "action": "frame",
                        "file_id": "${file_id}",
                        "page_id": "${page_id}",
                        "name": "HeroFrame",
                    },
                },
                {
                    "id": "s2",
                    "tool": "modify",
                    "depends_on": ["s1"],
                    "params": {
                        "action": "layout",
                        "file_id": "${file_id}",
                        "page_id": "${page_id}",
                        "frame_id": "$steps.s1.shape_id",
                        "direction": "column",
                    },
                },
                {
                    "id": "s3",
                    "tool": "create_text",  # Legacy alias inside batch!
                    "depends_on": ["s2"],
                    "params": {
                        "file_id": "${file_id}",
                        "page_id": "${page_id}",
                        "parent_id": "$cache.hero_frame.shape_id",
                        "text": "${headline}",
                    },
                },
            ],
        )
    )
    assert batch_out["status"] == "OK"
    assert batch_out["output"]["shape_id"] == "text-uuid-200"
    assert batch_out["output"]["parent_id"] == "frame-uuid-100"
    assert batch_out["token_savings_estimate"]["turns_saved"] == 2

    # 5. Cycle in batch steps is BLOCKED by ToposGuard
    blocked_out = json.loads(
        await playbook_run(
            action="batch",
            steps=[
                {"id": "a", "tool": "discover", "depends_on": ["b"], "params": {}},
                {"id": "b", "tool": "discover", "depends_on": ["a"], "params": {}},
            ],
        )
    )
    assert blocked_out["status"] == "BLOCKED"
    assert blocked_out["verdict"] == "BLOCKED"

    # 6. Smoke-check all domain branch toolsets: self-describing action="help" & invalid action guard
    for sub_fn in (projects, files, shapes, create, modify, text, assets, comments, media, export, system, plugin):
        help_json = json.loads(await sub_fn(action="help"))
        assert help_json["status"] == "OK"
        assert len(help_json["available_actions"]) >= 2
        res_json = json.loads(await sub_fn(action="__invalid__"))
        assert res_json["status"] in ("INVALID_ACTION", "PARAM_ERROR")


def test_settings_ignores_extra_build_vars_and_exposes_versions() -> None:
    cfg = Settings(
        _env_file=None,
        penpot_mcp_version="0.3.0",
        mcp_protocol_version="2025-03-26",
        PYTHON_IMAGE_TAG="3.13-alpine",
        UV_VERSION="0.12.10",
    )  # type: ignore[call-arg]
    assert cfg.penpot_mcp_version == "0.3.0"
    assert cfg.mcp_protocol_version == "2025-03-26"
    assert not hasattr(cfg, "PYTHON_IMAGE_TAG")
    assert not hasattr(cfg, "UV_VERSION")
