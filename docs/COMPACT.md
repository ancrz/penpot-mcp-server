# Context Compaction Instruction
Mode: Short Mode | Branch: dev (synced with main & tag v0.6.0) | Target: penpot-mcp:0.6.0 (PostgreSQL 17.12 + pgvector 0.8.7, Valkey 8.1, Penpot 2.19.1)
Active Intent: v0.6.0 Reproducible Dockerized PG15->PG17 + pgvector Auto-Migrator (`Dockerfile.pg-migrator`, `scripts/pg_migrator.sh`) and Valkey 8.1 Epoch-Namespaced Read-Through Cache (`src/penpot_mcp/services/cache.py`, `config/valkey/valkey.conf`, `config/postgresql/postgresql.conf`)
Status: VERIFIED (48 passed in AEP sandbox_run, 0 ruff/mypy issues across 35 files, 0 Semgrep findings across 1539 rules, live PG15->PG17 migration preserving 2 users + 35 indexed shapes, live Valkey cache hit/miss verified)
Files Changed: .env.example, .env.local.example, Dockerfile, Dockerfile.pg-migrator, docker-compose.penpot.yml, pyproject.toml, uv.lock, config/postgresql/postgresql.conf, config/valkey/valkey.conf, scripts/pg_migrator.sh, src/penpot_mcp/__init__.py, src/penpot_mcp/config.py, src/penpot_mcp/services/cache.py, src/penpot_mcp/services/db.py, src/penpot_mcp/agent_gateway.py, tests/test_unit_core.py, README.md, README.es-ES.md, docs/COMPACT.md, docs/MILESTONES.md
Open Defects: None
Routing: Stage 6 Hermon (Done)
Decisions:
- Used Dockerized Init-Container (`penpot-pg-migrator`) with `depends_on: condition: service_completed_successfully` bundling PG15+PG17+pgvector binaries so major upgrades run safely via `pg_dumpall` + atomic backup + `initdb --data-checksums` + `pg_restore` + automatic rollback without `docker.sock`.
- Ported `edilio-vivas` `volatile-lfu` Valkey 8.1 config and `O(1)` epoch-based namespace invalidation (`ValkeyCache`) on Valkey DB 1 (isolating Penpot's DB 0) with fail-open degradation to PostgreSQL.
Next Actions: None (v0.6.0 complete and verified).
Drop: Raw test outputs, scan logs, and intermediate traces.
