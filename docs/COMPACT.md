# Context Compaction Instruction
Mode: Full Mode | Branch: dev | Target: penpot-mcp:0.4.0 (MCP 2025-03-26)
Active Intent: v0.4.0 RE as SDD (Figma AI + Official Penpot MCP + sdk-agent-pipeline + vanguard-control) — Hybrid PenpotGateway, Embedded Multi-Provider AI Gateway (`PenpotAgentGateway`), Correlated WebSocket Futures, and In-App Penpot AI Studio Mini-Frontend GUI
Status: Dokimos VERIFIED (45/45 unit tests passed, Ruff 0, Mypy 0 in 34 files, Semgrep 0, pip-audit 0, Docker Scout 0 CVEs, Live LLM `/agent/chat` & Playwright E2E verified) -> Ready for Hermon commit on `dev`
Files Changed: Dockerfile, docker-compose.penpot.yml, .env.example, .env.local.example, .gitignore, scripts/agent_reload_stack.sh, _re/figma_ai_penpot_sdd/blueprint.md, docs/06_re_sdd_ai_gateway_blueprint.md, src/penpot_mcp/__init__.py, src/penpot_mcp/config.py, src/penpot_mcp/ws_controller.py, src/penpot_mcp/gateway.py, src/penpot_mcp/agent_gateway.py, src/penpot_mcp/server.py, src/penpot_mcp/tools/subtools.py, src/penpot_mcp/plugin/manifest.json, src/penpot_mcp/plugin/plugin.js, src/penpot_mcp/plugin/ui.html, tests/test_unit_core.py, README.md, README.es-ES.md, TOOLS.md, docs/COMPACT.md, docs/MILESTONES.md, docs/PROJECT.md
Open Defects: None
Routing: Graphos G2 -> Orchestrator -> Hermon (Stage 6 Commit & Push to `origin/dev`; `main` and tags strictly user-gated)
Decisions:
- Sanitized & fixed `PenpotGateway` (`is_interactive` now dynamically reflects `ws_controller.is_connected`) and upgraded `PenpotWSController` with correlated `asyncio.Future` RPC (`execute_script`, `request_context`) and dual-transport support (`/plugin/ws` on `:8787`, standalone `:4402`, and `/plugin/telemetry`).
- Integrated `sdk-agent-pipeline` and `vanguard-control` patterns into `src/penpot_mcp/agent_gateway.py` (`PenpotAgentGateway`) with Gemini/OpenAI/Anthropic/Ollama protocol translation, `thoughtSignature` backfill, circuit-breaker failover (`primary -> backup -> local -> deterministic`), and 1-turn `playbook_run(action='code')` UI artifact synthesis (`hero_card`, `login_form`, `pricing_card`, `navbar`).
- Built the In-App **Penpot AI Studio** Mini-Frontend GUI (`src/penpot_mcp/plugin/ui.html` + `src/penpot_mcp/plugin/plugin.js`) and verified it live in Penpot via Playwright (`MCP_DOCKER`).
Next Actions:
1. Hermon creates atomic commit on `dev` signed as `ancrz <anthony_cruz@outlook.com>` and pushes to `origin/dev` (never `main` or tags without explicit user authorization).
Drop: Raw test outputs, scan logs, and intermediate traces.
