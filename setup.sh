#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
# Penpot MCP Server — Guided Setup
# ═══════════════════════════════════════════════════════════

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()  { echo -e "${GREEN}[+]${RESET} $1"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $1"; }
err()   { echo -e "${RED}[x]${RESET} $1"; }
header() { echo -e "\n${BOLD}── $1 ──${RESET}\n"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║   Penpot MCP Server — Setup              ║"
echo "║   AI-powered design tool access           ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Check prerequisites ─────────────────────────────────

header "Checking prerequisites"

if ! command -v docker &>/dev/null; then
    err "Docker is not installed. Install it from https://docs.docker.com/get-docker/"
    exit 1
fi
info "Docker found: $(docker --version | head -1)"

if ! docker compose version &>/dev/null; then
    err "Docker Compose v2 is not installed."
    exit 1
fi
info "Docker Compose found: $(docker compose version | head -1)"

# Check if Penpot is running
if docker ps --format '{{.Names}}' | grep -q 'penpot-backend'; then
    info "Penpot backend is running"
else
    warn "Penpot backend doesn't seem to be running."
    warn "Make sure your Penpot stack is up before starting the MCP server."
fi

if docker ps --format '{{.Names}}' | grep -q 'penpot-postgres'; then
    info "Penpot PostgreSQL is running"
else
    warn "Penpot PostgreSQL doesn't seem to be running."
fi

# ── Configuration ────────────────────────────────────────

header "Configuration"

if [ -f .env ]; then
    warn ".env file already exists."
    read -rp "Overwrite? (y/N): " overwrite
    if [[ ! "$overwrite" =~ ^[yY]$ ]]; then
        info "Keeping existing .env"
    else
        rm .env
    fi
fi

if [ ! -f .env ]; then
    cp .env.example .env
    info "Created .env from .env.example"

    echo ""
    echo "Please provide the following configuration values."
    echo "Press Enter to accept the default shown in [brackets]."
    echo ""

    # Public URL
    read -rp "Penpot public URL [http://localhost:9001]: " penpot_url
    penpot_url="${penpot_url:-http://localhost:9001}"
    sed -i "s|PENPOT_PUBLIC_URL=.*|PENPOT_PUBLIC_URL=${penpot_url}|" .env
    info "Public URL: ${penpot_url}"

    # Access Token
    echo ""
    echo "An access token is needed to authenticate with Penpot."
    echo "To create one:"
    echo "  1. Open Penpot in your browser"
    echo "  2. Click your avatar (bottom-left) → Access Tokens"
    echo "  3. Click 'Generate new token'"
    echo "  4. Copy the token"
    echo ""
    echo "Note: You need 'enable-access-tokens' in PENPOT_FLAGS."
    echo "Add it to your Penpot .env file and restart if needed."
    echo ""
    read -rp "Access token (or press Enter to skip): " token
    if [ -n "$token" ]; then
        sed -i "s|PENPOT_ACCESS_TOKEN=.*|PENPOT_ACCESS_TOKEN=${token}|" .env
        info "Access token configured"
    else
        warn "No access token. You can add it to .env later."
        echo ""
        echo "Alternatively, provide email/password credentials:"
        read -rp "Penpot email (or Enter to skip): " email
        if [ -n "$email" ]; then
            read -rsp "Penpot password: " password
            echo ""
            sed -i "s|PENPOT_EMAIL=.*|PENPOT_EMAIL=${email}|" .env
            sed -i "s|PENPOT_PASSWORD=.*|PENPOT_PASSWORD=${password}|" .env
            info "Email/password credentials configured"
        fi
    fi

    # Database password
    echo ""
    echo "The database password is in your Penpot docker-compose.yml"
    echo "or .env file (look for POSTGRES_PASSWORD or PENPOT_DB_PASS)."
    echo ""
    read -rsp "PostgreSQL password: " db_pass
    echo ""
    if [ -n "$db_pass" ]; then
        sed -i "s|PENPOT_DB_PASS=.*|PENPOT_DB_PASS=${db_pass}|" .env
        info "Database password configured"
    else
        warn "No database password set. Add it to .env before starting."
    fi

    # MCP Port
    read -rp "MCP server port [8787]: " mcp_port
    mcp_port="${mcp_port:-8787}"
    sed -i "s|MCP_PORT=.*|MCP_PORT=${mcp_port}|" .env
    info "MCP port: ${mcp_port}"
fi

# ── Build ────────────────────────────────────────────────

header "Building Docker image"

docker compose build penpot-mcp 2>/dev/null || docker build -t penpot-mcp .
info "Docker image built successfully"

# ── Start ────────────────────────────────────────────────

header "Starting MCP server"

# Try docker compose first (if service is in compose file)
if docker compose config --services 2>/dev/null | grep -q penpot-mcp; then
    docker compose up -d penpot-mcp
    info "Started via docker compose"
else
    warn "penpot-mcp not found in docker-compose.yml"
    warn "See docker-compose.penpot.yml for the service definition to add."
    echo ""
    echo "You can also run it standalone:"
    echo "  docker run -d --name penpot-mcp \\"
    echo "    --network penpot \\"
    echo "    --env-file .env \\"
    echo "    -p 127.0.0.1:${mcp_port:-8787}:8787 \\"
    echo "    penpot-mcp"
    exit 0
fi

# ── Health check ─────────────────────────────────────────

header "Health check"

sleep 3

mcp_port=$(grep MCP_PORT .env 2>/dev/null | cut -d= -f2)
mcp_port="${mcp_port:-8787}"

if curl -sf "http://localhost:${mcp_port}/mcp" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup","version":"1.0"}}}' \
    > /dev/null 2>&1; then
    info "MCP server is running on port ${mcp_port}"
else
    warn "Server may still be starting. Check logs with: docker logs penpot-mcp"
fi

# ── Done ─────────────────────────────────────────────────

header "Setup complete"

echo "To connect Claude Code, add this to your .mcp.json:"
echo ""
echo '  {'
echo '    "mcpServers": {'
echo '      "penpot": {'
echo '        "type": "streamable-http",'
echo "        \"url\": \"http://localhost:${mcp_port}/mcp\""
echo '      }'
echo '    }'
echo '  }'
echo ""
echo "Then restart Claude Code and run /mcp to verify 66 tools are loaded."
echo ""
info "Done!"
