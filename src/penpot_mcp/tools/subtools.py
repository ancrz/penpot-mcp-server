"""Domain Branch Toolsets & Code-Mode Batch Optimization for Penpot MCP.

Groups the 77 Penpot capabilities into 14 domain-level branch tools so AI clients
can treat each domain tool as a primary toolset router (`action`), combined with
pure in-memory MCP optimization primitives:
  - `verbosity` ('summary' | 'detail' | 'brief' | 'terse')
  - `select_fields` server-side dot-path field projection
  - In-memory `SessionPool` state caching (`session_id`, `store_as`)
  - `playbook_run(action='batch'|'code')` single-turn multi-step execution with
    `$prev.*`, `$steps.<id>.*`, and `$cache.<key>.*` variable piping.
"""

from __future__ import annotations

from collections.abc import Callable
import inspect
import json
from typing import Any

from penpot_mcp.aep_engine import (
    GLOBAL_SESSION_POOL,
    RequestTelemetry,
    ToposGuard,
    attach_envelope,
    hydrate_struct,
    normalize_tool_result,
    project_fields,
    resolve_step_refs,
)
from penpot_mcp.config import settings
from penpot_mcp.services.api import api
from penpot_mcp.services.db import db

# In-memory store for custom named playbooks created during a session
_PLAYBOOK_STORE: dict[str, dict[str, Any]] = {}

# Built-in tactical playbooks / recipes for discover(action="playbook")
_TACTICAL_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "WF-1: Create Screen & Auto-Layout": {
        "description": "Create a parent frame, configure flex auto-layout, and insert child elements in 1 turn via playbook_run(action='batch').",
        "canonical_call": (
            "playbook_run(action='batch', return_only='last', steps=["
            "{'id': 'frame', 'tool': 'create', 'params': {'action': 'frame', 'file_id': '<uuid>', 'page_id': '<uuid>', 'name': 'Card', 'width': 360, 'height': 240}}, "
            "{'id': 'layout', 'tool': 'modify', 'depends_on': ['frame'], 'params': {'action': 'layout', 'file_id': '<uuid>', 'page_id': '<uuid>', 'frame_id': '$steps.frame.shape_id', 'layout_type': 'flex', 'direction': 'column', 'gap': 12, 'padding': 16}}, "
            "{'id': 'title', 'tool': 'create', 'depends_on': ['layout'], 'params': {'action': 'text', 'file_id': '<uuid>', 'page_id': '<uuid>', 'parent_id': '$steps.frame.shape_id', 'text': 'Welcome', 'font_size': 20, 'font_weight': '700'}}"
            "])"
        ),
    },
    "WF-2: Design System Token Binding": {
        "description": "Inspect design tokens in a file and automatically bind matching library colors/typographies to page shapes.",
        "canonical_call": (
            "playbook_run(action='batch', steps=["
            "{'id': 'tokens', 'tool': 'assets', 'params': {'action': 'design_tokens', 'file_id': '<uuid>'}}, "
            "{'id': 'bind', 'tool': 'assets', 'depends_on': ['tokens'], 'params': {'action': 'auto_bind_tokens', 'file_id': '<uuid>', 'page_id': '<uuid>'}}"
            "])"
        ),
    },
    "WF-3: Design-to-Code CSS & SVG Extraction": {
        "description": "Search for a target component/frame and extract both its CSS rules and SVG markup in a single batch.",
        "canonical_call": (
            "playbook_run(action='batch', steps=["
            "{'id': 'css', 'tool': 'shapes', 'params': {'action': 'css', 'file_id': '<uuid>', 'page_id': '<uuid>', 'shape_id': '<uuid>'}}, "
            "{'id': 'svg', 'tool': 'shapes', 'params': {'action': 'svg', 'file_id': '<uuid>', 'page_id': '<uuid>', 'shape_id': '<uuid>'}}"
            "])"
        ),
    },
}

# Domain Schema Catalog (maps the 14 compressed tools -> 77 actions)
SUBTOOLS_CATALOG: dict[str, dict[str, Any]] = {
    "discover": {
        "domain": "Orientation, Capability Manifest & Schema Reflection",
        "actions": ["discover", "capabilities", "playbook", "schema"],
        "description": "Orientation router: maps natural-language use cases to domain toolsets, returns capability readiness with in-memory caching, workflow playbooks, or tool schemas.",
    },
    "projects": {
        "domain": "Projects & Teams",
        "actions": [
            "list_teams",
            "list_projects",
            "create_project",
            "list_files",
            "search_files",
            "team_members",
            "team_fonts",
        ],
        "description": "List teams, team members, custom team fonts, list/create projects, list project files, and search files across projects.",
    },
    "files": {
        "domain": "File Lifecycle, History & Snapshots",
        "actions": [
            "summary",
            "pages",
            "history",
            "libraries",
            "share_links",
            "live_presence",
            "create",
            "rename",
            "duplicate",
            "delete",
            "raw_data",
            "compare_revisions",
            "create_snapshot",
            "list_snapshots",
        ],
        "description": "Manage Penpot design files, pages metadata, revision history, share links, live presence, diffs, raw Transit data, and version snapshots.",
    },
    "shapes": {
        "domain": "Shape Reading, Hierarchy & Code Gen",
        "actions": ["list", "tree", "details", "search", "css", "svg"],
        "description": "Read page objects, hierarchical shape trees, full shape details, search shapes, and generate CSS or SVG.",
    },
    "create": {
        "domain": "Shape, Component & Page Creation",
        "actions": [
            "rectangle",
            "frame",
            "ellipse",
            "text",
            "path",
            "group",
            "component",
            "component_instance",
            "page",
        ],
        "description": "Create frames, rectangles, ellipses, text nodes, vector paths, groups, components, component instances, and pages.",
    },
    "modify": {
        "domain": "Shape, Layout & Page Modification",
        "actions": [
            "attrs",
            "move",
            "resize",
            "delete",
            "rename",
            "fill",
            "stroke",
            "opacity",
            "layout",
            "reorder",
            "delete_page",
            "rename_page",
        ],
        "description": "Modify shape attributes, geometry, fills, strokes, opacity, flex/grid auto-layout, z-order, and pages.",
    },
    "text": {
        "domain": "Text Content & Typography Styling",
        "actions": ["set_content", "set_font", "set_font_size", "set_align", "set_style"],
        "description": "Update text content, font family, font size, alignment, and weight/italic/decoration styles.",
    },
    "assets": {
        "domain": "Design System Assets & Tokens",
        "actions": [
            "components",
            "design_tokens",
            "list_colors",
            "create_color",
            "update_color",
            "delete_color",
            "list_typographies",
            "create_typography",
            "update_typography",
            "delete_typography",
            "apply_token",
            "auto_bind_tokens",
        ],
        "description": "Manage library components, design tokens, color assets (solid/gradient), typography assets, and token binding.",
    },
    "comments": {
        "domain": "Comments, Presence & Collaboration",
        "actions": ["list", "active_users", "share_links", "create", "reply", "resolve"],
        "description": "Read/create comment threads, reply, resolve threads, inspect live active users, and list share links.",
    },
    "media": {
        "domain": "Media Assets & Custom Fonts",
        "actions": ["list_assets", "list_fonts", "upload"],
        "description": "List file media images, list custom team fonts, and upload media from validated external URLs (SSRF-guarded).",
    },
    "export": {
        "domain": "Frame & Shape Rendering Export",
        "actions": ["png", "svg"],
        "description": "Export frames or shapes as rendered PNG (base64) or SVG via Penpot exporter with local SVG fallback.",
    },
    "system": {
        "domain": "Database, Webhooks & Profile",
        "actions": ["query_db", "webhooks", "profile"],
        "description": "Execute read-only SQL SELECT queries, inspect team webhooks, and retrieve authenticated user profile.",
    },
    "plugin": {
        "domain": "Interactive Browser Plugin Bridge & Embedded AI Studio Gateway",
        "actions": [
            "selection",
            "context",
            "status",
            "execute_script",
            "focus",
            "agent_chat",
        ],
        "description": "Read live user canvas selection/context, execute correlated JS in the Penpot browser plugin, focus shapes, or invoke the embedded SDK Agent Pipeline.",
    },
    "playbook_run": {
        "domain": "DAG Orchestration & Ephemeral Batch / Code Mode",
        "actions": ["batch", "code", "run", "create", "list", "dag_audit"],
        "description": "Execute multi-step Penpot pipelines in a single turn with $prev/$steps/$cache piping, Topos DAG validation, and field projection.",
    },
}


def _store_if_requested(
    payload: dict[str, Any],
    session_id: str | None,
    store_as: str | None,
    agent_id: str = "default",
) -> None:
    if store_as and store_as.strip() and payload.get("status") != "ERROR":
        eff_sid = (session_id or "default").strip()
        GLOBAL_SESSION_POOL.cache_set(eff_sid, agent_id, store_as.strip(), payload)


