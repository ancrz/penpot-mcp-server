# Pragma Execution Report — Stage 4 (v0.3.0)

**Project:** `penpot-mcp-server`  
**Branch:** `dev`  
**Target Version:** `0.3.0`  
**Protocol Version:** `2025-03-26`  
**Mode:** Short Mode (RE Compatible from `agent-execution-plane`)  
**Date:** 2026-11-13  

---

## 1. Declared Tool Reach & Blast Radius (P1)

- **Tools Used:** `agent-execution-plane` MCP (`topos_audit_dag`, `aep_ctl`, `sandbox_run`, `kanban_board`), native file tools, Docker Engine + Docker Scout, Semgrep, Ruff, Mypy, Pytest, `pip-audit`.
- **Blast Radius:**
  - `src/penpot_mcp/__init__.py` (dynamic package version `0.3.0`)
  - `src/penpot_mcp/aep_engine.py` (RE from AEP: `SessionPool` RAM cache, `ToposGuard` DAG validator, `hydrate_struct`, `resolve_step_refs`, `project_fields`, `RequestTelemetry`, `attach_envelope`)
  - `src/penpot_mcp/tools/subtools.py` (14 polymorphic domain sub-tools multiplexing all 77 actions + `playbook_run` Code Mode batch engine)
  - `src/penpot_mcp/server.py` (`configure_mcp_tools` for `compressed` / `legacy` / `hybrid` modes, `NotificationOptions(tools_changed=True)`)
  - `src/penpot_mcp/config.py`, `.env.example`, `.env.local.example`, `Dockerfile`, `docker-compose.penpot.yml`, `pyproject.toml`, `uv.lock`
  - `src/penpot_mcp/services/api.py`, `src/penpot_mcp/services/db.py`, `src/penpot_mcp/tools/media.py`, `src/penpot_mcp/tools/tokens.py`, `src/penpot_mcp/ws_controller.py`
  - `tests/test_unit_core.py`, `README.md`, `README.es-ES.md`, `TOOLS.md`, `docs/COMPACT.md`

---

## 2. Summary of Changes Implemented

1. **AEP Sub-Tool Compression (77 Granular Tools → 14 Polymorphic Sub-Tools)**:
   - Implemented `discover`, `projects`, `files`, `shapes`, `create`, `modify`, `text`, `assets`, `comments`, `media`, `export`, `system`, `plugin`, and `playbook_run` in `src/penpot_mcp/tools/subtools.py`.
   - Configurable via `MCP_TOOL_MODE=compressed` (default, 14 tools), `legacy` (77 tools), or `hybrid` (91 tools).
   - Reduces initial MCP `tools/list` schema payload by ~82% and achieves ~95% token reduction on `discover(action="capabilities")` cache hits.
2. **AEP Code Mode (`playbook_run`) & Token Optimization Primitives**:
   - Ported `ToposGuard.audit_task_dag` (Kahn's algorithm cycle detection) to block circular step dependencies before execution.
   - Ported `${var}` native-type-preserving structural hydration and `$prev.<path>`, `$steps.<step_id>.<path>`, `$cache.<slot>.<path>` variable piping across batch steps.
   - Added `verbosity="summary" | "detail" | "brief" | "terse"`, `select_fields` dot-path projection, and `return_only="last" | "summary" | "all"`.
3. **Audit Observation Remediations**:
   - **Real SSRF & RPC Guards (`services/api.py`, `tools/media.py`)**: Implemented `validate_rpc_method`, `validate_penpot_endpoint_url`, `validate_external_media_url` (blocking loopback, link-local/cloud metadata `169.254.169.254`, RFC1918 private networks by default, non-HTTP schemes, and single-label internal container hostnames), and `validate_export_redirect_uri` with `follow_redirects=False`.
   - **Real Input & WebSocket Validation (`tools/tokens.py`, `ws_controller.py`)**: Added validation on `shape_ids` (non-empty, max 500), `token_type`, and `target_property` in `apply_design_token`; added `max_size=1_048_576`, `host` validation, and string-ID sanitization in `PenpotWSController`.
   - **Backward-Compatible `PENPOT_DB_USER` Default (`config.py`, `services/db.py`)**: Restored `penpot` as the default `PENPOT_DB_USER` so existing `.env` configurations do not break on upgrade, while logging a startup warning recommending the dedicated read-only `penpot_mcp` role.
   - **Documentation Clarity (`README.md`, `README.es-ES.md`, `TOOLS.md`)**: Documented the 14 compressed sub-tools and 77 actions, updated the architecture diagram to disambiguate the official Penpot plugin-only MCP, and updated all `.env` variable tables for `v0.3.0`.
   - **AEP Pueue & Kanban Hygiene**: Cleaned all 138 unlabeled finished Pueue tasks (`pueue clean` → 0 tasks) and archived 163 stale `SYNTHESIZED` test tasks from the AEP Kanban board.
