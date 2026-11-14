"""Penpot MCP Server — AI-powered design tool access via Model Context Protocol."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse

from penpot_mcp.config import settings
from penpot_mcp.services.api import api
from penpot_mcp.services.db import db

logger = logging.getLogger(__name__)

_INSTRUCTIONS = (
    "Penpot MCP Server (AEP-Optimized Suite) — provides AI agents with full access "
    "to self-hosted Penpot design files, shapes, components, design tokens, comments, and exports. "
    "Reads from PostgreSQL for speed, writes via Penpot RPC API for safety.\n"
    "Recommended workflow:\n"
    "1. Call discover(action='capabilities', verbosity='summary') or discover(use_case='...') for orientation.\n"
    "2. Use the 14 compressed polymorphic sub-tools (discover, projects, files, shapes, create, modify, "
    "text, assets, comments, media, export, system, plugin, playbook_run) with verbosity='summary' and select_fields=[...].\n"
    "3. Chain multi-step UI creation/modification in 1 turn using playbook_run(action='batch', steps=[...], "
    "return_only='last') with $prev.*, $steps.<id>.*, and $cache.<key>.* variable piping."
)

mcp = FastMCP(
    "Penpot MCP",
    instructions=_INSTRUCTIONS,
    host=settings.mcp_host,
    port=settings.mcp_port,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{h}:*" for h in settings.mcp_allowed_hosts]
        + list(settings.mcp_allowed_hosts),
        allowed_origins=[f"http://{h}:*" for h in settings.mcp_allowed_hosts],
    ),
)

_orig_create_init_options = mcp._mcp_server.create_initialization_options


def _create_initialization_options(
    notification_options: NotificationOptions | None = None,
    experimental_capabilities: dict[str, dict[str, Any]] | None = None,
) -> Any:
    if notification_options is None:
        notification_options = NotificationOptions(tools_changed=True)
    else:
        notification_options.tools_changed = True
    return _orig_create_init_options(notification_options, experimental_capabilities)


setattr(mcp._mcp_server, "create_initialization_options", _create_initialization_options)

_LEGACY_TOOL_FNS: list[Callable[..., Any]] = []


def _legacy_tool() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record a legacy granular tool function for registration in 'legacy' or 'hybrid' mode."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _LEGACY_TOOL_FNS.append(fn)
        return fn

    return _decorator


# ═══════════════════════════════════════════════════════════════
# Root health check
# ═══════════════════════════════════════════════════════════════


@mcp.custom_route("/", methods=["GET"])
async def root(request):
    """Root health check endpoint."""
    return JSONResponse(
        {
            "service": "Penpot MCP",
            "status": "ok",
            "version": settings.penpot_mcp_version,
            "protocol_version": settings.mcp_protocol_version,
            "tool_mode": settings.mcp_tool_mode,
        }
    )


# ═══════════════════════════════════════════════════════════════
# Plugin: Static file serving for Penpot browser plugin
# Load via Penpot: Main Menu -> Plugin Manager -> http://localhost:8787/plugin/manifest.json
# ═══════════════════════════════════════════════════════════════

_PLUGIN_DIR = Path(__file__).parent / "plugin"


@mcp.custom_route("/plugin/manifest.json", methods=["GET", "OPTIONS"])
async def plugin_manifest(request):
    """Serve the Penpot plugin manifest (dynamically adapting host origin for localhost or Docker)."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    manifest_data = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    req_headers = getattr(request, "headers", {}) or {}
    host_hdr = req_headers.get("host")
    if host_hdr:
        scheme = getattr(getattr(request, "url", None), "scheme", "http") or "http"
        manifest_data["host"] = f"{scheme}://{host_hdr}"
    return JSONResponse(
        manifest_data,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/plugin.js", methods=["GET", "OPTIONS"])
async def plugin_js(request):
    """Serve the Penpot plugin JavaScript."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return FileResponse(
        _PLUGIN_DIR / "plugin.js",
        media_type="application/javascript",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/ui.html", methods=["GET", "OPTIONS"])
async def plugin_ui(request):
    """Serve the Penpot plugin UI panel."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    return FileResponse(
        _PLUGIN_DIR / "ui.html",
        media_type="text/html",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@mcp.custom_route("/plugin/config.json", methods=["GET"])
async def plugin_config(request):
    """Serve dynamic plugin configuration (WebSocket URL, same-origin only)."""
    ws_url = settings.plugin_ws_url
    same_origin_ws_path = "/plugin/ws"
    if settings.ws_token:
        sep = "&" if "?" in ws_url else "?"
        encoded_tok = quote(settings.ws_token, safe="")
        ws_url = f"{ws_url}{sep}token={encoded_tok}"
        same_origin_ws_path = f"/plugin/ws?token={encoded_tok}"
    return JSONResponse(
        {
            "ws_url": ws_url,
            "same_origin_ws_path": same_origin_ws_path,
            "version": settings.penpot_mcp_version,
            "agent_model": settings.penpot_agent_model,
            "agent_mode": settings.penpot_agent_mode,
        }
    )


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }


@mcp.custom_route("/plugin/telemetry", methods=["POST", "OPTIONS"])
async def plugin_telemetry(request):
    """Receive live selection/context telemetry from ui.html and return any pending commands."""
    from starlette.responses import Response
    from penpot_mcp.ws_controller import ws_controller

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        body = await request.json()
    except Exception:
        body = {}
    commands = ws_controller.record_http_telemetry(body if isinstance(body, dict) else {})
    return JSONResponse(
        {
            "status": "ok",
            "commands": commands,
            "context": ws_controller.get_live_context(),
        },
        headers=_cors_headers(),
    )


