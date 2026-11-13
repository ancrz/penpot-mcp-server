# Project Context: Penpot MCP Server

## 1. Premise & Core Intent
`penpot-mcp-server` provides AI agents (Claude Code, Cursor, Gemini CLI, Antigravity) with full programmatic access to self-hosted [Penpot](https://penpot.app) instances via the Model Context Protocol (FastMCP protocol version `2025-03-26`).

## 2. Active Intent: v0.2.0 Hardening & Modernization
- Complete upgrade from initial `0.1.0` baseline to `0.2.0`.
- Eliminate 76 known supply-chain vulnerabilities flagged by `pip-audit` (reduced to 0 CVEs).
- Eliminate Semgrep SAST security and code quality findings across source and infra configs (0 findings across 1584 auto and 1455 security rules).
- Provide a hardened, multi-stage Alpine Docker runtime with non-root privileges (`appuser`), Docker healthchecks, and stripped build packages (78 artifacts, 0 container CVEs via Docker Scout & OSV.dev).
- Parameterize build arguments and version configuration across `.env`, `Dockerfile`, and `docker-compose.penpot.yml`.

## 3. Architecture & Key Decisions
- **Tri-Layer Strategy:** Direct PostgreSQL reads via `asyncpg`, RPC API writes via `httpx`, and live browser canvas interactions via WebSocket bridge (`websockets`).
- **Runtime & Toolchain:** Python 3.13-alpine, Astral `uv` 0.12.10 package manager, Pydantic v2 Settings with `extra = "ignore"`.
- **Packaging:** Multi-stage Docker container with stripped pip/setuptools/ensurepip artifacts.

## 4. Current State
- **Implementation:** Completed by Pragma across 14 source and configuration files.
- **Verification:** Stage 5 verification completed by Dokimos with a **VERIFIED** verdict across all 8 quality gates (Ruff lint, Pytest 38/38, Mypy, Semgrep Auto, Semgrep Security, pip-audit, Docker smoke test, Docker Scout OSV).
- **Handoff:** Ready for Stage 6 Hermon atomic commit and release tagging (`v0.2.0`).
