# Version Control Report — Penpot MCP Server v0.4.0 (Full Pipeline)

- **Pipeline File**: `docs/pipeline/feature_20261113_223500.md`
- **Role**: Hermon (Stage 6)
- **Date**: 2026-11-13
- **Author**: `ancrz <anthony_cruz@outlook.com>`
- **Release Tag**: `v0.4.0`

## 1. Commits & Branch Topology

| Branch / Tag | Commit / Reference | Description |
|---|---|---|
| `dev` | `d45633d` | `feat(gateway): integrate SDK agent pipeline, bidirectional plugin futures, and Penpot AI Studio GUI (v0.4.0)` |
| `main` | Fast-forward / merge from `dev` | Promoted verified `v0.4.0` release (`v0.3.0` + `v0.4.0`) to `main` per explicit user authorization |
| `v0.4.0` | Annotated tag on `main` / `dev` | Release `v0.4.0`: Hybrid `PenpotGateway`, `sdk-agent-pipeline` `PenpotAgentGateway`, Correlated WS Futures, and In-App Penpot AI Studio GUI |

## 2. Secret & Supply-Chain Hygiene
- Verified `.env` and `_re/` are excluded via `.gitignore` and absent from `git status` and commit trees.
- Verified `0` CVEs in `pip-audit` and `docker scout cves penpot-mcp:0.4.0`.
- Verified `0` findings in Semgrep (`--config auto`).