@mcp.custom_route("/agent/status", methods=["GET", "OPTIONS"])
async def agent_status_route(request):
    """Return non-secret status of the embedded SDK Agent Pipeline, provider chain, and active canvas."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    q_uid = request.query_params.get("user_id")
    q_email = request.query_params.get("user_email") or request.query_params.get("email")
    q_sid = request.query_params.get("session_id")
    canvas = await agent_gateway.resolve_target_canvas()
    await agent_gateway.switch_or_resolve_user(
        user_id=q_uid,
        email=q_email,
        session_id=q_sid,
        file_id=canvas.get("file_id"),
    )
    status_data = agent_gateway.get_status()
    status_data["resolved_canvas"] = canvas
    status_data["pgvector"] = await db.get_pgvector_status()
    return JSONResponse(status_data, headers=_cors_headers())


@mcp.custom_route("/agent/chat", methods=["POST", "OPTIONS"])
async def agent_chat_route(request):
    """Execute a natural-language or quick-action turn through the SDK Agent Pipeline Gateway."""
    from dataclasses import asdict
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "ERROR", "error": "Invalid JSON body."},
            status_code=400,
            headers=_cors_headers(),
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "ERROR", "error": "JSON body must be an object."},
            status_code=400,
            headers=_cors_headers(),
        )

    prompt = str(
        payload.get("prompt") or payload.get("message") or payload.get("text") or ""
    ).strip()
    quick_action = str(payload.get("quick_action") or "").strip() or None
    if not prompt and not quick_action:
        return JSONResponse(
            {"status": "PARAM_ERROR", "error": "'prompt' or 'quick_action' is required."},
            status_code=422,
            headers=_cors_headers(),
        )

    turn = await agent_gateway.chat(
        prompt or quick_action or "",
        file_id=payload.get("file_id"),
        page_id=payload.get("page_id"),
        user_id=payload.get("user_id"),
        user_email=payload.get("user_email") or payload.get("email"),
        session_id=payload.get("session_id"),
        quick_action=quick_action,
        history=payload.get("history") if isinstance(payload.get("history"), list) else None,
        attachment_ids=(
            [str(x) for x in payload["attachment_ids"]]
            if isinstance(payload.get("attachment_ids"), list)
            else None
        ),
    )
    return JSONResponse(asdict(turn), headers=_cors_headers())


@mcp.custom_route("/agent/upload", methods=["POST", "OPTIONS"])
async def agent_upload_route(request):
    """Upload a .penpot template, SVG, JSON, text, or image for chat context / Penpot import."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway
    from penpot_mcp.services.attachments import parse_uploaded_attachment

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse(
                {"status": "PARAM_ERROR", "error": "Field 'file' (multipart/form-data) is required."},
                status_code=422,
                headers=_cors_headers(),
            )
        raw_bytes = await upload.read()
        filename = str(getattr(upload, "filename", "") or "upload.bin")
        content_type = str(getattr(upload, "content_type", "") or "")
        import_flag = str(form.get("import_to_penpot") or "").strip().lower() in ("1", "true", "yes")
        project_id = str(form.get("project_id") or "").strip()

        att = parse_uploaded_attachment(
            filename=filename,
            content_bytes=raw_bytes,
            content_type=content_type,
        )

        # Keep bounded cache (50 items) in gateway memory for chat context injection
        if len(agent_gateway.attachments_cache) >= 50:
            oldest_key = next(iter(agent_gateway.attachments_cache))
            agent_gateway.attachments_cache.pop(oldest_key, None)
        agent_gateway.attachments_cache[att["id"]] = att

        if import_flag and att.get("kind") == "penpot_template":
            try:
                if not project_id:
                    project_id = str(agent_gateway.active_user.get("default_project_id") or "")
                if not project_id:
                    teams = await api.list_teams()
                    if teams:
                        projs = await api.list_projects(str(teams[0]["id"]))
                        if projs:
                            project_id = str(projs[0]["id"])
                if project_id:
                    imp = await api.import_binfile(project_id, att["filename"], raw_bytes)
                    att["imported_to_penpot"] = True
                    att["import_result"] = imp
                else:
                    att["imported_to_penpot"] = False
                    att["import_error"] = "No se encontró un project_id destino."
            except Exception as exc:
                att["imported_to_penpot"] = False
                att["import_error"] = str(exc)

        public_att = {k: v for k, v in att.items() if k != "image_base64"}
        return JSONResponse({"status": "OK", "attachment": public_att}, headers=_cors_headers())
    except ValueError as exc:
        return JSONResponse(
            {"status": "PARAM_ERROR", "error": str(exc)},
            status_code=422,
            headers=_cors_headers(),
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "ERROR", "error": str(exc)},
            status_code=500,
            headers=_cors_headers(),
        )


@mcp.custom_route("/agent/breakers/reset", methods=["POST", "OPTIONS"])
async def agent_breakers_reset(request):
    """Reset any open provider circuit breakers."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        body = await request.json()
        scope = body.get("scope") if isinstance(body, dict) else None
    except Exception:
        scope = None
    count = agent_gateway.breakers.reset(scope)
    return JSONResponse({"status": "ok", "reset_count": count}, headers=_cors_headers())


@mcp.custom_route("/agent/config", methods=["GET", "OPTIONS"])
async def agent_config_route(request):
    """Return full AI Gateway configuration (presets, providers, masked keys, active profile, instructions, user & pgvector)."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    q_uid = request.query_params.get("user_id")
    q_email = request.query_params.get("user_email") or request.query_params.get("email")
    q_sid = request.query_params.get("session_id")
    canvas = await agent_gateway.resolve_target_canvas()
    await agent_gateway.switch_or_resolve_user(
        user_id=q_uid,
        email=q_email,
        session_id=q_sid,
        file_id=canvas.get("file_id"),
    )
    cfg = agent_gateway.get_config()
    cfg["resolved_canvas"] = canvas
    cfg["penpot_users"] = await db.list_penpot_users(canvas.get("file_id"))
    cfg["pgvector"] = await db.get_pgvector_status()
    return JSONResponse(json.loads(json.dumps(cfg, default=str)), headers=_cors_headers())


@mcp.custom_route("/agent/users", methods=["GET", "POST", "OPTIONS"])
async def agent_users_route(request):
    """List Penpot users from public.profile or switch the active authenticated user session."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    canvas = await agent_gateway.resolve_target_canvas()
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        active_u = await agent_gateway.switch_or_resolve_user(
            user_id=payload.get("user_id"),
            email=payload.get("email") or payload.get("user_email"),
            session_id=payload.get("session_id"),
            file_id=payload.get("file_id") or canvas.get("file_id"),
        )
    else:
        active_u = await agent_gateway.switch_or_resolve_user(
            user_id=request.query_params.get("user_id"),
            email=request.query_params.get("email") or request.query_params.get("user_email"),
            session_id=request.query_params.get("session_id"),
            file_id=canvas.get("file_id"),
        )

    users = await db.list_penpot_users(canvas.get("file_id"))
    out = {
        "status": "ok",
        "active_user": active_u,
        "users": users,
        "active_profile": agent_gateway.active_profile,
        "credentials": agent_gateway.list_catalog(),
    }
    return JSONResponse(json.loads(json.dumps(out, default=str)), headers=_cors_headers())


@mcp.custom_route("/agent/vision", methods=["GET", "POST", "OPTIONS"])
async def agent_vision_route(request):
    """Sync live Penpot canvas objects into PostgreSQL pgvector and query the semantic + spatial scene graph."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway
    from penpot_mcp.services.vision import sync_and_query_canvas_vision

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())

    payload: dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}

    q_file = payload.get("file_id") or request.query_params.get("file_id")
    q_page = payload.get("page_id") or request.query_params.get("page_id")
    q_text = str(
        payload.get("query")
        or request.query_params.get("query")
        or "hero card title button frame"
    )
    top_k = int(payload.get("top_k") or request.query_params.get("top_k") or 10)

    canvas = await agent_gateway.resolve_target_canvas(file_id=q_file, page_id=q_page)
    vis = await sync_and_query_canvas_vision(
        file_id=canvas.get("file_id"),
        page_id=canvas.get("page_id"),
        query_text=q_text,
        top_k=top_k,
    )
    vis["pgvector_status"] = await db.get_pgvector_status()
    return JSONResponse(json.loads(json.dumps(vis, default=str)), headers=_cors_headers())


