#!/usr/bin/env bash
set -euo pipefail

# Idempotent Dockerized PostgreSQL + pgvector Auto-Migrator
# Supports:
#   - Fresh install (no PG_VERSION -> exit 0, target postgres initializes cleanly)
#   - Same major version (no-op, exit 0)
#   - Major version upgrade (e.g. 15 -> 17) via target pg_dumpall + atomic backup + initdb + restore
#   - --dry-run mode for abstract pre-flight validation

DRY_RUN="${DRY_RUN:-false}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="true"
fi

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
TARGET_MAJOR="${POSTGRES_TARGET_MAJOR:-17}"
PGUSER="${POSTGRES_USER:-penpot}"
PGDB="${POSTGRES_DB:-penpot}"
PGPASSWORD="${POSTGRES_PASSWORD:-penpot}"

log() {
  echo "[pg-migrator] $*"
}

if [[ ! -f "${PGDATA}/PG_VERSION" ]]; then
  log "Fresh PGDATA (${PGDATA}) detected — target PostgreSQL ${TARGET_MAJOR} will initialize cleanly."
  exit 0
fi

CURRENT_MAJOR="$(tr -d '[:space:]' < "${PGDATA}/PG_VERSION")"
log "Detected PGDATA version=${CURRENT_MAJOR}, target=${TARGET_MAJOR}, dry_run=${DRY_RUN}"

if [[ "${CURRENT_MAJOR}" == "${TARGET_MAJOR}" ]]; then
  log "PGDATA is already at major version ${TARGET_MAJOR}. Nothing to migrate."
  exit 0
fi

if [[ "${CURRENT_MAJOR}" -gt "${TARGET_MAJOR}" ]]; then
  log "ERROR: Downgrade from ${CURRENT_MAJOR} to ${TARGET_MAJOR} is unsupported."
  exit 1
fi

OLD_BIN="/usr/lib/postgresql/${CURRENT_MAJOR}/bin"
NEW_BIN="/usr/lib/postgresql/${TARGET_MAJOR}/bin"

if [[ ! -x "${OLD_BIN}/pg_ctl" ]]; then
  log "ERROR: Old binaries missing at ${OLD_BIN}."
  exit 1
fi
if [[ ! -x "${NEW_BIN}/pg_dumpall" ]] || [[ ! -x "${NEW_BIN}/initdb" ]]; then
  log "ERROR: Target binaries missing at ${NEW_BIN}."
  exit 1
fi

TS="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/penpot_pg${CURRENT_MAJOR}_to_${TARGET_MAJOR}_${TS}.sql"
OLD_BACKUP_DIR="${BACKUP_DIR}/pgdata_v${CURRENT_MAJOR}_${TS}"

if [[ "${DRY_RUN}" == "true" ]]; then
  log "DRY-RUN PLAN:"
  log "  1. Start temporary PG ${CURRENT_MAJOR} (${OLD_BIN}) on 127.0.0.1:5439"
  log "  2. Logical dump via PG ${TARGET_MAJOR} pg_dumpall -> ${DUMP_FILE}"
  log "  3. Stop PG ${CURRENT_MAJOR} and archive ${PGDATA} -> ${OLD_BACKUP_DIR}"
  log "  4. Run ${NEW_BIN}/initdb --data-checksums -D ${PGDATA}"
  log "  5. Start temporary PG ${TARGET_MAJOR}, restore ${DUMP_FILE}, ensure vector & pg_trgm, ANALYZE"
  log "  6. Stop temporary PG ${TARGET_MAJOR}; rollback to ${OLD_BACKUP_DIR} if any step fails"
  exit 0
fi

mkdir -p "${BACKUP_DIR}"
chown -R postgres:postgres "${BACKUP_DIR}" "${PGDATA}"
chmod 700 "${PGDATA}"

PWFILE="$(mktemp)"
printf '%s' "${PGPASSWORD}" > "${PWFILE}"
chown postgres:postgres "${PWFILE}"
chmod 600 "${PWFILE}"

