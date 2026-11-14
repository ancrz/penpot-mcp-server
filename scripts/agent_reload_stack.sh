#!/usr/bin/env bash
# Runbook: Build and reload the Penpot MCP Server + AI Gateway container in the Penpot stack.
# Environment variables:
#   PENPOT_STACK_DIR: Path to the Penpot docker-compose stack (default: ../penpot)
#   PENPOT_MCP_VERSION: Image version tag (default: 0.5.0)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PENPOT_STACK_DIR="${PENPOT_STACK_DIR:-$(cd "${REPO_ROOT}/../penpot" && pwd)}"
PENPOT_MCP_VERSION="${PENPOT_MCP_VERSION:-0.5.0}"

echo "[agent_reload_stack] Building penpot-mcp:${PENPOT_MCP_VERSION} from ${REPO_ROOT}..."
docker build \
  --build-arg PENPOT_MCP_VERSION="${PENPOT_MCP_VERSION}" \
  -t "penpot-mcp:${PENPOT_MCP_VERSION}" \
  "${REPO_ROOT}"

if [[ -f "${PENPOT_STACK_DIR}/docker-compose.yml" ]]; then
  echo "[agent_reload_stack] Reloading penpot-mcp service in ${PENPOT_STACK_DIR}..."
  docker compose -f "${PENPOT_STACK_DIR}/docker-compose.yml" up -d --build penpot-mcp
  echo "[agent_reload_stack] Verifying health endpoint..."
  for _ in {1..15}; do
    if curl -fsS http://127.0.0.1:8787/ >/dev/null 2>&1; then
      curl -fsS http://127.0.0.1:8787/
      echo ""
      exit 0
    fi
    sleep 1
  done
  echo "[agent_reload_stack] Warning: healthcheck did not answer within 15s" >&2
  exit 1
fi