@mcp.custom_route("/agent/providers", methods=["POST", "PUT", "DELETE", "OPTIONS"])
async def agent_providers_route(request):
    """Create, replace (write-only API key), or delete a provider credential profile (per Penpot user)."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "ERROR", "error": "JSON body must be an object."},
            status_code=400,
            headers=_cors_headers(),
        )

    if payload.get("user_id") or payload.get("user_email"):
        await agent_gateway.switch_or_resolve_user(
            user_id=payload.get("user_id"),
            email=payload.get("user_email"),
        )

    if request.method == "DELETE" or payload.get("action") == "delete":
        name = str(payload.get("name") or request.query_params.get("name") or "").strip()
        try:
            res = agent_gateway.delete_credential(name)
            await agent_gateway.sync_active_user_to_db()
            return JSONResponse(res, headers=_cors_headers())
        except KeyError as exc:
            return JSONResponse(
                {"status": "NOT_FOUND", "error": str(exc)},
                status_code=404,
                headers=_cors_headers(),
            )

    try:
        entry = agent_gateway.upsert_credential(
            name=str(payload.get("name") or ""),
            provider=str(payload.get("provider") or "gemini"),
            api_key=payload.get("api_key"),
            base_url=payload.get("base_url"),
            tier=payload.get("tier"),
            purpose=payload.get("purpose"),
            description=payload.get("description"),
            model=payload.get("model"),
            activate=bool(payload.get("activate", True)),
        )
        await agent_gateway.sync_active_user_to_db()
        return JSONResponse(
            {
                "status": "ok",
                "active_user": agent_gateway.active_user,
                "credential": entry,
                "active_profile": agent_gateway.active_profile,
                "credentials": agent_gateway.list_catalog(),
            },
            headers=_cors_headers(),
        )
    except ValueError as exc:
        return JSONResponse(
            {"status": "PARAM_ERROR", "error": str(exc)},
            status_code=422,
            headers=_cors_headers(),
        )


@mcp.custom_route("/agent/providers/test", methods=["POST", "OPTIONS"])
async def agent_providers_test_route(request):
    """Test a provider API key or local endpoint and list its generative models."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    res = await agent_gateway.test_provider_key(
        provider=str(payload.get("provider") or "gemini"),
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        credential_name=payload.get("credential") or payload.get("name"),
    )
    return JSONResponse(res, headers=_cors_headers())


