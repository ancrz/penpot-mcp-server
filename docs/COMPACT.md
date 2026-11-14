# Context Compaction Instruction
Mode: Full Mode | Branch: dev (synced with main & tag v0.5.0) | Target: penpot-mcp:0.5.0 (MCP 2025-03-26, Penpot 2.19.1, PostgreSQL 15 + pgvector 0.8.7 + pg_trgm 1.6)
Active Intent: v0.5.0 Multi-User Penpot Profile Integration (`public.profile` + `public.http_session_v2` + `public.mcp_user_ai_profile`) and PostgreSQL `pgvector 0.8.7` Semantic & Spatial Canvas Vision Eye (`public.mcp_canvas_vision_index` with `vector(64)` HNSW + `pg_trgm`)
Status: VERIFIED (47 passed in pytest, 0 ruff/mypy issues, 0 Semgrep findings, AEP topos_audit_dag APPROVED; live `penpot-mcp:0.5.0` running on `:8787` with `pgvector/pgvector:pg15` indexing 35 canvas shapes in <50ms)
Files Changed: Dockerfile, docker-compose.penpot.yml, .env.example, .env.local.example, scripts/agent_reload_stack.sh, scripts/init_pgvector_and_users.sql, src/penpot_mcp/__init__.py, src/penpot_mcp/services/db.py, src/penpot_mcp/services/vision.py, src/penpot_mcp/agent_gateway.py, src/penpot_mcp/server.py, src/penpot_mcp/plugin/ui.html, tests/test_unit_core.py, docs/COMPACT.md
Open Defects: None
Routing: Stage 6 Hermon (Done)
Decisions:
- Enabled `pgvector 0.8.7` and `pg_trgm 1.6` in PostgreSQL 15 (`penpot-postgres` via `pgvector/pgvector:pg15` + auto-init on `PenpotDB.connect()`) and built `src/penpot_mcp/services/vision.py` to index live Penpot canvas shapes into `public.mcp_canvas_vision_index` (`vector(64)` HNSW cosine + `pg_trgm`) and inject the `Canvas Vision Eye` + collision-free coordinates (`recommended_free_origin`) into every Gateway LLM turn.
- Connected Penpot's native `public.profile`, `public.http_session_v2`, and `public.presence` tables to `public.mcp_user_ai_profile` so each authenticated Penpot designer automatically loads their own isolated AI profile, API keys (`…last4`), and active model (`Claude`, `ChatGPT`, `Gemini`, `DeepSeek`, `Ollama`).
Next Actions: None (v0.5.0 complete and verified).
Drop: Raw test outputs, scan logs, and intermediate traces.
