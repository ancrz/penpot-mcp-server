FROM python:3.13-slim AS base

# Install uv from pinned official image for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /usr/local/bin/uv

WORKDIR /app

# Create unprivileged runtime user
RUN useradd --create-home --shell /bin/bash appuser

# Copy dependency manifest and lockfile first for Docker layer caching
COPY pyproject.toml uv.lock ./

# Install locked production dependencies without project
RUN uv sync --locked --no-dev --no-install-project

# Copy source code and install the project itself
COPY src/ src/
RUN uv sync --locked --no-dev && chown -R appuser:appuser /app

USER appuser

# Expose MCP HTTP port and optional WebSocket plugin port
EXPOSE 8787
EXPOSE 4402

# Run the installed entrypoint directly without runtime re-sync
CMD ["/app/.venv/bin/penpot-mcp"]
