# Hermon Version Control Report — Stage 6 (v0.3.0)

**Project:** `penpot-mcp-server`  
**Branch:** `dev`  
**Target Version:** `0.3.0`  
**Author / Committer:** `ancrz <anthony_cruz@outlook.com>`  
**Date:** 2026-11-13  

---

## 1. Branch & Gate Policy Compliance

- **Active Branch:** `dev`
- **Remote Sync (`origin/dev`):** Pushed pending commit `e25d324` (`fix(typing): resolve mypy errors in advanced and colors tools`) first, followed by `v0.3.0` feature & security remediation commit.
- **`main` & Tag Gate Policy:** Strictly enforced — **no push to `main` and no tag push (`v0.3.0`) executed** without explicit user authorization.

---

## 2. Atomic Commit Summary (`v0.3.0` on `dev`)

- **Type:** `feat(mcp)`
- **Summary:** `feat(mcp): compress 77 tools into 14 AEP sub-tools, add playbook_run code mode, and enforce real SSRF guards (v0.3.0)`
- **Key Deliverables Included:**
  - `src/penpot_mcp/aep_engine.py`: AEP RAM `SessionPool`, `ToposGuard` DAG validator, `${var}` structural hydration, `$prev`/`$steps`/`$cache` piping, and dot-path `select_fields` projection.
  - `src/penpot_mcp/tools/subtools.py` & `src/penpot_mcp/server.py`: 14 polymorphic domain sub-tools multiplexing all 77 Penpot actions (`MCP_TOOL_MODE=compressed` default, plus `legacy` and `hybrid` modes).
  - `src/penpot_mcp/services/api.py`, `src/penpot_mcp/tools/media.py`, `src/penpot_mcp/tools/tokens.py`, `src/penpot_mcp/ws_controller.py`: Real SSRF, RPC path, exporter redirect, and input/size validation guards.
  - `src/penpot_mcp/config.py` & `src/penpot_mcp/services/db.py`: Restored backward-compatible `PENPOT_DB_USER=penpot` default with startup warning recommending `penpot_mcp` read-only role.
  - `Dockerfile`, `docker-compose.penpot.yml`, `pyproject.toml`, `.env.example`, `.env.local.example`: Dynamic version `0.3.0` and zero-CVE Alpine container hardening (`0C 0H 0M 0L`).