def _invalid_action_response(
    tool_name: str,
    action: str,
    valid_actions: list[str],
    tracker: RequestTelemetry,
    verbosity: str,
) -> str:
    clean_act = (action or "").strip().lower()
    if clean_act in ("help", "actions", "list_actions", "schema"):
        meta = SUBTOOLS_CATALOG.get(tool_name, {})
        return attach_envelope(
            {
                "status": "OK",
                "tool": tool_name,
                "domain": meta.get("domain", tool_name),
                "description": meta.get("description", ""),
                "available_actions": valid_actions,
            },
            tracker,
            verbosity=verbosity,
            summary=f"Toolset branch '{tool_name}' exposes {len(valid_actions)} actions.",
        )
    return attach_envelope(
        {
            "status": "INVALID_ACTION",
            "tool": tool_name,
            "error": f"Unrecognized action {action!r} for tool '{tool_name}'. Valid actions: {valid_actions}",
            "valid_actions": valid_actions,
        },
        tracker,
        verbosity=verbosity,
    )


def _missing_param_response(
    tool_name: str,
    action: str,
    missing: list[str],
    tracker: RequestTelemetry,
    verbosity: str,
) -> str:
    return attach_envelope(
        {
            "status": "PARAM_ERROR",
            "tool": tool_name,
            "action": action,
            "error": f"Action '{action}' on '{tool_name}' requires parameter(s): {', '.join(missing)}",
            "required_params": missing,
        },
        tracker,
        verbosity=verbosity,
    )


# ═══════════════════════════════════════════════════════════════
# 1. discover — Orientation, Capabilities, Playbooks & Schema
# ═══════════════════════════════════════════════════════════════


async def discover(
    action: str = "discover",
    use_case: str = "",
    query: str = "",
    target: str = "",
    limit: int = 5,
    verbosity: str = "summary",
    session_id: str | None = None,
    store_as: str | None = None,
    force_refresh: bool = False,
    select_fields: list[str] | None = None,
) -> str:
    """Orientation, capability manifest, workflow playbooks, and schema reflection for Penpot MCP.

    Actions: 'discover' | 'capabilities' | 'playbook' | 'schema'.
    """
    act = (action or "discover").strip().lower()
    tracker = RequestTelemetry("discover", act)
    eff_sid = (session_id or "global").strip()
    v_mode = (verbosity or "summary").strip().lower()

    if act in ("capabilities", "version", "status"):
        cache_key = "discover_capabilities"
        cached = GLOBAL_SESSION_POOL.cache_get(eff_sid, "default", cache_key)
        if cached.get("found") and not force_refresh and isinstance(cached.get("value"), dict):
            manifest = dict(cached["value"])
            cache_status = "CACHED"
            source = "ram_session_cache"
        else:
            from penpot_mcp.ws_controller import ws_controller

            manifest = {
                "status": "OK",
                "server_version": settings.penpot_mcp_version,
                "mcp_protocol_version": settings.mcp_protocol_version,
                "tool_mode": settings.mcp_tool_mode,
                "compressed_tools_count": len(SUBTOOLS_CATALOG),
                "underlying_actions_count": 77,
                "readiness": {
                    "db_pool_connected": db._pool is not None,
                    "api_client_connected": api._client is not None,
                    "has_access_token": settings.has_access_token,
                    "has_credentials": settings.has_credentials,
                    "ws_enabled": settings.ws_enabled,
                    "ws_plugin_connected": ws_controller.is_connected,
                },
                "security_posture": {
                    "dns_rebinding_protection": True,
                    "ssrf_guard": "strict" if not settings.ssrf_allow_private_media else "allow_private",
                    "db_user": settings.penpot_db_user,
                    "db_statement_timeout_ms": settings.db_statement_timeout_ms,
                },
                "domains": {
                    k: {"actions": v["actions"], "action_count": len(v["actions"])}
                    for k, v in SUBTOOLS_CATALOG.items()
                },
            }
            GLOBAL_SESSION_POOL.cache_set(eff_sid, "default", cache_key, manifest)
            cache_status = "FRESH"
            source = "system"

        _store_if_requested(manifest, eff_sid, store_as)

        if v_mode in ("terse", "cached"):
            terse_payload: dict[str, Any] = {
                "status": "OK",
                "cache_status": cache_status,
                "source": source,
                "session_id": eff_sid,
                "server_version": manifest["server_version"],
                "tool_mode": manifest["tool_mode"],
                "compressed_tools_count": manifest["compressed_tools_count"],
                "underlying_actions_count": manifest["underlying_actions_count"],
                "readiness": manifest["readiness"],
                "token_savings_pct": 95.0,
            }
            return attach_envelope(
                terse_payload,
                tracker,
                verbosity=verbosity,
                select_fields=select_fields,
                summary=f"Capabilities loaded ({cache_status}, 14 sub-tools / 77 actions).",
            )

        manifest["cache_status"] = cache_status
        manifest["source"] = source
        manifest["session_id"] = eff_sid
        return attach_envelope(
            manifest,
            tracker,
            verbosity=verbosity,
            select_fields=select_fields,
            summary=f"Capability manifest loaded ({source}, {len(SUBTOOLS_CATALOG)} sub-tools / 77 actions).",
        )

    if act in ("playbook", "tactical_guide", "recipes"):
        payload: dict[str, Any] = {
            "status": "OK",
            "action": "playbook",
            "playbooks": _TACTICAL_PLAYBOOKS,
            "custom_playbooks": list(_PLAYBOOK_STORE.keys()),
            "batch_piping_syntax": {
                "prev_step": "$prev.<field>",
                "named_step": "$steps.<step_id>.<field>",
                "ram_cache": "$cache.<store_as_key>.<field>",
                "template_var": "${var_name}",
            },
        }
        _store_if_requested(payload, eff_sid, store_as)
        return attach_envelope(
            payload,
            tracker,
            verbosity=verbosity,
            select_fields=select_fields,
            summary=f"Loaded {len(_TACTICAL_PLAYBOOKS)} tactical UI playbooks.",
        )

    if act in ("schema", "tools_schema"):
        tgt = (target or query or use_case or "").strip().lower()
        if tgt and tgt in SUBTOOLS_CATALOG:
            schema_payload: dict[str, Any] = {
                "status": "OK",
                "tool": tgt,
                "schema": SUBTOOLS_CATALOG[tgt],
            }
        else:
            schema_payload = {
                "status": "OK",
                "tools_count": len(SUBTOOLS_CATALOG),
                "underlying_actions_count": 77,
                "tools": SUBTOOLS_CATALOG,
            }
        _store_if_requested(schema_payload, eff_sid, store_as)
        return attach_envelope(
            schema_payload,
            tracker,
            verbosity=verbosity,
            select_fields=select_fields,
            summary="Tool schema catalog returned.",
        )

    if act in ("discover", "match", "route", "search", "recommend"):
        q = (use_case or query or target or "").strip().lower()
        tokens = [t for t in q.replace("_", " ").replace("-", " ").split() if t]
        matches: list[dict[str, Any]] = []
        for tname, meta in SUBTOOLS_CATALOG.items():
            haystack = f"{tname} {meta['domain']} {meta['description']} {' '.join(meta['actions'])}".lower()
            score = sum(3 if tok in tname or tok in meta["actions"] else (1 if tok in haystack else 0) for tok in tokens)
            if score > 0 or not tokens:
                matched_actions = [a for a in meta["actions"] if any(tok in a for tok in tokens)] or meta["actions"][:3]
                matches.append(
                    {
                        "tool": tname,
                        "domain": meta["domain"],
                        "score": score,
                        "recommended_actions": matched_actions,
                        "canonical_call": f"{tname}(action='{matched_actions[0]}', verbosity='summary')",
                    }
                )
        matches.sort(key=lambda m: int(m["score"]), reverse=True)
        eff_limit = max(1, int(limit or 5))
        res_payload: dict[str, Any] = {
            "status": "OK",
            "query": q,
            "matches": matches[:eff_limit],
            "total_matches": len(matches),
        }
        _store_if_requested(res_payload, eff_sid, store_as)
        return attach_envelope(
            res_payload,
            tracker,
            verbosity=verbosity,
            select_fields=select_fields,
            summary=f"Routed query {q!r} to {len(matches[:eff_limit])} recommended sub-tools.",
        )

    return _invalid_action_response(
        "discover",
        action,
        ["discover", "capabilities", "playbook", "schema"],
        tracker,
        verbosity,
    )


# ═══════════════════════════════════════════════════════════════
# 2. projects — Teams, Projects & File Discovery (5 actions)
# ═══════════════════════════════════════════════════════════════


