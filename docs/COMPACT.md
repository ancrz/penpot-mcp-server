# Context Compaction Instruction
Mode: Short Mode | Branch: dev | Target: penpot-mcp:0.7.0+ (Python 3.14 + Pydantic v2, Go 1.24 WS Hub, React 19 + Zod SPA, Nginx Unprivileged, PostgreSQL 17 + pgvector 0.8.7, Valkey 8.1, sqlite-vec 0.1.9)
Active Intent: SDD modularization, Dual-DB `AgentStateStore`, and `sqlite-vec` semantic memory complementing `pgvector` canvas vision, plus chat attachments, sandboxes, Canvas Delta probe, and fresh-start seeding.
Status: VERIFIED (90 unit tests pass via `pytest -m "not e2e"`, ruff/mypy clean, committed on `dev` @ `8e1ae46`).
Ready / Implemented:
- Chat attachments (`/agent/attachments`): `.penpot` (with project import), `.svg`, `.json`, `.md`, `.txt`, `.csv`, `.yaml`, images, `.pdf`, `.docx`, `.xlsx`.
- `design_suite_sandbox` + `ffmpeg` MCP in Code Mode.
- Per-chat sandbox policy (ephemeral TTL `SANDBOX_EPHEMERAL_TTL_SEC` vs persistent no-expiration) + `/agent/sandbox/download` + `penpot_sandbox_data:/sandbox` volume documented in `docker-compose.penpot.yml` & `.env.example`.
- Manual-edit probe (`plugin.js` `filechange`/`shapechange` + `db.get_canvas_delta_since` + Valkey `vision`/`file` cache invalidation + prompt injection in `agent_gateway.py`).
- Multi-day chat persistence (`public.mcp_chat_sessions` + `/agent/chats*` + React selector / `+ Nuevo Chat`).
- Idempotent fresh-start seeding in `db.ensure_schema()`: `'default'` chat session + `mcp_user_ai_profile` backfill from `public.profile` + `artifacts` column migration.
- Schema-Driven Development (SDD) & modularization: `src/penpot_mcp/schemas/agent_sdd.py` + `frontend/src/schemas.ts`, `frontend/src/types.ts`, `frontend/src/App.tsx`, `frontend/src/tabs/*` (no hardcodes).
- Dual-DB `AgentStateStore` (`src/penpot_mcp/services/agent_store.py`, `AGENT_DB_URL`): supports `penpot_postgres` (default), `sqlite:///...`, or secondary `postgres://...`.
- `sqlite-vec` (`v0.1.9`, `vec0` `float[64]`) semantic memory in `AgentStateStore`: auto-indexes `chat_turn` (`POST /agent/chat`) and `artifact` (`register_artifact()`), exposes `GET /agent/memory/search`, and injects cross-chat recall into `AgentGateway.chat()`.
- `README.md` and `docs/COMPACT.md` updated with SDD, Dual-DB, and `sqlite-vec` architecture.
Pending / Optional Future Work:
- Persist uploaded chat `attachments` metadata/blobs to PostgreSQL or `/sandbox/uploads` instead of process RAM if survival across container restarts is required.
- Optional automatic `pgvector` warm-up scan (`mcp_canvas_vision_index`) on fresh startup for pre-existing Penpot files.
- Additional live/E2E validations requested by user (e.g., manual Penpot edit delta probe end-to-end, persistent sandbox download across container restart).
Drop: Raw build logs and intermediate traces.