rollback() {
  local rc=$?
  rm -f "${PWFILE}"
  if [[ ${rc} -ne 0 ]] && [[ -d "${OLD_BACKUP_DIR}" ]]; then
    log "ERROR detected (rc=${rc}). Rolling back PGDATA from ${OLD_BACKUP_DIR}..."
    su -s /bin/sh postgres -c "${OLD_BIN}/pg_ctl -D '${PGDATA}' -m immediate stop" >/dev/null 2>&1 || true
    su -s /bin/sh postgres -c "${NEW_BIN}/pg_ctl -D '${PGDATA}' -m immediate stop" >/dev/null 2>&1 || true
    rm -rf "${PGDATA:?}/"*
    cp -a "${OLD_BACKUP_DIR}/." "${PGDATA}/"
    chown -R postgres:postgres "${PGDATA}"
    chmod 700 "${PGDATA}"
    log "Rollback complete."
  fi
  exit "${rc}"
}
trap rollback EXIT

log "Step 1/5: Starting temporary PG ${CURRENT_MAJOR} on 127.0.0.1:5439..."
su -s /bin/sh postgres -c "${OLD_BIN}/pg_ctl -D '${PGDATA}' -l '${BACKUP_DIR}/pg${CURRENT_MAJOR}_start.log' -o '-p 5439 -c listen_addresses=127.0.0.1' start"

log "Step 2/5: Exporting logical dump with PG ${TARGET_MAJOR} pg_dumpall -> ${DUMP_FILE}..."
su -s /bin/sh postgres -c "PGPASSWORD='${PGPASSWORD}' ${NEW_BIN}/pg_dumpall -h 127.0.0.1 -p 5439 -U '${PGUSER}' --clean --if-exists > '${DUMP_FILE}'"

log "Step 3/5: Stopping PG ${CURRENT_MAJOR} and archiving old PGDATA -> ${OLD_BACKUP_DIR}..."
su -s /bin/sh postgres -c "${OLD_BIN}/pg_ctl -D '${PGDATA}' -m fast stop"
mkdir -p "${OLD_BACKUP_DIR}"
mv "${PGDATA}/"* "${OLD_BACKUP_DIR}/"
chown -R postgres:postgres "${PGDATA}"
chmod 700 "${PGDATA}"

log "Step 4/5: Initializing clean PG ${TARGET_MAJOR} cluster with --data-checksums..."
su -s /bin/sh postgres -c "${NEW_BIN}/initdb -D '${PGDATA}' --username='${PGUSER}' --pwfile='${PWFILE}' --encoding=UTF8 --locale=en_US.UTF-8 --data-checksums"
cp -a "${OLD_BACKUP_DIR}/pg_hba.conf" "${PGDATA}/pg_hba.conf"

log "Step 5/5: Starting temporary PG ${TARGET_MAJOR}, restoring dump, enabling pgvector + pg_trgm, and running ANALYZE..."
su -s /bin/sh postgres -c "${NEW_BIN}/pg_ctl -D '${PGDATA}' -l '${BACKUP_DIR}/pg${TARGET_MAJOR}_restore.log' -o '-p 5439 -c listen_addresses=127.0.0.1' start"
su -s /bin/sh postgres -c "PGPASSWORD='${PGPASSWORD}' ${NEW_BIN}/psql -h 127.0.0.1 -p 5439 -U '${PGUSER}' -d postgres -v ON_ERROR_STOP=0 -f '${DUMP_FILE}' > '${BACKUP_DIR}/pg${TARGET_MAJOR}_restore_psql.log' 2>&1"
su -s /bin/sh postgres -c "PGPASSWORD='${PGPASSWORD}' ${NEW_BIN}/psql -h 127.0.0.1 -p 5439 -U '${PGUSER}' -d '${PGDB}' -c 'CREATE EXTENSION IF NOT EXISTS vector;' -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;' -c 'ANALYZE;'"
su -s /bin/sh postgres -c "${NEW_BIN}/pg_ctl -D '${PGDATA}' -m fast stop"

rm -f "${PWFILE}"
trap - EXIT
log "Migration ${CURRENT_MAJOR} -> ${TARGET_MAJOR} completed successfully!"