async def projects(
    action: str = "list_projects",
    team_id: str | None = None,
    project_id: str | None = None,
    name: str | None = None,
    query: str | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for Teams, Projects, and cross-project file search.

    Route via action: 'list_teams' | 'list_projects' | 'create_project' | 'list_files' | 'search_files' (or 'help').
    """
    from penpot_mcp.tools import files as _files_mod
    from penpot_mcp.tools import projects as _proj_mod

    act = (action or "list_projects").strip().lower()
    tracker = RequestTelemetry("projects", act)
    valid = [
        "list_teams",
        "list_projects",
        "create_project",
        "list_files",
        "search_files",
        "team_members",
        "team_fonts",
    ]

    try:
        if act in ("list_teams", "teams"):
            raw: Any = await _proj_mod.list_teams()
        elif act in ("list_projects", "projects", "list"):
            raw = await _proj_mod.list_projects(team_id)
        elif act in ("create_project", "create"):
            if not team_id or not name:
                return _missing_param_response("projects", act, ["team_id", "name"], tracker, verbosity)
            raw = await _files_mod.create_project(team_id, name)
        elif act in ("list_files", "files"):
            if not project_id:
                return _missing_param_response("projects", act, ["project_id"], tracker, verbosity)
            raw = await _proj_mod.list_files(project_id)
        elif act in ("search_files", "search"):
            if not query:
                return _missing_param_response("projects", act, ["query"], tracker, verbosity)
            raw = await _proj_mod.search_files(query)
        elif act in ("team_members", "members"):
            if not team_id:
                return _missing_param_response("projects", act, ["team_id"], tracker, verbosity)
            raw = await db.list_team_members(team_id)
        elif act in ("team_fonts", "fonts"):
            if not team_id:
                return _missing_param_response("projects", act, ["team_id"], tracker, verbosity)
            raw = await db.list_team_fonts(team_id)
        else:
            return _invalid_action_response("projects", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 3. files — File Operations, History, Diffs & Snapshots (12 actions)
# ═══════════════════════════════════════════════════════════════


async def files(
    action: str = "summary",
    file_id: str | None = None,
    project_id: str | None = None,
    page_id: str | None = None,
    name: str | None = None,
    label: str | None = None,
    limit: int = 20,
    revn_from: int | None = None,
    revn_to: int | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for File CRUD, pages, history, snapshots, and revision diffs.

    Route via action: 'summary' | 'pages' | 'history' | 'libraries' | 'create' | 'rename' | 'duplicate' | 'delete' | 'raw_data' | 'compare_revisions' | 'create_snapshot' | 'list_snapshots' (or 'help').
    """
    from penpot_mcp.tools import advanced as _adv_mod
    from penpot_mcp.tools import files as _files_mod

    act = (action or "summary").strip().lower()
    tracker = RequestTelemetry("files", act)
    valid = [
        "summary",
        "pages",
        "history",
        "libraries",
        "share_links",
        "live_presence",
        "create",
        "rename",
        "duplicate",
        "delete",
        "raw_data",
        "compare_revisions",
        "create_snapshot",
        "list_snapshots",
    ]

    try:
        if act in ("summary", "get_file_summary"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw: Any = await _files_mod.get_file_summary(file_id)
        elif act in ("pages", "get_file_pages"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _files_mod.get_file_pages(file_id)
        elif act in ("history", "get_file_history"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _files_mod.get_file_history(file_id, limit)
        elif act in ("libraries", "get_file_libraries"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _files_mod.get_file_libraries(file_id)
        elif act in ("share_links", "shares"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await db.list_file_share_links(file_id)
        elif act in ("live_presence", "presence"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await db.list_file_presence(file_id)
        elif act in ("create", "create_file"):
            if not project_id or not name:
                return _missing_param_response("files", act, ["project_id", "name"], tracker, verbosity)
            raw = await _files_mod.create_file(project_id, name)
        elif act in ("rename", "rename_file"):
            if not file_id or not name:
                return _missing_param_response("files", act, ["file_id", "name"], tracker, verbosity)
            raw = await _files_mod.rename_file(file_id, name)
        elif act in ("duplicate", "duplicate_file"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _files_mod.duplicate_file(file_id, name)
        elif act in ("delete", "delete_file"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _files_mod.delete_file(file_id)
        elif act in ("raw_data", "get_file_raw_data"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await _adv_mod.get_file_raw_data(file_id, page_id)
        elif act in ("compare_revisions", "diff"):
            if not file_id or revn_from is None:
                return _missing_param_response("files", act, ["file_id", "revn_from"], tracker, verbosity)
            raw = await _adv_mod.compare_revisions(file_id, revn_from, revn_to)
        elif act in ("create_snapshot", "snapshot"):
            if not file_id or not label:
                return _missing_param_response("files", act, ["file_id", "label"], tracker, verbosity)
            raw = await api.create_snapshot(file_id, label)
        elif act in ("list_snapshots", "get_snapshots", "snapshots"):
            if not file_id:
                return _missing_param_response("files", act, ["file_id"], tracker, verbosity)
            raw = await api.get_snapshots(file_id)
        else:
            return _invalid_action_response("files", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 4. shapes — Shape Reading, Tree, Search, CSS & SVG (6 actions)
# ═══════════════════════════════════════════════════════════════


async def shapes(
    action: str = "list",
    file_id: str | None = None,
    page_id: str | None = None,
    shape_id: str | None = None,
    root_id: str | None = None,
    shape_type: str | None = None,
    query: str | None = None,
    search_type: str = "name",
    depth: int = 3,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for reading page objects, shape trees, details, search, CSS, and SVG.

    Route via action: 'list' | 'tree' | 'details' | 'search' | 'css' | 'svg' (or 'help').
    """
    from penpot_mcp.gateway import gateway
    from penpot_mcp.tools import shapes as _shapes_mod
    from penpot_mcp.transformers.css import shape_to_css_string
    from penpot_mcp.transformers.svg import shape_to_svg

    act = (action or "list").strip().lower()
    tracker = RequestTelemetry("shapes", act)
    valid = ["list", "tree", "details", "search", "css", "svg"]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("shapes", act, valid, tracker, verbosity)
    if not file_id or not page_id:
        return _missing_param_response("shapes", act, ["file_id", "page_id"], tracker, verbosity)

    try:
        if act in ("list", "get_page_objects", "objects"):
            raw: Any = await gateway.execute_intent(
                "get_page_objects", file_id=file_id, page_id=page_id, shape_type=shape_type
            )
        elif act in ("tree", "get_shape_tree"):
            raw = await gateway.execute_intent(
                "get_shape_tree", file_id=file_id, page_id=page_id, root_id=root_id, depth=depth
            )
        elif act in ("details", "get_shape_details"):
            if not shape_id:
                return _missing_param_response("shapes", act, ["shape_id"], tracker, verbosity)
            raw = await _shapes_mod.get_shape_details(file_id, page_id, shape_id)
        elif act in ("search", "search_shapes"):
            if not query:
                return _missing_param_response("shapes", act, ["query"], tracker, verbosity)
            raw = await _shapes_mod.search_shapes(file_id, page_id, query, search_type)
        elif act in ("css", "get_shape_css"):
            if not shape_id:
                return _missing_param_response("shapes", act, ["shape_id"], tracker, verbosity)
            shape = await _shapes_mod.get_shape_details(file_id, page_id, shape_id)
            if "error" in shape:
                raw = shape
            else:
                raw = {"shape_id": shape_id, "name": shape.get("name"), "css": shape_to_css_string(shape)}
        elif act in ("svg", "get_shape_svg"):
            if not shape_id:
                return _missing_param_response("shapes", act, ["shape_id"], tracker, verbosity)
            shape = await _shapes_mod.get_shape_details(file_id, page_id, shape_id)
            if "error" in shape:
                raw = shape
            else:
                raw = {"shape_id": shape_id, "name": shape.get("name"), "svg": shape_to_svg(shape)}
        else:
            return _invalid_action_response("shapes", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 5. create — Shape, Component, Instance & Page Creation (9 actions)
# ═══════════════════════════════════════════════════════════════


async def create(
    action: str = "rectangle",
    file_id: str | None = None,
    page_id: str | None = None,
    x: float = 0,
    y: float = 0,
    width: float | None = None,
    height: float | None = None,
    name: str | None = None,
    fill_color: str | None = None,
    fill_opacity: float = 1.0,
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    border_radius: float = 0,
    clip_content: bool = True,
    parent_id: str | None = None,
    text: str = "Text",
    font_family: str = "sourcesanspro",
    font_size: int = 16,
    font_weight: str = "400",
    font_style: str = "normal",
    text_align: str = "left",
    line_height: float = 1.2,
    letter_spacing: float = 0,
    text_decoration: str = "none",
    segments: list[dict[str, Any]] | None = None,
    shape_ids: list[str] | None = None,
    shape_id: str | None = None,
    component_id: str | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for creating shapes, frames, paths, groups, components, instances, and pages.

    Route via action: 'rectangle' | 'frame' | 'ellipse' | 'text' | 'path' | 'group' | 'component' | 'component_instance' | 'page' (or 'help').
    """
    from penpot_mcp.tools import create as _create_mod

    act = (action or "rectangle").strip().lower()
    tracker = RequestTelemetry("create", act)
    valid = [
        "rectangle",
        "frame",
        "ellipse",
        "text",
        "path",
        "group",
        "component",
        "component_instance",
        "page",
    ]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("create", act, valid, tracker, verbosity)
    if not file_id:
        return _missing_param_response("create", act, ["file_id"], tracker, verbosity)

    try:
        if act in ("page", "create_page"):
            raw: Any = await _create_mod.create_page(file_id, name or "New Page")
        else:
            if not page_id:
                return _missing_param_response("create", act, ["page_id"], tracker, verbosity)

            if act in ("rectangle", "rect", "create_rectangle"):
                raw = await _create_mod.create_rectangle(
                    file_id=file_id,
                    page_id=page_id,
                    x=x,
                    y=y,
                    width=width if width is not None else 100,
                    height=height if height is not None else 100,
                    name=name or "Rectangle",
                    fill_color=fill_color if fill_color is not None else "#B1B2B5",
                    fill_opacity=fill_opacity,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    opacity=opacity,
                    border_radius=border_radius,
                    parent_id=parent_id,
                )
            elif act in ("frame", "create_frame"):
                raw = await _create_mod.create_frame(
                    file_id=file_id,
                    page_id=page_id,
                    x=x,
                    y=y,
                    width=width if width is not None else 300,
                    height=height if height is not None else 300,
                    name=name or "Frame",
                    fill_color=fill_color if fill_color is not None else "#FFFFFF",
                    fill_opacity=fill_opacity,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    opacity=opacity,
                    border_radius=border_radius,
                    clip_content=clip_content,
                    parent_id=parent_id,
                )
            elif act in ("ellipse", "circle", "create_ellipse"):
                raw = await _create_mod.create_ellipse(
                    file_id=file_id,
                    page_id=page_id,
                    x=x,
                    y=y,
                    width=width if width is not None else 100,
                    height=height if height is not None else 100,
                    name=name or "Ellipse",
                    fill_color=fill_color if fill_color is not None else "#B1B2B5",
                    fill_opacity=fill_opacity,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    opacity=opacity,
                    parent_id=parent_id,
                )
            elif act in ("text", "create_text"):
                raw = await _create_mod.create_text(
                    file_id=file_id,
                    page_id=page_id,
                    text=text,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    name=name,
                    font_family=font_family,
                    font_size=font_size,
                    font_weight=font_weight,
                    font_style=font_style,
                    fill_color=fill_color if fill_color is not None else "#000000",
                    fill_opacity=fill_opacity,
                    text_align=text_align,
                    line_height=line_height,
                    letter_spacing=letter_spacing,
                    text_decoration=text_decoration,
                    opacity=opacity,
                    parent_id=parent_id,
                )
            elif act in ("path", "create_path"):
                if not segments:
                    return _missing_param_response("create", act, ["segments"], tracker, verbosity)
                raw = await _create_mod.create_path(
                    file_id=file_id,
                    page_id=page_id,
                    segments=segments,
                    name=name or "Path",
                    fill_color=fill_color,
                    fill_opacity=fill_opacity,
                    stroke_color=stroke_color if stroke_color is not None else "#000000",
                    stroke_width=stroke_width,
                    opacity=opacity,
                    parent_id=parent_id,
                )
            elif act in ("group", "create_group"):
                if not shape_ids:
                    return _missing_param_response("create", act, ["shape_ids"], tracker, verbosity)
                raw = await _create_mod.create_group(
                    file_id=file_id,
                    page_id=page_id,
                    shape_ids=shape_ids,
                    name=name or "Group",
                    parent_id=parent_id,
                )
            elif act in ("component", "create_component"):
                if not shape_id:
                    return _missing_param_response("create", act, ["shape_id"], tracker, verbosity)
                raw = await _create_mod.create_component(
                    file_id=file_id,
                    page_id=page_id,
                    shape_id=shape_id,
                    name=name,
                )
            elif act in ("component_instance", "instance", "create_component_instance"):
                if not component_id:
                    return _missing_param_response("create", act, ["component_id"], tracker, verbosity)
                raw = await _create_mod.create_component_instance(
                    file_id=file_id,
                    page_id=page_id,
                    component_id=component_id,
                    x=x,
                    y=y,
                    parent_id=parent_id,
                )
            else:
                return _invalid_action_response("create", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 6. modify — Shape, Layout & Page Modification (12 actions)
# ═══════════════════════════════════════════════════════════════


async def modify(
    action: str = "attrs",
    file_id: str | None = None,
    page_id: str | None = None,
    shape_id: str | None = None,
    frame_id: str | None = None,
    parent_id: str | None = None,
    shape_ids: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    name: str | None = None,
    color: str = "#B1B2B5",
    opacity: float = 1.0,
    style: str = "solid",
    layout_type: str = "flex",
    direction: str = "row",
    gap: float = 0,
    padding: float = 0,
    align_items: str | None = None,
    justify_content: str | None = None,
    wrap: str = "nowrap",
    index: int = 0,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for modifying shapes, geometry, fills, strokes, flex/grid layout, and pages.

    Route via action: 'attrs' | 'move' | 'resize' | 'delete' | 'rename' | 'fill' | 'stroke' | 'opacity' | 'layout' | 'reorder' | 'delete_page' | 'rename_page' (or 'help').
    """
    from penpot_mcp.tools import modify as _mod

    act = (action or "attrs").strip().lower()
    tracker = RequestTelemetry("modify", act)
    valid = [
        "attrs",
        "move",
        "resize",
        "delete",
        "rename",
        "fill",
        "stroke",
        "opacity",
        "layout",
        "reorder",
        "delete_page",
        "rename_page",
    ]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("modify", act, valid, tracker, verbosity)
    if not file_id or not page_id:
        return _missing_param_response("modify", act, ["file_id", "page_id"], tracker, verbosity)

    try:
        if act in ("attrs", "modify_shape"):
            if not shape_id or not attrs:
                return _missing_param_response("modify", act, ["shape_id", "attrs"], tracker, verbosity)
            raw: Any = await _mod.modify_shape(file_id, page_id, shape_id, attrs)
        elif act in ("move", "move_shape"):
            if not shape_id or x is None or y is None:
                return _missing_param_response("modify", act, ["shape_id", "x", "y"], tracker, verbosity)
            raw = await _mod.move_shape(file_id, page_id, shape_id, x, y)
        elif act in ("resize", "resize_shape"):
            if not shape_id or width is None or height is None:
                return _missing_param_response("modify", act, ["shape_id", "width", "height"], tracker, verbosity)
            raw = await _mod.resize_shape(file_id, page_id, shape_id, width, height)
        elif act in ("delete", "delete_shape"):
            if not shape_id:
                return _missing_param_response("modify", act, ["shape_id"], tracker, verbosity)
            raw = await _mod.delete_shape(file_id, page_id, shape_id)
        elif act in ("rename", "rename_shape"):
            if not shape_id or not name:
                return _missing_param_response("modify", act, ["shape_id", "name"], tracker, verbosity)
            raw = await _mod.rename_shape(file_id, page_id, shape_id, name)
        elif act in ("fill", "set_fill"):
            if not shape_id:
                return _missing_param_response("modify", act, ["shape_id"], tracker, verbosity)
            raw = await _mod.set_fill(file_id, page_id, shape_id, color, opacity)
        elif act in ("stroke", "set_stroke"):
            if not shape_id:
                return _missing_param_response("modify", act, ["shape_id"], tracker, verbosity)
            raw = await _mod.set_stroke(
                file_id,
                page_id,
                shape_id,
                color if color != "#B1B2B5" else "#000000",
                width if width is not None else 1.0,
                opacity,
                style,
            )
        elif act in ("opacity", "set_opacity"):
            if not shape_id:
                return _missing_param_response("modify", act, ["shape_id"], tracker, verbosity)
            raw = await _mod.set_opacity(file_id, page_id, shape_id, opacity)
        elif act in ("layout", "set_layout"):
            eff_frame_id = frame_id or shape_id
            if not eff_frame_id:
                return _missing_param_response("modify", act, ["frame_id"], tracker, verbosity)
            raw = await _mod.set_layout(
                file_id,
                page_id,
                eff_frame_id,
                layout_type,
                direction,
                gap,
                padding,
                align_items,
                justify_content,
                wrap,
            )
        elif act in ("reorder", "reorder_shapes"):
            if not parent_id or not shape_ids:
                return _missing_param_response("modify", act, ["parent_id", "shape_ids"], tracker, verbosity)
            raw = await _mod.reorder_shapes(file_id, page_id, parent_id, shape_ids, index)
        elif act in ("delete_page",):
            raw = await _mod.delete_page(file_id, page_id)
        elif act in ("rename_page",):
            if not name:
                return _missing_param_response("modify", act, ["name"], tracker, verbosity)
            raw = await _mod.rename_page(file_id, page_id, name)
        else:
            return _invalid_action_response("modify", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 7. text — Text Content & Typography Overrides (5 actions)
# ═══════════════════════════════════════════════════════════════


async def text(
    action: str = "set_content",
    file_id: str | None = None,
    page_id: str | None = None,
    shape_id: str | None = None,
    text_content: str | None = None,
    text: str | None = None,
    font_family: str | None = None,
    font_size: int | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    fill_color: str | None = None,
    text_align: str | None = None,
    align: str | None = None,
    text_decoration: str | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for text content, font family, font size, alignment, and text styling.

    Route via action: 'set_content' | 'set_font' | 'set_font_size' | 'set_align' | 'set_style' (or 'help').
    """
    from penpot_mcp.tools import text as _text_mod

    act = (action or "set_content").strip().lower()
    tracker = RequestTelemetry("text", act)
    valid = ["set_content", "set_font", "set_font_size", "set_align", "set_style"]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("text", act, valid, tracker, verbosity)
    if not file_id or not page_id or not shape_id:
        return _missing_param_response("text", act, ["file_id", "page_id", "shape_id"], tracker, verbosity)

    eff_text = text_content if text_content is not None else text
    eff_align = align or text_align

    try:
        if act in ("set_content", "set_text_content", "content"):
            if eff_text is None:
                return _missing_param_response("text", act, ["text"], tracker, verbosity)
            raw: Any = await _text_mod.set_text_content(
                file_id,
                page_id,
                shape_id,
                eff_text,
                font_family,
                font_size,
                font_weight,
                fill_color,
                eff_align,
            )
        elif act in ("set_font", "font"):
            if not font_family:
                return _missing_param_response("text", act, ["font_family"], tracker, verbosity)
            raw = await _text_mod.set_font(file_id, page_id, shape_id, font_family)
        elif act in ("set_font_size", "font_size", "size"):
            if font_size is None:
                return _missing_param_response("text", act, ["font_size"], tracker, verbosity)
            raw = await _text_mod.set_font_size(file_id, page_id, shape_id, font_size)
        elif act in ("set_align", "set_text_align", "align"):
            if not eff_align:
                return _missing_param_response("text", act, ["align"], tracker, verbosity)
            raw = await _text_mod.set_text_align(file_id, page_id, shape_id, eff_align)
        elif act in ("set_style", "set_text_style", "style"):
            raw = await _text_mod.set_text_style(
                file_id, page_id, shape_id, font_weight, font_style, text_decoration
            )
        else:
            return _invalid_action_response("text", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 8. assets — Components, Colors, Typographies & Design Tokens (12 actions)
# ═══════════════════════════════════════════════════════════════


async def assets(
    action: str = "design_tokens",
    file_id: str | None = None,
    page_id: str | None = None,
    name: str | None = None,
    color_id: str | None = None,
    color: str | None = None,
    opacity: float | None = None,
    path: str | None = None,
    gradient: dict[str, Any] | None = None,
    typography_id: str | None = None,
    font_id: str | None = None,
    font_family: str | None = None,
    font_variant_id: str | None = None,
    font_size: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    line_height: str | None = None,
    letter_spacing: str | None = None,
    text_transform: str | None = None,
    shape_ids: list[str] | None = None,
    token_id: str | None = None,
    token_type: str | None = None,
    target_property: str = "fill",
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for Design System assets: components, colors, typographies, and token binding.

    Route via action: 'components' | 'design_tokens' | 'list_colors' | 'create_color' | 'update_color' | 'delete_color' | 'list_typographies' | 'create_typography' | 'update_typography' | 'delete_typography' | 'apply_token' | 'auto_bind_tokens' (or 'help').
    """
    from penpot_mcp.tools import colors as _colors_mod
    from penpot_mcp.tools import components as _comp_mod
    from penpot_mcp.tools import tokens as _tokens_mod
    from penpot_mcp.tools import typographies as _typo_mod

    act = (action or "design_tokens").strip().lower()
    tracker = RequestTelemetry("assets", act)
    valid = [
        "components",
        "design_tokens",
        "list_colors",
        "create_color",
        "update_color",
        "delete_color",
        "list_typographies",
        "create_typography",
        "update_typography",
        "delete_typography",
        "apply_token",
        "auto_bind_tokens",
    ]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("assets", act, valid, tracker, verbosity)
    if not file_id:
        return _missing_param_response("assets", act, ["file_id"], tracker, verbosity)

    try:
        if act in ("components", "get_component_instances"):
            raw: Any = await _comp_mod.get_component_instances(file_id)
        elif act in ("design_tokens", "get_design_tokens", "tokens"):
            raw = await _comp_mod.get_design_tokens(file_id)
        elif act in ("list_colors", "get_colors_library", "colors"):
            raw = await _comp_mod.get_colors_library(file_id)
        elif act in ("create_color",):
            if not name:
                return _missing_param_response("assets", act, ["name"], tracker, verbosity)
            raw = await _colors_mod.create_color(
                file_id,
                name,
                color,
                opacity if opacity is not None else 1.0,
                path if path is not None else "",
                gradient,
            )
        elif act in ("update_color",):
            if not color_id:
                return _missing_param_response("assets", act, ["color_id"], tracker, verbosity)
            raw = await _colors_mod.update_color(
                file_id, color_id, name, color, opacity, path, gradient
            )
        elif act in ("delete_color",):
            if not color_id:
                return _missing_param_response("assets", act, ["color_id"], tracker, verbosity)
            raw = await _colors_mod.delete_color(file_id, color_id)
        elif act in ("list_typographies", "get_typography_library", "typographies"):
            raw = await _comp_mod.get_typography_library(file_id)
        elif act in ("create_typography",):
            if not name:
                return _missing_param_response("assets", act, ["name"], tracker, verbosity)
            raw = await _typo_mod.create_typography(
                file_id=file_id,
                name=name,
                font_id=font_id or "sourcesanspro",
                font_family=font_family or "sourcesanspro",
                font_variant_id=font_variant_id or "regular",
                font_size=font_size or "14",
                font_weight=font_weight or "480",
                font_style=font_style or "normal",
                line_height=line_height or "1.2",
                letter_spacing=letter_spacing or "0",
                text_transform=text_transform or "none",
                path=path if path is not None else "",
            )
        elif act in ("update_typography",):
            if not typography_id:
                return _missing_param_response("assets", act, ["typography_id"], tracker, verbosity)
            raw = await _typo_mod.update_typography(
                file_id=file_id,
                typography_id=typography_id,
                name=name,
                font_id=font_id,
                font_family=font_family,
                font_variant_id=font_variant_id,
                font_size=font_size,
                font_weight=font_weight,
                font_style=font_style,
                line_height=line_height,
                letter_spacing=letter_spacing,
                text_transform=text_transform,
                path=path,
            )
        elif act in ("delete_typography",):
            if not typography_id:
                return _missing_param_response("assets", act, ["typography_id"], tracker, verbosity)
            raw = await _typo_mod.delete_typography(file_id, typography_id)
        elif act in ("apply_token", "apply_design_token"):
            if not page_id or not shape_ids or not token_id or not token_type:
                return _missing_param_response(
                    "assets", act, ["page_id", "shape_ids", "token_id", "token_type"], tracker, verbosity
                )
            raw = await _tokens_mod.apply_design_token(
                file_id, page_id, shape_ids, token_id, token_type, target_property
            )
        elif act in ("auto_bind_tokens", "auto_bind_library_tokens"):
            if not page_id:
                return _missing_param_response("assets", act, ["page_id"], tracker, verbosity)
            raw = await _tokens_mod.auto_bind_library_tokens(file_id, page_id, shape_ids)
        else:
            return _invalid_action_response("assets", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 9. comments — Comments, Active Users & Share Links (6 actions)
# ═══════════════════════════════════════════════════════════════


async def comments(
    action: str = "list",
    file_id: str | None = None,
    page_id: str | None = None,
    thread_id: str | None = None,
    content: str | None = None,
    x: float | None = None,
    y: float | None = None,
    frame_id: str | None = None,
    resolved: bool | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for comments, replies, thread resolution, live presence, and share links.

    Route via action: 'list' | 'active_users' | 'share_links' | 'create' | 'reply' | 'resolve' (or 'help').
    """
    from penpot_mcp.tools import comments as _comm_mod

    act = (action or "list").strip().lower()
    tracker = RequestTelemetry("comments", act)
    valid = ["list", "active_users", "share_links", "create", "reply", "resolve"]

    try:
        if act in ("list", "get_comments"):
            if not file_id:
                return _missing_param_response("comments", act, ["file_id"], tracker, verbosity)
            raw: Any = await _comm_mod.get_comments(file_id, resolved)
        elif act in ("active_users", "get_active_users"):
            if not file_id:
                return _missing_param_response("comments", act, ["file_id"], tracker, verbosity)
            raw = await _comm_mod.get_active_users(file_id)
        elif act in ("share_links", "get_share_links"):
            if not file_id:
                return _missing_param_response("comments", act, ["file_id"], tracker, verbosity)
            raw = await _comm_mod.get_share_links(file_id)
        elif act in ("create", "create_comment"):
            if not file_id or not page_id or not content or x is None or y is None:
                return _missing_param_response(
                    "comments", act, ["file_id", "page_id", "content", "x", "y"], tracker, verbosity
                )
            raw = await _comm_mod.create_comment(file_id, page_id, content, x, y, frame_id)
        elif act in ("reply", "reply_to_comment"):
            if not thread_id or not content:
                return _missing_param_response("comments", act, ["thread_id", "content"], tracker, verbosity)
            raw = await _comm_mod.reply_to_comment(thread_id, content)
        elif act in ("resolve", "resolve_comment"):
            if not thread_id:
                return _missing_param_response("comments", act, ["thread_id"], tracker, verbosity)
            raw = await _comm_mod.resolve_comment(thread_id, True if resolved is None else resolved)
        else:
            return _invalid_action_response("comments", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 10. media — Media Assets & Custom Fonts (3 actions)
# ═══════════════════════════════════════════════════════════════


async def media(
    action: str = "list_assets",
    file_id: str | None = None,
    team_id: str | None = None,
    name: str | None = None,
    url: str | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for file media assets, team custom fonts, and SSRF-guarded media upload.

    Route via action: 'list_assets' | 'list_fonts' | 'upload' (or 'help').
    """
    from penpot_mcp.tools import media as _media_mod

    act = (action or "list_assets").strip().lower()
    tracker = RequestTelemetry("media", act)
    valid = ["list_assets", "list_fonts", "upload"]

    try:
        if act in ("list_assets", "list_media_assets", "assets"):
            if not file_id:
                return _missing_param_response("media", act, ["file_id"], tracker, verbosity)
            raw: Any = await _media_mod.list_media_assets(file_id)
        elif act in ("list_fonts", "fonts"):
            if not team_id:
                return _missing_param_response("media", act, ["team_id"], tracker, verbosity)
            raw = await _media_mod.list_fonts(team_id)
        elif act in ("upload", "upload_media"):
            if not file_id or not name or not url:
                return _missing_param_response("media", act, ["file_id", "name", "url"], tracker, verbosity)
            raw = await _media_mod.upload_media(file_id, name, url)
        else:
            return _invalid_action_response("media", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 11. export — Frame & Shape Export to PNG / SVG (2 actions)
# ═══════════════════════════════════════════════════════════════


async def export(
    action: str = "png",
    file_id: str | None = None,
    page_id: str | None = None,
    object_id: str | None = None,
    scale: float = 1.0,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for exporting frames/shapes to PNG or SVG.

    Route via action: 'png' | 'svg' (or 'help').
    """
    from penpot_mcp.tools import export as _exp_mod

    act = (action or "png").strip().lower()
    tracker = RequestTelemetry("export", act)
    valid = ["png", "svg"]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("export", act, valid, tracker, verbosity)
    if not file_id or not page_id or not object_id:
        return _missing_param_response("export", act, ["file_id", "page_id", "object_id"], tracker, verbosity)

    try:
        if act in ("png", "export_frame_png"):
            raw: Any = await _exp_mod.export_frame_png(file_id, page_id, object_id, scale)
        elif act in ("svg", "export_frame_svg"):
            raw = await _exp_mod.export_frame_svg(file_id, page_id, object_id)
        else:
            return _invalid_action_response("export", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 12. system — Read-Only Database SQL, Webhooks & Profile (3 actions)
# ═══════════════════════════════════════════════════════════════


async def system(
    action: str = "profile",
    sql: str | None = None,
    team_id: str | None = None,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for read-only PostgreSQL SELECT queries, team webhooks, and user profile.

    Route via action: 'query_db' | 'webhooks' | 'profile' (or 'help').
    """
    from penpot_mcp.tools import database as _db_mod

    act = (action or "profile").strip().lower()
    tracker = RequestTelemetry("system", act)
    valid = ["query_db", "webhooks", "profile", "audit_log", "cache_stats"]

    try:
        if act in ("query_db", "query_database", "sql"):
            if not sql:
                return _missing_param_response("system", act, ["sql"], tracker, verbosity)
            raw: Any = await _db_mod.query_database(sql)
        elif act in ("webhooks", "get_webhooks"):
            if not team_id:
                return _missing_param_response("system", act, ["team_id"], tracker, verbosity)
            raw = await _db_mod.get_webhooks(team_id)
        elif act in ("profile", "get_profile"):
            raw = await api.get_profile()
        elif act in ("audit_log", "audit"):
            raw = await db.list_audit_logs()
        elif act in ("cache_stats", "stats"):
            raw = {
                "valkey": db.cache.stats(),
                "pgvector": await db.get_pgvector_status(),
            }
        else:
            return _invalid_action_response("system", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 13. plugin — Interactive Browser Plugin Bridge & AI Gateway (6 actions)
# ═══════════════════════════════════════════════════════════════


async def plugin(
    action: str = "selection",
    script: str | None = None,
    prompt: str | None = None,
    quick_action: str | None = None,
    file_id: str | None = None,
    page_id: str | None = None,
    shape_ids: list[str] | None = None,
    await_result: bool = True,
    timeout: float = 12.0,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    store_as: str | None = None,
    session_id: str | None = None,
) -> str:
    """Branch toolset for live browser plugin selection, correlated JS evaluation, viewport focus, and AI Studio chat.

    Route via action: 'selection' | 'context' | 'status' | 'execute_script' | 'focus' | 'agent_chat' (or 'help').
    """
    from dataclasses import asdict
    from penpot_mcp.agent_gateway import agent_gateway
    from penpot_mcp.gateway import gateway

    act = (action or "selection").strip().lower()
    tracker = RequestTelemetry("plugin", act)
    valid = [
        "selection",
        "context",
        "status",
        "execute_script",
        "focus",
        "agent_chat",
    ]

    if act in ("help", "actions", "list_actions", "schema"):
        return _invalid_action_response("plugin", act, valid, tracker, verbosity)

    try:
        if act in ("selection", "get_active_selection"):
            raw: dict[str, Any] = await gateway.execute_intent("get_active_selection")
        elif act in ("context", "get_live_context"):
            raw = await gateway.execute_intent(
                "get_live_context", file_id=file_id, page_id=page_id
            )
        elif act in ("status", "gateway_status"):
            raw = agent_gateway.get_status()
        elif act in ("execute_script", "script"):
            if not script:
                return _missing_param_response("plugin", act, ["script"], tracker, verbosity)
            raw = await gateway.execute_intent(
                "execute_plugin_script",
                script=script,
                await_result=await_result,
                timeout=timeout,
            )
        elif act in ("focus", "focus_shapes"):
            raw = await gateway.execute_intent("focus_shapes", shape_ids=shape_ids)
        elif act in ("agent_chat", "chat"):
            if not prompt and not quick_action:
                return _missing_param_response("plugin", act, ["prompt"], tracker, verbosity)
            turn = await agent_gateway.chat(
                prompt or quick_action or "",
                file_id=file_id,
                page_id=page_id,
                quick_action=quick_action,
            )
            raw = asdict(turn)
        else:
            return _invalid_action_response("plugin", action, valid, tracker, verbosity)

        payload = normalize_tool_result(raw, act)
        _store_if_requested(payload, session_id, store_as)
        return attach_envelope(payload, tracker, verbosity=verbosity, select_fields=select_fields)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


# ═══════════════════════════════════════════════════════════════
# 14. playbook_run — Ephemeral Batch / Code Mode & DAG Playbooks
# ═══════════════════════════════════════════════════════════════

_PLAYBOOK_FAIL_STATUSES = frozenset({"ERROR", "INVALID_ACTION", "PARAM_ERROR", "BLOCKED", "NOT_ALLOWED"})


def _build_batch_dispatch() -> dict[str, Any]:
    """Return dispatch map supporting both the 13 domain sub-tools AND all 77 legacy tool names."""
    return {
        # 13 compressed sub-tools
        "discover": discover,
        "projects": projects,
        "files": files,
        "shapes": shapes,
        "create": create,
        "modify": modify,
        "text": text,
        "assets": assets,
        "comments": comments,
        "media": media,
        "export": export,
        "system": system,
        "plugin": plugin,
        # 77 legacy tool aliases mapped to sub-tools for 100% compatibility in code/batch mode
        "list_teams": lambda **kw: projects(action="list_teams", **kw),
        "list_projects": lambda **kw: projects(action="list_projects", **kw),
        "create_project": lambda **kw: projects(action="create_project", **kw),
        "list_files": lambda **kw: projects(action="list_files", **kw),
        "search_files": lambda **kw: projects(action="search_files", **kw),
        "get_file_summary": lambda **kw: files(action="summary", **kw),
        "get_file_pages": lambda **kw: files(action="pages", **kw),
        "get_file_history": lambda **kw: files(action="history", **kw),
        "get_file_libraries": lambda **kw: files(action="libraries", **kw),
        "create_file": lambda **kw: files(action="create", **kw),
        "rename_file": lambda **kw: files(action="rename", **kw),
        "duplicate_file": lambda **kw: files(action="duplicate", **kw),
        "delete_file": lambda **kw: files(action="delete", **kw),
        "get_file_raw_data": lambda **kw: files(action="raw_data", **kw),
        "compare_revisions": lambda **kw: files(action="compare_revisions", **kw),
        "create_snapshot": lambda **kw: files(action="create_snapshot", **kw),
        "get_snapshots": lambda **kw: files(action="list_snapshots", **kw),
        "get_page_objects": lambda **kw: shapes(action="list", **kw),
        "get_shape_tree": lambda **kw: shapes(action="tree", **kw),
        "get_shape_details": lambda **kw: shapes(action="details", **kw),
        "search_shapes": lambda **kw: shapes(action="search", **kw),
        "get_shape_css": lambda **kw: shapes(action="css", **kw),
        "get_shape_svg": lambda **kw: shapes(action="svg", **kw),
        "create_rectangle": lambda **kw: create(action="rectangle", **kw),
        "create_frame": lambda **kw: create(action="frame", **kw),
        "create_ellipse": lambda **kw: create(action="ellipse", **kw),
        "create_text": lambda **kw: create(action="text", **kw),
        "create_path": lambda **kw: create(action="path", **kw),
        "create_group": lambda **kw: create(action="group", **kw),
        "create_component": lambda **kw: create(action="component", **kw),
        "create_component_instance": lambda **kw: create(action="component_instance", **kw),
        "create_page": lambda **kw: create(action="page", **kw),
        "modify_shape": lambda **kw: modify(action="attrs", **kw),
        "move_shape": lambda **kw: modify(action="move", **kw),
        "resize_shape": lambda **kw: modify(action="resize", **kw),
        "delete_shape": lambda **kw: modify(action="delete", **kw),
        "rename_shape": lambda **kw: modify(action="rename", **kw),
        "set_fill": lambda **kw: modify(action="fill", **kw),
        "set_stroke": lambda **kw: modify(action="stroke", **kw),
        "set_opacity": lambda **kw: modify(action="opacity", **kw),
        "set_layout": lambda **kw: modify(action="layout", **kw),
        "reorder_shapes": lambda **kw: modify(action="reorder", **kw),
        "delete_page": lambda **kw: modify(action="delete_page", **kw),
        "rename_page": lambda **kw: modify(action="rename_page", **kw),
        "set_text_content": lambda **kw: text(action="set_content", **kw),
        "set_font": lambda **kw: text(action="set_font", **kw),
        "set_font_size": lambda **kw: text(action="set_font_size", **kw),
        "set_text_align": lambda **kw: text(action="set_align", **kw),
        "set_text_style": lambda **kw: text(action="set_style", **kw),
        "get_component_instances": lambda **kw: assets(action="components", **kw),
        "get_design_tokens": lambda **kw: assets(action="design_tokens", **kw),
        "get_colors_library": lambda **kw: assets(action="list_colors", **kw),
        "create_color": lambda **kw: assets(action="create_color", **kw),
        "update_color": lambda **kw: assets(action="update_color", **kw),
        "delete_color": lambda **kw: assets(action="delete_color", **kw),
        "get_typography_library": lambda **kw: assets(action="list_typographies", **kw),
        "create_typography": lambda **kw: assets(action="create_typography", **kw),
        "update_typography": lambda **kw: assets(action="update_typography", **kw),
        "delete_typography": lambda **kw: assets(action="delete_typography", **kw),
        "apply_design_token": lambda **kw: assets(action="apply_token", **kw),
        "auto_bind_library_tokens": lambda **kw: assets(action="auto_bind_tokens", **kw),
        "get_comments": lambda **kw: comments(action="list", **kw),
        "get_active_users": lambda **kw: comments(action="active_users", **kw),
        "get_share_links": lambda **kw: comments(action="share_links", **kw),
        "create_comment": lambda **kw: comments(action="create", **kw),
        "reply_to_comment": lambda **kw: comments(action="reply", **kw),
        "resolve_comment": lambda **kw: comments(action="resolve", **kw),
        "list_media_assets": lambda **kw: media(action="list_assets", **kw),
        "list_fonts": lambda **kw: media(action="list_fonts", **kw),
        "upload_media": lambda **kw: media(action="upload", **kw),
        "export_frame_png": lambda **kw: export(action="png", **kw),
        "export_frame_svg": lambda **kw: export(action="svg", **kw),
        "query_database": lambda **kw: system(action="query_db", **kw),
        "get_webhooks": lambda **kw: system(action="webhooks", **kw),
        "get_profile": lambda **kw: system(action="profile", **kw),
        "get_active_selection": lambda **kw: plugin(action="selection", **kw),
        "execute_plugin_script": lambda **kw: plugin(action="execute_script", **kw),
    }


async def _execute_ordered_steps(
    ordered_steps: list[dict[str, Any]],
    *,
    batch_name: str,
    eff_sid: str,
    eff_agent: str,
    stop_on_error: bool,
    return_only: str,
    select_fields: list[str] | None,
    verbosity: str,
    tracker: RequestTelemetry,
) -> str:
    slot = GLOBAL_SESSION_POOL.acquire(session_id=eff_sid, agent_id=eff_agent)
    dispatch = _build_batch_dispatch()
    step_outputs: dict[str, Any] = {}
    prev_output: Any = None
    results: list[dict[str, Any]] = []
    all_ok = True
    slot_cache_dict = dict(
        GLOBAL_SESSION_POOL.cache_get(session_id=eff_sid, agent_id=eff_agent).get("cache", {})
    )

    try:
        for step in ordered_steps:
            sid = str(step.get("id", f"s{len(results) + 1}"))
            tool = str(step.get("tool", ""))
            step_params = dict(step.get("params") or {})

            # Resolve variable piping ($prev.*, $steps.<id>.*, $cache.<key>.*)
            step_params = resolve_step_refs(
                step_params, step_outputs, prev_output, slot_cache=slot_cache_dict
            )

            fn = dispatch.get(tool)
            if fn is None:
                results.append(
                    {
                        "id": sid,
                        "tool": tool,
                        "status": "NOT_ALLOWED",
                        "ok": False,
                        "output": {"error": f"Unknown or disallowed tool in batch: {tool!r}"},
                    }
                )
                all_ok = False
                if stop_on_error:
                    break
                continue

            try:
                sig = inspect.signature(fn)
                has_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if not has_var_kw:
                    step_params = {k: v for k, v in step_params.items() if k in sig.parameters}
                if "verbosity" not in step_params and (
                    "verbosity" in sig.parameters or has_var_kw
                ):
                    step_params["verbosity"] = "summary"

                raw = fn(**step_params)
                if inspect.iscoroutine(raw):
                    raw = await raw
                out = json.loads(raw) if isinstance(raw, str) else raw
                st = out.get("status", "OK") if isinstance(out, dict) else "OK"
                if isinstance(out, dict):
                    out.pop("envelope", None)
            except Exception as exc:
                st, out = "ERROR", {"status": "ERROR", "error": str(exc)}

            ok = st not in _PLAYBOOK_FAIL_STATUSES and not (
                isinstance(out, dict) and "error" in out and st == "ERROR"
            )
            step_res = {"id": sid, "tool": tool, "status": st, "ok": ok, "output": out}
            results.append(step_res)
            step_outputs[sid] = out
            prev_output = out

            if ok:
                GLOBAL_SESSION_POOL.cache_set(eff_sid, eff_agent, sid, out)
                slot_cache_dict[sid] = out
                if step.get("store_as"):
                    k_as = str(step["store_as"]).strip()
                    if k_as:
                        GLOBAL_SESSION_POOL.cache_set(eff_sid, eff_agent, k_as, out)
                        slot_cache_dict[k_as] = out
            else:
                all_ok = False
                if stop_on_error:
                    break
    finally:
        GLOBAL_SESSION_POOL.release(session_id=eff_sid, agent_id=eff_agent, clear_state=False)

    ret_mode = (return_only or "all").strip().lower()
    base_res: dict[str, Any] = {
        "status": "OK" if all_ok else "PARTIAL",
        "batch": batch_name,
        "all_ok": all_ok,
        "steps_run": len(results),
    }

    failed_steps = [
        {
            "id": r["id"],
            "tool": r["tool"],
            "status": r["status"],
            "error": (
                (r.get("output") or {}).get("error")
                if isinstance(r.get("output"), dict)
                else str(r.get("output"))
            ),
        }
        for r in results
        if not r.get("ok")
    ]
    if failed_steps:
        base_res["failed_steps"] = failed_steps

    if ret_mode == "last":
        base_res["output"] = prev_output
    elif ret_mode == "summary":
        base_res["steps_summary"] = [
            {"id": r["id"], "tool": r["tool"], "status": r["status"], "ok": r["ok"]}
            for r in results
        ]
    else:
        base_res["results"] = results

    base_res["session_cache"] = {
        "slot_id": slot["slot_id"],
        "session_id": eff_sid,
        "reuse_count": slot["reuse_count"],
        "cached_keys": GLOBAL_SESSION_POOL.cache_get(
            session_id=eff_sid, agent_id=eff_agent
        ).get("keys", []),
    }

    full_bytes = len(json.dumps(base_res, ensure_ascii=False, default=str).encode("utf-8"))
    if select_fields and isinstance(select_fields, list):
        projected_res = project_fields(base_res, select_fields)
        if isinstance(projected_res, dict):
            projected_res.setdefault("status", base_res["status"])
            if failed_steps and "failed_steps" not in projected_res:
                projected_res["failed_steps"] = failed_steps
            proj_bytes = len(
                json.dumps(projected_res, ensure_ascii=False, default=str).encode("utf-8")
            )
            saved_bytes = max(0, full_bytes - proj_bytes)
            projected_res["token_savings_estimate"] = {
                "full_bytes": full_bytes,
                "projected_bytes": proj_bytes,
                "saved_bytes": saved_bytes,
                "savings_pct": round((saved_bytes / full_bytes * 100), 2)
                if full_bytes > 0
                else 0.0,
                "turns_saved": max(0, len(results) - 1),
            }
            final_res = projected_res
        else:
            final_res = base_res
    else:
        base_res["token_savings_estimate"] = {
            "full_bytes": full_bytes,
            "projected_bytes": full_bytes,
            "saved_bytes": 0,
            "savings_pct": 0.0,
            "turns_saved": max(0, len(results) - 1),
        }
        final_res = base_res

    return attach_envelope(
        final_res,
        tracker,
        verbosity=verbosity,
        summary=f"Batch '{batch_name}' finished ({len(results)} steps, all_ok={all_ok}).",
    )


async def playbook_run(
    action: str = "batch",
    name: str | None = None,
    steps: list[dict[str, Any]] | str | None = None,
    vars: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    description: str | None = None,
    stop_on_error: bool = True,
    return_only: str = "all",
    select_fields: list[str] | None = None,
    tasks: list[str] | None = None,
    dependencies: dict[str, list[str]] | None = None,
    session_id: str | None = None,
    agent_id: str = "default",
    verbosity: str = "summary",
) -> str:
    """Multi-step Batch / Code Mode & Workflow Orchestration for Penpot MCP.

    Route via action: 'batch' | 'code' | 'run' | 'create' | 'list' | 'dag_audit'.
    """
    act = (action or "batch").strip().lower()
    tracker = RequestTelemetry("playbook_run", act)
    valid = ["batch", "code", "run", "create", "list", "dag_audit"]
    effective_vars = vars if vars is not None else (params or {})
    eff_sid = (
        session_id
        or (effective_vars.get("session_id") if isinstance(effective_vars, dict) else None)
        or "penpot-batch"
    )

    try:
        if act in ("dag_audit", "audit"):
            if not tasks:
                return _missing_param_response("playbook_run", act, ["tasks"], tracker, verbosity)
            verdict, logs = ToposGuard.audit_task_dag(tasks, dependencies or {})
            return attach_envelope(
                {
                    "status": verdict,
                    "verdict": verdict,
                    "details": logs,
                },
                tracker,
                verbosity=verbosity,
                summary=f"DAG audit verdict: {verdict} ({len(tasks)} tasks).",
            )

        if act == "list":
            return attach_envelope(
                {
                    "status": "OK",
                    "built_in_playbooks": list(_TACTICAL_PLAYBOOKS.keys()),
                    "custom_playbooks": [
                        {
                            "name": k,
                            "description": v.get("description", ""),
                            "steps_count": len(v.get("steps", [])),
                        }
                        for k, v in _PLAYBOOK_STORE.items()
                    ],
                },
                tracker,
                verbosity=verbosity,
            )

        if act == "create":
            if not name or not steps:
                return _missing_param_response("playbook_run", act, ["name", "steps"], tracker, verbosity)
            parsed_steps = json.loads(steps) if isinstance(steps, str) else steps
            if not isinstance(parsed_steps, list) or not parsed_steps:
                return attach_envelope(
                    {"status": "PARAM_ERROR", "error": "'steps' must be a non-empty list of step dicts."},
                    tracker,
                    verbosity=verbosity,
                )
            v = ToposGuard.validate_and_order(parsed_steps)
            if not v.get("ok"):
                return attach_envelope(
                    {"status": "BLOCKED", "verdict": v.get("verdict"), "logs": v.get("logs")},
                    tracker,
                    verbosity=verbosity,
                )
            _PLAYBOOK_STORE[name.strip()] = {
                "name": name.strip(),
                "description": description or "",
                "steps": v["steps"],
            }
            return attach_envelope(
                {
                    "status": "OK",
                    "name": name.strip(),
                    "verdict": v["verdict"],
                    "order": v["order"],
                    "steps": len(v["steps"]),
                },
                tracker,
                verbosity=verbosity,
            )

        if act == "run":
            if not name:
                return _missing_param_response("playbook_run", act, ["name"], tracker, verbosity)
            pb = _PLAYBOOK_STORE.get(name.strip())
            if not pb:
                return attach_envelope(
                    {"status": "ERROR", "error": f"Playbook {name.strip()!r} not found."},
                    tracker,
                    verbosity=verbosity,
                )
            raw_steps = pb["steps"]
            if effective_vars:
                raw_steps = hydrate_struct(raw_steps, effective_vars, strict=False)
            v = ToposGuard.validate_and_order(raw_steps)
            if not v.get("ok"):
                return attach_envelope(
                    {"status": "BLOCKED", "verdict": v.get("verdict"), "logs": v.get("logs")},
                    tracker,
                    verbosity=verbosity,
                )
            return await _execute_ordered_steps(
                v["ordered_steps"],
                batch_name=name.strip(),
                eff_sid=str(eff_sid),
                eff_agent=agent_id,
                stop_on_error=stop_on_error,
                return_only=return_only,
                select_fields=select_fields,
                verbosity=verbosity,
                tracker=tracker,
            )

        if act in ("batch", "code", "ephemeral", "pipeline"):
            raw_steps_in = json.loads(steps) if isinstance(steps, str) else steps
            if not isinstance(raw_steps_in, list) or not raw_steps_in:
                return attach_envelope(
                    {
                        "status": "PARAM_ERROR",
                        "error": "Action 'batch'/'code' requires 'steps' (non-empty list of step dicts).",
                    },
                    tracker,
                    verbosity=verbosity,
                )
            if effective_vars:
                raw_steps_in = hydrate_struct(raw_steps_in, effective_vars, strict=False)
            v = ToposGuard.validate_and_order(raw_steps_in)
            if not v.get("ok"):
                return attach_envelope(
                    {"status": "BLOCKED", "verdict": v.get("verdict"), "logs": v.get("logs")},
                    tracker,
                    verbosity=verbosity,
                )
            return await _execute_ordered_steps(
                v["ordered_steps"],
                batch_name=(name or "ephemeral-batch").strip(),
                eff_sid=str(eff_sid),
                eff_agent=agent_id,
                stop_on_error=stop_on_error,
                return_only=return_only,
                select_fields=select_fields,
                verbosity=verbosity,
                tracker=tracker,
            )

        return _invalid_action_response("playbook_run", action, valid, tracker, verbosity)
    except Exception as exc:
        return attach_envelope({"status": "ERROR", "action": act, "error": str(exc)}, tracker, verbosity=verbosity)


COMPRESSED_SUBTOOLS: list[Callable[..., Any]] = [
    discover,
    projects,
    files,
    shapes,
    create,
    modify,
    text,
    assets,
    comments,
    media,
    export,
    system,
    plugin,
    playbook_run,
]
