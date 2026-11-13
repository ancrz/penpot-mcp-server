# Version Control Report — Stage 6

**Project:** `penpot-mcp-server`  
**Branch:** `dev`  
**Base / Upstream:** `origin/dev`  
**Mode:** Short Mode  
**Timestamp:** 2026-11-13T21:37:00Z  
**Verdict Hand-off:** Dokimos VERIFIED (8/8 quality gates pass)  
**Release Tag:** `v0.2.0`  

---

## 1. Author Identity & Security Verification

- **Git Author Name:** `ancrz`
- **Git Author Email:** `anthony_cruz@outlook.com`
- **Credential & Secret Protection:** `.env` verified ignored via `git check-ignore -v .env` (`.gitignore:4:*.env`) and strictly excluded from git staging.
- **Scope Creep Audit:** Stage 6 staged only files within the verified blast radius.

---

## 2. Commit Log & Atomic Ordering

Follows Conventional Commits v1.0.0 and TM Forum traceability standards across dependency layers:

| # | Short Hash | Type | Scope | Description | Files |
|---|---|---|---|---|---|
| 1 | `13262ed` | `build` | `deps` | upgrade dependencies to latest stable and parameterize version env vars (v0.2.0) | 6 |
| 2 | `f86832d` | `fix` | `security` | harden multi-stage Dockerfile and remediate all Semgrep SAST findings | 7 |
| 3 | `HEAD` | `docs` | `release` | document v0.2.0 version variables and record verification artifacts | 8 |

### Commit Breakdown

#### 1. `13262ed` — `build(deps)`
- Bump project version to 0.2.0 in `pyproject.toml` and refresh `uv.lock`.
- Parameterize `PENPOT_MCP_IMAGE_TAG` and Penpot backend versions in `setup.sh`.
- Synchronize version defaults in `config.py`, `.env.example`, and `.env.local.example`.
- *Plan-ID:* `feature_20261113_172000` | *Verified-By:* Dokimos (8/8 gates PASS)

#### 2. `f86832d` — `fix(security)`
- Implement multi-stage build with non-root `appuser` (UID 10001) in `Dockerfile`.
- Wire build arguments and version environment variables in `docker-compose.penpot.yml`.
- Enforce explicit SQL query projection and parameterization in `services/db.py`.
- Fix SSRF validation with strict protocol and domain filtering in `services/api.py`.
- Remediate Semgrep SAST findings in `tools/tokens.py` and `ws_controller.py`.
- Update unit tests in `tests/test_unit_core.py` to match hardened security behavior.
- *Plan-ID:* `feature_20261113_172000` | *Verified-By:* Dokimos (8/8 gates PASS, 0 CVEs, 0 SAST findings)

#### 3. `HEAD` — `docs(release)`
- Update `README.md` and `README.es-ES.md` with v0.2.0 architecture and setup instructions.
- Add `docs/PROJECT.md`, `docs/MILESTONES.md`, and `docs/COMPACT.md` with reinforcement graphs.
- Record pipeline artifacts (`execution_report.md`, `feature_20261113_172000.md`, `verification_report.md`, `version_control_report.md`).
- *Plan-ID:* `feature_20261113_172000` | *Verified-By:* Dokimos (8/8 gates PASS)

---

## 3. Local Release Tag

- **Tag Name:** `v0.2.0`
- **Type:** Annotated
- **Message:** `Release v0.2.0: parameterized version management, multi-stage zero-CVE container, and Semgrep SAST hardening`
- **Target:** `dev` (Release HEAD)

---

## 4. User-Gated Push Status

- **Policy:** GEMINI.md Stage 6 User-Gated Push Rule enforced.
- **Current State:** Commits and tag remain **LOCAL ONLY** on branch `dev`.
- **Remote Push:** Pending explicit user approval.
- **Next Steps:**
  - Fast-forward merge or push `dev` to `origin/dev`.
  - Fast-forward merge or push `main` to `origin/main` (if desired).
  - Push annotated tag `v0.2.0` (`git push origin v0.2.0`).
