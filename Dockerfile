FROM python:3.13-slim AS base

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for Docker layer caching
COPY pyproject.toml ./

# Install dependencies (no dev deps in production)
RUN uv sync --no-dev --no-install-project

# Copy source code
COPY src/ src/

# Install the project itself
RUN uv sync --no-dev

# Expose MCP port
EXPOSE 8787
EXPOSE 4402

# Run the MCP server
CMD ["uv", "run", "penpot-mcp"]
