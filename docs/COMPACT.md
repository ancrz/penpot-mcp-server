# Context Compaction Instruction
Mode: Short Mode | Branch: dev | Target: penpot-mcp:0.3.0 (MCP 2025-03-26)
Active Intent: v0.3.0 AEP Sub-Tool Compression (14 Sub-Tools / 77 Actions), Code Mode (`playbook_run`), Real SSRF Hardening & Audit Remediation
Status: Dokimos VERIFIED (43/43 tests passed, Ruff 0, Mypy 0 in 32 files, Semgrep 0, pip-audit 0, Docker Scout 0 CVEs) -> Ready for Hermon commit on `dev`
Files Changed: pyproject.toml, uv.lock, Dockerfile, docker-compose.penpot.yml, .env.example, .env.local.example, src/penpot_mcp/__init__.py, src/penpot_mcp/aep_engine.py, src/penpot_mcp/config.py, src/penpot_mcp/server.py, src/penpot_mcp/ws_controller.py, src/penpot_mcp/services/api.py, src/penpot_mcp/services/db.py, src/penpot_mcp/tools/subtools.py, src/penpot_mcp/tools/media.py, src/penpot_mcp/tools/tokens.py, tests/test_unit_core.py, README.md, README.es-ES.md, TOOLS.md, docs/COMPACT.md
Open Defects: None
Routing: Graphos G2 -> Orchestrator -> Hermon (Stage 6 Commit & Push to `origin/dev`; `main` and tags strictly user-gated)
Decisions:
- Compressed 77 granular tools into 14 polymorphic domain sub-tools (`MCP_TOOL_MODE=compressed` default, with `legacy` and `hybrid` modes configurable via `.env`).
- Ported AEP Code Mode (`playbook_run` with `action="batch"|"code"`, `ToposGuard` DAG cycle check, `${var}` hydration, `$prev.*` / `$steps.<id>.*` / `$cache.<key>.*` piping, `verbosity`, `select_fields`, and `SessionPool` RAM cache).
- Implemented real SSRF & RPC command validation in `services/api.py` and `tools/media.py`, plus real input/size/host validation in `tools/tokens.py` and `ws_controller.py`.
- Restored `PENPOT_DB_USER=penpot` as backward-compatible default while warning at startup to use `penpot_mcp` in hardened deployments.
- Dynamic version `0.3.0` sourced from `src/penpot_mcp/__init__.py` and parameterized across `.env`, `.env.example`, `.env.local.example`, `Dockerfile`, and `docker-compose.penpot.yml`.
Next Actions:
1. Build Docker image `penpot-mcp:0.3.0` and verify 0 CVEs with Docker Scout.
2. Hermon creates atomic commit on `dev` and pushes to `origin/dev` (never `main` or tags without explicit user authorization).
Drop: Raw test outputs, scan logs, and resolved intermediate defect traces.