@mcp.custom_route("/agent/profile", methods=["POST", "PUT", "OPTIONS"])
async def agent_profile_route(request):
    """Switch active preset, provider account, model, fallback model, and RPM limit without restart."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "ERROR", "error": "Invalid JSON body."},
            status_code=400,
            headers=_cors_headers(),
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "ERROR", "error": "JSON body must be an object."},
            status_code=400,
            headers=_cors_headers(),
        )
    if payload.get("user_id") or payload.get("user_email"):
        await agent_gateway.switch_or_resolve_user(
            user_id=payload.get("user_id"),
            email=payload.get("user_email"),
        )
    prof = agent_gateway.update_active_profile(
        preset=payload.get("preset"),
        account=payload.get("account") or payload.get("credential"),
        provider=payload.get("provider"),
        model=payload.get("model"),
        fallback_model=payload.get("fallback_model"),
        rpm=payload.get("rpm"),
    )
    await agent_gateway.sync_active_user_to_db()
    return JSONResponse(
        {
            "status": "ok",
            "active_user": agent_gateway.active_user,
            "active_profile": prof,
            "routes": agent_gateway.get_status()["routes"],
        },
        headers=_cors_headers(),
    )


@mcp.custom_route("/agent/instructions", methods=["POST", "PUT", "OPTIONS"])
async def agent_instructions_route(request):
    """Save or reset the versioned agent instruction addendum (Vanguard Control AgentsTab)."""
    from starlette.responses import Response
    from penpot_mcp.agent_gateway import agent_gateway

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("user_id") or payload.get("user_email"):
        await agent_gateway.switch_or_resolve_user(
            user_id=payload.get("user_id"),
            email=payload.get("user_email"),
        )
    inst = agent_gateway.update_agent_instructions(
        op=str(payload.get("op") or "save"),
        addendum=str(payload.get("addendum") or payload.get("text") or ""),
        note=str(payload.get("note") or ""),
    )
    await agent_gateway.sync_active_user_to_db()
    return JSONResponse({"status": "ok", "agent_instructions": inst}, headers=_cors_headers())


@mcp.custom_route("/plugin/embed.js", methods=["GET", "OPTIONS"])
async def plugin_embed_js(request):
    """Serve a 1-click floating launcher script that docks Penpot AI Studio inside any Penpot workspace tab."""
    from starlette.responses import Response

    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers())
    host_header = request.headers.get("host", f"localhost:{settings.mcp_port}")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme or "http")
    origin = f"{scheme}://{host_header}"
    js_code = f"""(async function() {{
  const ORIGIN = {json.dumps(origin)};
  const params = new URLSearchParams(window.location.search || window.location.hash.split('?')[1] || '');
  const fileId = params.get('file-id') || '';
  const pageId = params.get('page-id') || '';
  let userId = '';
  let userEmail = '';
  let userName = '';
  try {{
    const profRes = await fetch('/api/rpc/command/get-profile', {{
      credentials: 'include',
      headers: {{ 'Accept': 'application/json' }}
    }});
    if (profRes.ok) {{
      const prof = await profRes.json();
      userId = prof.id || prof['~:id'] || '';
      userEmail = prof.email || prof['~:email'] || '';
      userName = prof.fullname || prof['~:fullname'] || '';
    }}
  }} catch (e) {{}}

  const existingDock = document.getElementById('penpot-ai-studio-dock');
  const existingFab = document.getElementById('penpot-ai-studio-fab');
  if (existingDock) existingDock.remove();
  if (existingFab) existingFab.remove();

  const dock = document.createElement('div');
  dock.id = 'penpot-ai-studio-dock';
  dock.style.cssText = 'position:fixed; right:18px; bottom:70px; width:440px; height:685px; z-index:99999; border-radius:14px; overflow:hidden; box-shadow:0 24px 60px rgba(0,0,0,0.65), 0 0 0 1px rgba(99,102,241,0.55); background:#090d16; transition:opacity 0.2s ease, transform 0.2s ease;';
  const iframe = document.createElement('iframe');
  iframe.id = 'penpot-ai-studio-iframe';
  iframe.src = ORIGIN + '/plugin/ui.html?file_id=' + encodeURIComponent(fileId)
    + '&page_id=' + encodeURIComponent(pageId)
    + '&user_id=' + encodeURIComponent(userId)
    + '&user_email=' + encodeURIComponent(userEmail)
    + '&user_name=' + encodeURIComponent(userName);
  iframe.style.cssText = 'width:100%; height:100%; border:none;';
  dock.appendChild(iframe);
  document.body.appendChild(dock);

  const fab = document.createElement('button');
  fab.id = 'penpot-ai-studio-fab';
  fab.type = 'button';
  fab.innerHTML = '🤖 Penpot AI Studio';
  fab.style.cssText = 'position:fixed; right:18px; bottom:18px; z-index:100000; background:linear-gradient(135deg,#6366f1,#0ea5e9); color:#fff; border:none; border-radius:999px; padding:10px 16px; font-family:Inter,sans-serif; font-weight:700; font-size:12.5px; cursor:pointer; box-shadow:0 10px 25px rgba(99,102,241,0.45);';
  let open = true;
  fab.onclick = function() {{
    open = !open;
    dock.style.display = open ? 'block' : 'none';
  }};
  document.body.appendChild(fab);
}})();
"""
    return Response(content=js_code, media_type="application/javascript", headers=_cors_headers())


# ═══════════════════════════════════════════════════════════════
# Category 1: Projects & Teams
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def list_teams() -> str:
    """List all teams in the Penpot instance with member and project counts."""
    from penpot_mcp.tools.projects import list_teams as _list_teams

    result = await _list_teams()
    return json.dumps(result, indent=2)


@_legacy_tool()
async def list_projects(team_id: str | None = None) -> str:
    """List projects, optionally filtered by team ID.

    Args:
        team_id: Filter by team UUID. Omit to list all projects.
    """
    from penpot_mcp.tools.projects import list_projects as _list_projects

    result = await _list_projects(team_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def list_files(project_id: str) -> str:
    """List all files in a project.

    Args:
        project_id: The project UUID.
    """
    from penpot_mcp.tools.projects import list_files as _list_files

    result = await _list_files(project_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def search_files(query: str) -> str:
    """Search files by name across all projects.

    Args:
        query: Search term (case-insensitive partial match).
    """
    from penpot_mcp.tools.projects import search_files as _search_files

    result = await _search_files(query)
    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 2: File Operations
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_file_summary(file_id: str) -> str:
    """Get detailed metadata for a file (counts, team, project, versions).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_summary as _get_file_summary

    result = await _get_file_summary(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_file_pages(file_id: str) -> str:
    """Get all pages in a file with their object counts.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_pages as _get_file_pages

    result = await _get_file_pages(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_file_history(file_id: str, limit: int = 20) -> str:
    """Get revision history of a file.

    Args:
        file_id: The file UUID.
        limit: Max entries to return (default 20).
    """
    from penpot_mcp.tools.files import get_file_history as _get_file_history

    result = await _get_file_history(file_id, limit)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_file_libraries(file_id: str) -> str:
    """List shared libraries linked to a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import get_file_libraries as _get_file_libraries

    result = await _get_file_libraries(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def create_project(team_id: str, name: str) -> str:
    """Create a new project in a team.

    Args:
        team_id: The team UUID.
        name: Project name.
    """
    from penpot_mcp.tools.files import create_project as _create_project

    result = await _create_project(team_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_file(project_id: str, name: str) -> str:
    """Create a new design file in a project.

    Args:
        project_id: The project UUID.
        name: File name.
    """
    from penpot_mcp.tools.files import create_file as _create_file

    result = await _create_file(project_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def rename_file(file_id: str, name: str) -> str:
    """Rename an existing file.

    Args:
        file_id: The file UUID.
        name: New name.
    """
    from penpot_mcp.tools.files import rename_file as _rename_file

    result = await _rename_file(file_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def duplicate_file(file_id: str, name: str | None = None) -> str:
    """Duplicate an existing file.

    Args:
        file_id: The file UUID.
        name: Optional name for the copy.
    """
    from penpot_mcp.tools.files import duplicate_file as _duplicate_file

    result = await _duplicate_file(file_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def delete_file(file_id: str) -> str:
    """Delete a file (moves to trash).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.files import delete_file as _delete_file

    result = await _delete_file(file_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 3: Shapes — Reading
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_page_objects(
    file_id: str, page_id: str, shape_type: str | None = None
) -> str:
    """List all objects on a page, optionally filtered by type.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_type: Filter — rect, circle, frame, text, group, path, image, svg-raw, bool.
    """
    from penpot_mcp.gateway import gateway

    result = await gateway.execute_intent(
        "get_page_objects", file_id=file_id, page_id=page_id, shape_type=shape_type
    )
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_shape_tree(
    file_id: str, page_id: str, root_id: str | None = None, depth: int = 3
) -> str:
    """Get the hierarchical tree of shapes on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        root_id: Start from this shape ID (omit for page root).
        depth: Max tree depth (default 3).
    """
    from penpot_mcp.gateway import gateway

    result = await gateway.execute_intent(
        "get_shape_tree", file_id=file_id, page_id=page_id, root_id=root_id, depth=depth
    )
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_shape_details(file_id: str, page_id: str, shape_id: str) -> str:
    """Get full details of a specific shape (fills, strokes, layout, text content).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details

    result = await _get_shape_details(file_id, page_id, shape_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def search_shapes(
    file_id: str, page_id: str, query: str, search_type: str = "name"
) -> str:
    """Search shapes by name or text content on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        query: Search term (case-insensitive).
        search_type: "name" or "text".
    """
    from penpot_mcp.tools.shapes import search_shapes as _search_shapes

    result = await _search_shapes(file_id, page_id, query, search_type)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_shape_css(file_id: str, page_id: str, shape_id: str) -> str:
    """Get CSS representation of a shape's visual properties.

    Converts Penpot fills, strokes, shadows, layout etc. to CSS.
    Inspired by Framelink/Figma's code generation approach.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details
    from penpot_mcp.transformers.css import shape_to_css_string

    shape = await _get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return json.dumps(shape)
    css = shape_to_css_string(shape)
    return json.dumps(
        {"shape_id": shape_id, "name": shape.get("name"), "css": css}, indent=2
    )


@_legacy_tool()
async def get_shape_svg(file_id: str, page_id: str, shape_id: str) -> str:
    """Get SVG representation of a shape.

    Leverages Penpot's native SVG format for direct conversion.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details
    from penpot_mcp.transformers.svg import shape_to_svg

    shape = await _get_shape_details(file_id, page_id, shape_id)
    if "error" in shape:
        return json.dumps(shape)
    svg = shape_to_svg(shape)
    return json.dumps(
        {"shape_id": shape_id, "name": shape.get("name"), "svg": svg}, indent=2
    )


# ═══════════════════════════════════════════════════════════════
# Category 4: Components & Design Tokens
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_component_instances(file_id: str) -> str:
    """List all components defined in a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_component_instances as _get_components

    result = await _get_components(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_design_tokens(file_id: str) -> str:
    """Get consolidated design tokens (colors, typographies, component count) from a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_design_tokens as _get_design_tokens

    result = await _get_design_tokens(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_colors_library(file_id: str) -> str:
    """Get all colors defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_colors_library as _get_colors

    result = await _get_colors(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def create_color(
    file_id: str,
    name: str,
    color: str | None = None,
    opacity: float = 1.0,
    path: str = "",
    gradient: dict | None = None,
) -> str:
    """Create a native color asset (solid color or gradient) in a file's local library.

    Args:
        file_id: The file UUID.
        name: Asset name within its path, such as "Primary".
        color: Optional 3- or 6-digit hexadecimal color, such as "#DFFB73".
        opacity: Color opacity from 0 to 1 (default 1).
        path: Optional slash-separated asset group, such as "Accent".
        gradient: Optional gradient dictionary containing type (linear/radial) and stops.
    """
    from penpot_mcp.tools.colors import create_color as _create_color

    result = await _create_color(file_id, name, color, opacity, path, gradient)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def update_color(
    file_id: str,
    color_id: str,
    name: str | None = None,
    color: str | None = None,
    opacity: float | None = None,
    path: str | None = None,
    gradient: dict | None = None,
) -> str:
    """Update or rename a native color asset, preserving unspecified fields.

    Args:
        file_id: The file UUID.
        color_id: The color asset UUID.
        name: Optional new asset name.
        color: Optional new 3- or 6-digit hexadecimal color.
        opacity: Optional opacity from 0 to 1.
        path: Optional slash-separated asset group; pass an empty string for root.
        gradient: Optional gradient dictionary containing type (linear/radial) and stops.
    """
    from penpot_mcp.tools.colors import update_color as _update_color

    result = await _update_color(file_id, color_id, name, color, opacity, path, gradient)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def delete_color(file_id: str, color_id: str) -> str:
    """Delete a native color asset from a file's local library.

    Args:
        file_id: The file UUID.
        color_id: The color asset UUID.
    """
    from penpot_mcp.tools.colors import delete_color as _delete_color

    result = await _delete_color(file_id, color_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_typography_library(file_id: str) -> str:
    """Get all typographies defined in a file's library.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.components import get_typography_library as _get_typography

    result = await _get_typography(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def create_typography(
    file_id: str,
    name: str,
    font_id: str = "sourcesanspro",
    font_family: str = "sourcesanspro",
    font_variant_id: str = "regular",
    font_size: str = "14",
    font_weight: str = "480",
    font_style: str = "normal",
    line_height: str = "1.2",
    letter_spacing: str = "0",
    text_transform: str = "none",
    path: str = "",
) -> str:
    """Create a native typography asset in a file's local library.

    Font metrics are strings because that is Penpot's native typography schema.
    """
    from penpot_mcp.tools.typographies import create_typography as _create_typography

    result = await _create_typography(
        file_id,
        name,
        font_id,
        font_family,
        font_variant_id,
        font_size,
        font_weight,
        font_style,
        line_height,
        letter_spacing,
        text_transform,
        path,
    )
    return json.dumps(result, indent=2)


@_legacy_tool()
async def update_typography(
    file_id: str,
    typography_id: str,
    name: str | None = None,
    font_id: str | None = None,
    font_family: str | None = None,
    font_variant_id: str | None = None,
    font_size: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    line_height: str | None = None,
    letter_spacing: str | None = None,
    text_transform: str | None = None,
    path: str | None = None,
) -> str:
    """Update a native typography asset, preserving unspecified fields."""
    from penpot_mcp.tools.typographies import update_typography as _update_typography

    result = await _update_typography(
        file_id,
        typography_id,
        name,
        font_id,
        font_family,
        font_variant_id,
        font_size,
        font_weight,
        font_style,
        line_height,
        letter_spacing,
        text_transform,
        path,
    )
    return json.dumps(result, indent=2)


@_legacy_tool()
async def delete_typography(file_id: str, typography_id: str) -> str:
    """Delete a native typography asset from a file's local library."""
    from penpot_mcp.tools.typographies import delete_typography as _delete_typography

    result = await _delete_typography(file_id, typography_id)
    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 5: Comments & Collaboration
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_comments(file_id: str, resolved: bool | None = None) -> str:
    """Get all comments on a file.

    Args:
        file_id: The file UUID.
        resolved: Filter — True=resolved, False=unresolved, omit=all.
    """
    from penpot_mcp.tools.comments import get_comments as _get_comments

    result = await _get_comments(file_id, resolved)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_active_users(file_id: str) -> str:
    """Get users currently editing a file (real-time presence).

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.comments import get_active_users as _get_active_users

    result = await _get_active_users(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_share_links(file_id: str) -> str:
    """List share links for a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.comments import get_share_links as _get_share_links

    result = await _get_share_links(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def create_comment(
    file_id: str,
    page_id: str,
    content: str,
    x: float,
    y: float,
    frame_id: str | None = None,
) -> str:
    """Create a new comment on a page at a specific position.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        content: Comment text.
        x: X position.
        y: Y position.
        frame_id: Optional frame to attach the comment to.
    """
    from penpot_mcp.tools.comments import create_comment as _create_comment

    result = await _create_comment(file_id, page_id, content, x, y, frame_id)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def reply_to_comment(thread_id: str, content: str) -> str:
    """Reply to an existing comment thread.

    Args:
        thread_id: The comment thread UUID.
        content: Reply text.
    """
    from penpot_mcp.tools.comments import reply_to_comment as _reply

    result = await _reply(thread_id, content)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def resolve_comment(thread_id: str, resolved: bool = True) -> str:
    """Resolve or unresolve a comment thread.

    Args:
        thread_id: The comment thread UUID.
        resolved: True to resolve, False to unresolve.
    """
    from penpot_mcp.tools.comments import resolve_comment as _resolve

    result = await _resolve(thread_id, resolved)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 6: Media & Fonts
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def list_media_assets(file_id: str) -> str:
    """List all media assets (images) in a file.

    Args:
        file_id: The file UUID.
    """
    from penpot_mcp.tools.media import list_media_assets as _list_media

    result = await _list_media(file_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def list_fonts(team_id: str) -> str:
    """List custom fonts uploaded to a team.

    Args:
        team_id: The team UUID.
    """
    from penpot_mcp.tools.media import list_fonts as _list_fonts

    result = await _list_fonts(team_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def upload_media(file_id: str, name: str, url: str) -> str:
    """Upload an image to a file from a URL.

    Args:
        file_id: The file UUID.
        name: Name for the media asset.
        url: Public URL of the image.
    """
    from penpot_mcp.tools.media import upload_media as _upload

    result = await _upload(file_id, name, url)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 7: Database & Advanced
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def query_database(sql: str) -> str:
    """Execute a read-only SQL query against the Penpot database.

    Power tool for advanced queries. Only SELECT statements allowed.

    Args:
        sql: SQL SELECT query.
    """
    from penpot_mcp.tools.database import query_database as _query

    result = await _query(sql)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_webhooks(team_id: str) -> str:
    """List webhooks configured for a team.

    Args:
        team_id: The team UUID.
    """
    from penpot_mcp.tools.database import get_webhooks as _get_webhooks

    result = await _get_webhooks(team_id)
    return json.dumps(result, indent=2)


@_legacy_tool()
async def get_profile() -> str:
    """Get the authenticated user's profile information."""
    result = await api.get_profile()
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8: Snapshots
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def create_snapshot(file_id: str, label: str) -> str:
    """Create a named snapshot (version) of a file.

    Args:
        file_id: The file UUID.
        label: Label for the snapshot.
    """
    result = await api.create_snapshot(file_id, label)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def get_snapshots(file_id: str) -> str:
    """List all snapshots of a file.

    Args:
        file_id: The file UUID.
    """
    result = await api.get_snapshots(file_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8b: Export
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def export_frame_png(
    file_id: str, page_id: str, object_id: str, scale: float = 1.0
) -> str:
    """Export a frame or shape to PNG via Penpot's exporter (headless Chromium).

    Returns base64-encoded PNG data. Use scale=2.0 for retina quality.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
        scale: Scale factor (1.0=normal, 2.0=retina).
    """
    from penpot_mcp.tools.export import export_frame_png as _export

    result = await _export(file_id, page_id, object_id, scale)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def export_frame_svg(file_id: str, page_id: str, object_id: str) -> str:
    """Export a frame or shape to SVG.

    Uses Penpot's exporter for pixel-perfect SVG; falls back to local
    SVG generation from shape data if the exporter is unavailable.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        object_id: The shape/frame UUID to export.
    """
    from penpot_mcp.tools.export import export_frame_svg as _export

    result = await _export(file_id, page_id, object_id)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 8c: Advanced Analysis
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_file_raw_data(file_id: str, page_id: str | None = None) -> str:
    """Get the decoded internal data structure of a file.

    Returns the file's pages, components, colors, and typographies
    after full transit decoding. Useful for debugging and advanced automation.

    Args:
        file_id: The file UUID.
        page_id: Optional — returns only that page's data if provided.
    """
    from penpot_mcp.tools.advanced import get_file_raw_data as _get

    result = await _get(file_id, page_id)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def compare_revisions(
    file_id: str, revn_from: int, revn_to: int | None = None
) -> str:
    """Compare two revisions of a file to see what changed.

    Shows the change operations (add-obj, mod-obj, del-obj, etc.)
    between two revision numbers. Useful for understanding edit history.

    Args:
        file_id: The file UUID.
        revn_from: Starting revision number.
        revn_to: Ending revision number (default: latest).
    """
    from penpot_mcp.tools.advanced import compare_revisions as _compare

    result = await _compare(file_id, revn_from, revn_to)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 9: Shape Creation
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def create_rectangle(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 100,
    height: float = 100,
    name: str = "Rectangle",
    fill_color: str = "#B1B2B5",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    parent_id: str | None = None,
) -> str:
    """Create a rectangle shape on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 100).
        height: Height in pixels (default 100).
        name: Shape name (default "Rectangle").
        fill_color: Fill color hex (default "#B1B2B5").
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Optional stroke color hex.
        stroke_width: Stroke width in pixels (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        border_radius: Corner radius for all corners (default 0).
        parent_id: Parent shape ID. If omitted, adds to root frame.
    """
    from penpot_mcp.tools.create import create_rectangle as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        border_radius,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_frame(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 300,
    height: float = 300,
    name: str = "Frame",
    fill_color: str = "#FFFFFF",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    clip_content: bool = True,
    parent_id: str | None = None,
) -> str:
    """Create a frame (artboard/container) that can hold child shapes.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 300).
        height: Height in pixels (default 300).
        name: Frame name (default "Frame").
        fill_color: Background color hex (default "#FFFFFF").
        fill_opacity: Background opacity 0-1 (default 1.0).
        stroke_color: Optional border color hex.
        stroke_width: Border width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        border_radius: Corner radius (default 0).
        clip_content: Clip children at frame bounds (default true).
        parent_id: Parent frame ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_frame as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        border_radius,
        clip_content,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_ellipse(
    file_id: str,
    page_id: str,
    x: float = 0,
    y: float = 0,
    width: float = 100,
    height: float = 100,
    name: str = "Ellipse",
    fill_color: str = "#B1B2B5",
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create an ellipse (or circle if width==height) on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        x: X position (default 0).
        y: Y position (default 0).
        width: Width in pixels (default 100).
        height: Height in pixels (default 100).
        name: Shape name (default "Ellipse").
        fill_color: Fill color hex (default "#B1B2B5").
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Optional stroke color hex.
        stroke_width: Stroke width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_ellipse as _create

    result = await _create(
        file_id,
        page_id,
        x,
        y,
        width,
        height,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_text(
    file_id: str,
    page_id: str,
    text: str = "Text",
    x: float = 0,
    y: float = 0,
    width: float | None = None,
    height: float | None = None,
    name: str | None = None,
    font_family: str = "sourcesanspro",
    font_size: int = 16,
    font_weight: str = "400",
    font_style: str = "normal",
    fill_color: str = "#000000",
    fill_opacity: float = 1.0,
    text_align: str = "left",
    line_height: float = 1.2,
    letter_spacing: float = 0,
    text_decoration: str = "none",
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create a text shape on a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        text: Text content (default "Text").
        x: X position (default 0).
        y: Y position (default 0).
        width: Text box width (auto-calculated if omitted).
        height: Text box height (auto-calculated if omitted).
        name: Shape name (defaults to text content).
        font_family: Font family (default "sourcesanspro").
        font_size: Font size in pixels (default 16).
        font_weight: Weight — "400"=normal, "700"=bold (default "400").
        font_style: Style — "normal" or "italic" (default "normal").
        fill_color: Text color hex (default "#000000").
        fill_opacity: Text opacity 0-1 (default 1.0).
        text_align: Alignment — "left"/"center"/"right"/"justify" (default "left").
        line_height: Line height multiplier (default 1.2).
        letter_spacing: Letter spacing pixels (default 0).
        text_decoration: "none"/"underline"/"line-through" (default "none").
        opacity: Overall shape opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_text as _create

    result = await _create(
        file_id,
        page_id,
        text,
        x,
        y,
        width,
        height,
        name,
        font_family,
        font_size,
        font_weight,
        font_style,
        fill_color,
        fill_opacity,
        text_align,
        line_height,
        letter_spacing,
        text_decoration,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_path(
    file_id: str,
    page_id: str,
    segments: list[dict],
    name: str = "Path",
    fill_color: str | None = None,
    fill_opacity: float = 1.0,
    stroke_color: str = "#000000",
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    parent_id: str | None = None,
) -> str:
    """Create a vector path shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        segments: Path segments — list of {command, x, y} dicts. Commands: "M"=move, "L"=line, "C"=curve (with c1x,c1y,c2x,c2y), "Z"=close.
        name: Shape name (default "Path").
        fill_color: Optional fill color hex.
        fill_opacity: Fill opacity 0-1 (default 1.0).
        stroke_color: Stroke color hex (default "#000000").
        stroke_width: Stroke width (default 1.0).
        opacity: Overall opacity 0-1 (default 1.0).
        parent_id: Parent shape ID. If omitted, adds to root.
    """
    from penpot_mcp.tools.create import create_path as _create

    result = await _create(
        file_id,
        page_id,
        segments,
        name,
        fill_color,
        fill_opacity,
        stroke_color,
        stroke_width,
        opacity,
        parent_id,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_group(
    file_id: str,
    page_id: str,
    shape_ids: list[str],
    name: str = "Group",
    parent_id: str | None = None,
) -> str:
    """Group existing shapes together.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_ids: List of shape UUIDs to group.
        name: Group name (default "Group").
        parent_id: Parent shape ID. If omitted, uses root.
    """
    from penpot_mcp.tools.create import create_group as _create

    result = await _create(file_id, page_id, shape_ids, name, parent_id)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_component(
    file_id: str,
    page_id: str,
    shape_id: str,
    name: str | None = None,
) -> str:
    """Convert a shape/frame into a reusable component.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID to convert.
        name: Component name (keeps current name if omitted).
    """
    from penpot_mcp.tools.create import create_component as _create

    result = await _create(file_id, page_id, shape_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def create_component_instance(
    file_id: str,
    page_id: str,
    component_id: str,
    x: float,
    y: float,
    parent_id: str | None = None,
) -> str:
    """Instantiate a component (place it on the canvas).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        component_id: The component asset UUID to instantiate.
        x: X coordinate for the instance.
        y: Y coordinate for the instance.
        parent_id: Optional parent frame/group UUID.
    """
    from penpot_mcp.tools.create import create_component_instance as _create

    result = await _create(file_id, page_id, component_id, x, y, parent_id)
    return json.dumps(result, indent=2, default=str)



@_legacy_tool()
async def create_page(file_id: str, name: str = "New Page") -> str:
    """Add a new page to a file.

    Args:
        file_id: The file UUID.
        name: Page name (default "New Page").
    """
    from penpot_mcp.tools.create import create_page as _create

    result = await _create(file_id, name)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 10: Shape Modification
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def modify_shape(file_id: str, page_id: str, shape_id: str, attrs: dict) -> str:
    """Modify arbitrary attributes of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        attrs: Dict of kebab-case attributes to set. E.g. {"opacity": 0.5, "name": "New Name"}.
    """
    from penpot_mcp.tools.modify import modify_shape as _modify

    result = await _modify(file_id, page_id, shape_id, attrs)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def move_shape(
    file_id: str, page_id: str, shape_id: str, x: float, y: float
) -> str:
    """Move a shape to a new position.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        x: New X position.
        y: New Y position.
    """
    from penpot_mcp.tools.modify import move_shape as _move

    result = await _move(file_id, page_id, shape_id, x, y)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def resize_shape(
    file_id: str, page_id: str, shape_id: str, width: float, height: float
) -> str:
    """Resize a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        width: New width in pixels.
        height: New height in pixels.
    """
    from penpot_mcp.tools.modify import resize_shape as _resize

    result = await _resize(file_id, page_id, shape_id, width, height)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def delete_shape(file_id: str, page_id: str, shape_id: str) -> str:
    """Delete a shape from a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
    """
    from penpot_mcp.tools.modify import delete_shape as _delete

    result = await _delete(file_id, page_id, shape_id)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def rename_shape(file_id: str, page_id: str, shape_id: str, name: str) -> str:
    """Rename a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        name: New name.
    """
    from penpot_mcp.tools.modify import rename_shape as _rename

    result = await _rename(file_id, page_id, shape_id, name)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_fill(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#B1B2B5",
    opacity: float = 1.0,
) -> str:
    """Set the fill color of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Fill color hex (default "#B1B2B5").
        opacity: Fill opacity 0-1 (default 1.0).
    """
    from penpot_mcp.tools.modify import set_fill as _set_fill

    result = await _set_fill(file_id, page_id, shape_id, color, opacity)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_stroke(
    file_id: str,
    page_id: str,
    shape_id: str,
    color: str = "#000000",
    width: float = 1.0,
    opacity: float = 1.0,
    style: str = "solid",
) -> str:
    """Set the stroke (border) of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        color: Stroke color hex (default "#000000").
        width: Stroke width pixels (default 1.0).
        opacity: Stroke opacity 0-1 (default 1.0).
        style: "solid"/"dashed"/"dotted"/"mixed" (default "solid").
    """
    from penpot_mcp.tools.modify import set_stroke as _set_stroke

    result = await _set_stroke(file_id, page_id, shape_id, color, width, opacity, style)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_opacity(file_id: str, page_id: str, shape_id: str, opacity: float) -> str:
    """Set the overall opacity of a shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The shape UUID.
        opacity: Opacity 0 (transparent) to 1 (opaque).
    """
    from penpot_mcp.tools.modify import set_opacity as _set_opacity

    result = await _set_opacity(file_id, page_id, shape_id, opacity)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_layout(
    file_id: str,
    page_id: str,
    frame_id: str,
    layout_type: str = "flex",
    direction: str = "row",
    gap: float = 0,
    padding: float = 0,
    align_items: str | None = None,
    justify_content: str | None = None,
    wrap: str = "nowrap",
) -> str:
    """Set flex/grid layout on a frame (auto-layout container).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        frame_id: The frame shape UUID.
        layout_type: "flex" or "grid" (default "flex").
        direction: "row"/"column"/"row-reverse"/"column-reverse" (default "row").
        gap: Gap between children pixels (default 0).
        padding: Padding on all sides pixels (default 0).
        align_items: Cross-axis — "start"/"center"/"end"/"stretch".
        justify_content: Main-axis — "start"/"center"/"end"/"space-between"/"space-around"/"space-evenly".
        wrap: "nowrap" or "wrap" (default "nowrap").
    """
    from penpot_mcp.tools.modify import set_layout as _set_layout

    result = await _set_layout(
        file_id,
        page_id,
        frame_id,
        layout_type,
        direction,
        gap,
        padding,
        align_items,
        justify_content,
        wrap,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def reorder_shapes(
    file_id: str,
    page_id: str,
    parent_id: str,
    shape_ids: list[str],
    index: int = 0,
) -> str:
    """Reorder shapes within a parent (z-order).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        parent_id: Parent shape/frame UUID.
        shape_ids: Shape UUIDs to move.
        index: Target index (0=bottom, default 0).
    """
    from penpot_mcp.tools.modify import reorder_shapes as _reorder

    result = await _reorder(file_id, page_id, parent_id, shape_ids, index)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def delete_page(file_id: str, page_id: str) -> str:
    """Delete a page from a file.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
    """
    from penpot_mcp.tools.modify import delete_page as _delete

    result = await _delete(file_id, page_id)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def rename_page(file_id: str, page_id: str, name: str) -> str:
    """Rename a page.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        name: New name.
    """
    from penpot_mcp.tools.modify import rename_page as _rename

    result = await _rename(file_id, page_id, name)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Category 10: Interactive Mode (WebSocket)
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def get_active_selection() -> str:
    """Get the UUIDs of the shapes currently selected by the user in the Penpot Plugin.

    This requires the user to have the Penpot MCP Plugin open and connected in their browser.
    """
    from penpot_mcp.gateway import gateway

    if not gateway.is_interactive:
        return json.dumps(
            {
                "error": "No active Penpot Plugin connection found. The user must have the plugin open."
            },
            indent=2,
        )

    return json.dumps({"selected_shape_ids": gateway.active_selection}, indent=2)


@_legacy_tool()
async def execute_plugin_script(script: str) -> str:
    """Execute a JavaScript snippet directly within the user's Penpot Plugin environment.

    This allows direct manipulation of the canvas using the Penpot Plugin API.

    Args:
        script: The JavaScript code to execute.
    """
    from penpot_mcp.ws_controller import ws_controller

    if not ws_controller.is_connected:
        return json.dumps(
            {"error": "No active Penpot Plugin connection found."}, indent=2
        )

    success = await ws_controller.send_command(script)
    if success:
        return json.dumps(
            {"status": "Script executed (or broadcasted) successfully."}, indent=2
        )
    else:
        return json.dumps({"error": "Failed to send script."}, indent=2)


# ═══════════════════════════════════════════════════════════════
# Category 11: Text Operations
# ═══════════════════════════════════════════════════════════════


@_legacy_tool()
async def set_text_content(
    file_id: str,
    page_id: str,
    shape_id: str,
    text: str,
    font_family: str | None = None,
    font_size: int | None = None,
    font_weight: str | None = None,
    fill_color: str | None = None,
    text_align: str | None = None,
) -> str:
    """Replace text content of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        text: New text content.
        font_family: Optional font family override.
        font_size: Optional font size override.
        font_weight: Optional font weight override.
        fill_color: Optional text color override (hex).
        text_align: Optional alignment override.
    """
    from penpot_mcp.tools.text import set_text_content as _set

    result = await _set(
        file_id,
        page_id,
        shape_id,
        text,
        font_family,
        font_size,
        font_weight,
        fill_color,
        text_align,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_font(file_id: str, page_id: str, shape_id: str, font_family: str) -> str:
    """Change the font family of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_family: Font family name (e.g., "sourcesanspro", "roboto").
    """
    from penpot_mcp.tools.text import set_font as _set

    result = await _set(file_id, page_id, shape_id, font_family)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_font_size(
    file_id: str, page_id: str, shape_id: str, font_size: int
) -> str:
    """Change the font size of a text shape.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_size: Font size in pixels.
    """
    from penpot_mcp.tools.text import set_font_size as _set

    result = await _set(file_id, page_id, shape_id, font_size)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_text_align(file_id: str, page_id: str, shape_id: str, align: str) -> str:
    """Set text alignment.

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        align: "left"/"center"/"right"/"justify".
    """
    from penpot_mcp.tools.text import set_text_align as _set

    result = await _set(file_id, page_id, shape_id, align)
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def set_text_style(
    file_id: str,
    page_id: str,
    shape_id: str,
    font_weight: str | None = None,
    font_style: str | None = None,
    text_decoration: str | None = None,
) -> str:
    """Set text styling (bold, italic, underline).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_id: The text shape UUID.
        font_weight: "400"=normal, "700"=bold, etc.
        font_style: "normal" or "italic".
        text_decoration: "none"/"underline"/"line-through".
    """
    from penpot_mcp.tools.text import set_text_style as _set

    result = await _set(
        file_id,
        page_id,
        shape_id,
        font_weight,
        font_style,
        text_decoration,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def apply_design_token(
    file_id: str,
    page_id: str,
    shape_ids: list[str],
    token_id: str,
    token_type: str,
    target_property: str = "fill",
) -> str:
    """Apply a design token (color asset or typography asset) to a list of shapes.

    Args:
        file_id: The file UUID.
        page_id: The page UUID containing the shapes.
        shape_ids: List of shape UUIDs to modify.
        token_id: The UUID of the color or typography library token.
        token_type: Token type - "color" or "typography".
        target_property: Target property - "fill" or "stroke" (applies only to color tokens).
    """
    from penpot_mcp.tools.tokens import apply_design_token as _apply

    result = await _apply(
        file_id,
        page_id,
        shape_ids,
        token_id,
        token_type,
        target_property,
    )
    return json.dumps(result, indent=2, default=str)


@_legacy_tool()
async def auto_bind_library_tokens(
    file_id: str,
    page_id: str,
    shape_ids: list[str] | None = None,
) -> str:
    """Automatically search shape styles/colors and bind matching library assets (color or typography tokens).

    Args:
        file_id: The file UUID.
        page_id: The page UUID.
        shape_ids: Optional list of shape UUIDs. If omitted, binds all shapes on the page.
    """
    from penpot_mcp.tools.tokens import auto_bind_library_tokens as _bind

    result = await _bind(file_id, page_id, shape_ids)
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# AEP Compressed Polymorphic Sub-Tools & Mode Registration
# ═══════════════════════════════════════════════════════════════

from penpot_mcp.tools.subtools import COMPRESSED_SUBTOOLS  # noqa: E402


def configure_mcp_tools(mode: str | None = None) -> int:
    """Configure registered MCP tools according to MCP_TOOL_MODE ('compressed' | 'legacy' | 'hybrid')."""
    eff_mode = (mode or settings.mcp_tool_mode or "compressed").strip().lower()
    mcp._tool_manager._tools.clear()

    if eff_mode in ("compressed", "hybrid"):
        for sub_fn in COMPRESSED_SUBTOOLS:
            mcp.add_tool(sub_fn)

    if eff_mode in ("legacy", "hybrid"):
        for leg_fn in _LEGACY_TOOL_FNS:
            mcp.add_tool(leg_fn)

    if not mcp._tool_manager._tools:
        for sub_fn in COMPRESSED_SUBTOOLS:
            mcp.add_tool(sub_fn)

    return len(mcp._tool_manager._tools)


configure_mcp_tools(settings.mcp_tool_mode)


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


def main():
    """Run the Penpot MCP server."""
    import anyio
    import uvicorn

    logging.basicConfig(
        level=getattr(logging, settings.mcp_log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    async def run():
        from starlette.routing import WebSocketRoute
        from penpot_mcp.ws_controller import ws_controller

        starlette_app = mcp.streamable_http_app()
        starlette_app.router.routes.append(
            WebSocketRoute("/plugin/ws", ws_controller.handle_starlette_ws)
        )
        starlette_app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.mcp_allowed_hosts,
        )
        _orig_lifespan = starlette_app.router.lifespan_context

        @asynccontextmanager
        async def _startup_lifespan(app):
            logger.info("Connecting to Penpot services...")
            await db.connect()
            await api.connect()
            await ws_controller.start()
            logger.info("Penpot MCP server ready (WS on port %d and /plugin/ws)", settings.ws_port)
            try:
                async with _orig_lifespan(app):
                    yield
            finally:
                await ws_controller.stop()
                await api.close()
                await db.close()
                logger.info("Penpot MCP server stopped")

        starlette_app.router.lifespan_context = _startup_lifespan

        config = uvicorn.Config(
            starlette_app,
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level=settings.mcp_log_level.lower(),
        )
        await uvicorn.Server(config).serve()

    anyio.run(run)


if __name__ == "__main__":
    main()
