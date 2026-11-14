# Context Compaction Instruction
Mode: Short Mode | Branch: dev (synced with main & tag v0.7.0) | Target: penpot-mcp:0.7.0 (Python 3.14 + Pydantic v2, Go 1.24 WS Hub, React 19 + Zod SPA, Nginx Unprivileged, PostgreSQL 17.12 + pgvector 0.8.7, Valkey 8.1)
Active Intent: v0.7.0 React 19 + Zod SPA, Imperative Go 1.24 WebSocket Hub (`ws-hub-go/`), Nginx Unprivileged Gateway (`nginx/`), Python 3.14 upgrade, and Penpot DB Gap Closure (6 new compressed sub-tool actions)
Status: VERIFIED (49 passed in AEP sandbox_run, Go test PASS, Vite+TSC build PASS, Playwright live E2E `🔌 Go WS: Live` + `👁️ pgvector 0.8.7 (41)` + `⚡ Valkey 1H/12M`, Semgrep running/clean)
Files Changed: Dockerfile, pyproject.toml, uv.lock, .env.example, .env.local.example, src/penpot_mcp/__init__.py, src/penpot_mcp/services/db.py, src/penpot_mcp/tools/subtools.py, ws-hub-go/*, frontend/*, nginx/*, tests/test_unit_core.py, README.md, README.es-ES.md, docs/COMPACT.md, docs/MILESTONES.md
Open Defects: None
Routing: Stage 6 Hermon (Done)
Decisions:
- Upgraded Python runtime to 3.14 (`3.14-alpine`) with Pydantic v2, paired with React 19 + TypeScript + Zod SPA served by Nginx Unprivileged (`nginxinc/nginx-unprivileged:1.27-alpine`).
- Moved WebSocket handling to a dedicated Go 1.24 goroutine Hub (`ws-hub-go/`) behind Nginx `/plugin/ws` with `same_origin_ws_path` token auth.
- Closed Penpot DB gaps by compressing `team_members`, `team_fonts`, `share_links`, `live_presence`, `audit_log`, and `cache_stats` into the 14 domain sub-tools.
Next Actions: None (v0.7.0 complete and verified).
Drop: Raw build logs and intermediate traces.
