# Version Control Report — Penpot MCP Server v0.5.0 (Full Pipeline)

- **Role**: Hermon (Stage 6)
- **Date**: 2026-11-14
- **Author**: `ancrz <anthony_cruz@outlook.com>`
- **Release Tag**: `v0.5.0`

## 1. Commits & Branch Topology

| Branch / Tag | Commit / Reference | Description |
|---|---|---|
| `dev` | `6281248` | `feat(gateway,vision,db): add multi-user Penpot profile isolation and pgvector Canvas Vision Eye (v0.5.0)` |
| `main` | Fast-forward from `dev` | Promoted verified `v0.5.0` release to `main` |
| `v0.5.0` | Annotated tag on `main` / `dev` | Release `v0.5.0`: Multi-user Penpot profile isolation + `pgvector` Canvas Vision Eye |

## 2. Secret & Supply-Chain Hygiene
- Verified `.env` and `_re/` are excluded via `.gitignore` and absent from `git status` and commit trees.
- Verified `0` findings in Semgrep (`--config auto`).
