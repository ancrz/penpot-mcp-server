# Project Context: Penpot MCP Server

## 1. Premise & Core Intent
`penpot-mcp-server` (`v0.4.0`) provides AI agents (Claude Code, Cursor, Gemini CLI, Antigravity) and in-app Penpot designers with full programmatic access to self-hosted [Penpot](https://penpot.app) instances via the Model Context Protocol (FastMCP `2025-03-26`), an AEP-inspired **14 Sub-Tool + Code Mode (`playbook_run`)** execution engine, and an embedded **SDK Agent Pipeline (`PenpotAgentGateway`)** with the **Penpot AI Studio** in-app mini-frontend.

## 2. Active Intent: v0.4.0 Hybrid Gateway, SDK Agent Pipeline & Penpot AI Studio GUI
- **RE as SDD Integration**: Synthesized Figma AI ("Make Designs" contextual bar & live selection context), Official Penpot MCP (bidirectional WebSocket RPC & JS sandbox evaluation), `sdk-agent-pipeline` (multi-provider Gemini REST adapter, `thoughtSignature` backfill, circuit-breaker failover), and `vanguard-control` (`/agent/status`, `/agent/chat`, `/agent/breakers/reset`).
- **Sanitized & Polished Tools**: Fixed `PenpotGateway.is_interactive` to dynamically track `ws_controller.is_connected`, added `asyncio.Future` request/response correlation (`execute_script`, `request_context`), and expanded the `plugin` sub-tool to 6 actions (`selection`, `context`, `status`, `execute_script`, `focus`, `agent_chat`).
- **In-App Penpot AI Studio GUI (`ui.html` + `plugin.js`)**: Embedded mini-frontend inside Penpot providing live WS + HTTP telemetry, active model/selection badges, 1-click Quick Actions (`+ Hero Card`, `+ Login Form`, `+ Pricing Card`, `+ Navbar`, `Bind Tokens`, `Inspect CSS`), natural-language composer, tool execution trace viewer, and shape focus pills.

## 3. Architecture & Key Decisions
- **Quad-Layer Strategy**:
  1. Direct PostgreSQL reads via `asyncpg` (`db.py`)
  2. Validated Penpot Transit/JSON RPC writes via `httpx` (`api.py`)
  3. Bidirectional Plugin WebSocket & HTTP Telemetry bridge (`ws_controller.py` + `gateway.py`)
  4. Embedded Multi-Provider AI Gateway (`agent_gateway.py`) + In-App **Penpot AI Studio** (`plugin/ui.html`, `plugin/plugin.js`)
- **Runtime & Security**: Python 3.13-alpine, Astral `uv` 0.12.10, non-root container (`0` CVEs in Docker Scout and `pip-audit`, `0` Semgrep findings).

## 4. Current State
- **Implementation**: Completed by Pragma (`v0.4.0`).
- **Verification**: Verified by Dokimos (45/45 unit tests, Ruff 0, Mypy 0 across 34 files, Semgrep 0, Docker Scout 0 CVEs, live Gemini `gemini-3.5-flash-lite` `/agent/chat` execution, and live Playwright verification of the Penpot AI Studio GUI and Penpot canvas).
