# Context Compaction Instruction
Mode: Short Mode | Branch: dev | Target: penpot-mcp:0.7.0+ (Python 3.14 + Pydantic v2, Go 1.24 WS Hub, React 19 + Zod SPA, Nginx Unprivileged, PostgreSQL 17 + pgvector 0.8.7, Valkey 8.1)
Active Intent: Chat attachments, `design_suite_sandbox`, `ffmpeg` Code Mode, persistent/ephemeral sandboxes, Canvas Delta probe, multi-day PostgreSQL chat persistence, and fresh-start seeding.
Status: VERIFIED (87 unit tests pass, ruff/mypy clean, Docker + Playwright live reload persistence verified).
Ready / Implemented:
- Chat attachments (`/agent/attachments`): `.penpot` (with project import), `.svg`, `.json`, `.md`, `.txt`, `.csv`, `.yaml`, images, `.pdf`, `.docx`, `.xlsx`.
- `design_suite_sandbox` + `ffmpeg` MCP in Code Mode.
- Per-chat sandbox policy (ephemeral TTL `SANDBOX_EPHEMERAL_TTL_SEC` vs persistent no-expiration) + `/agent/sandbox/download` + `penpot_sandbox_data:/sandbox` volume documented in `docker-compose.penpot.yml` & `.env.example`.
- Manual-edit probe (`plugin.js` `filechange`/`shapechange` + `db.get_canvas_delta_since` + Valkey `vision`/`file` cache invalidation + prompt injection in `agent_gateway.py`).
- Multi-day chat persistence (`public.mcp_chat_sessions` + `/agent/chats*` + React selector / `+ Nuevo Chat`).
- Idempotent fresh-start seeding in `db.ensure_schema()`: `'default'` chat session + `mcp_user_ai_profile` backfill from `public.profile` + `artifacts` column migration.
- `README.md` updated with v0.7.0+ capabilities.
Pending / Optional Future Work:
- Persist uploaded chat `attachments` metadata/blobs to PostgreSQL or `/sandbox/uploads` instead of process RAM if survival across container restarts is required.
- Optional automatic `pgvector` warm-up scan (`mcp_canvas_vision_index`) on fresh startup for pre-existing Penpot files.
- Additional live/E2E validations requested by user (e.g., manual Penpot edit delta probe end-to-end, persistent sandbox download across container restart).
Drop: Raw build logs and intermediate traces.
