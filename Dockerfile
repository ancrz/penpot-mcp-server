ARG PYTHON_IMAGE_TAG=3.13-alpine
ARG UV_VERSION=0.12.10
ARG PENPOT_MCP_VERSION=0.2.0

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv_bin
FROM python:${PYTHON_IMAGE_TAG} AS builder

COPY --from=uv_bin /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable

COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_IMAGE_TAG} AS runtime

ARG PENPOT_MCP_VERSION=0.2.0
LABEL org.opencontainers.image.version="${PENPOT_MCP_VERSION}"
ENV PENPOT_MCP_VERSION="${PENPOT_MCP_VERSION}" PATH="/app/.venv/bin:$PATH"

RUN if command -v apk >/dev/null 2>&1; then \
        apk upgrade --no-cache && \
        adduser -D -h /home/appuser -s /sbin/nologin appuser; \
    else \
        apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
        useradd --create-home --shell /usr/sbin/nologin appuser; \
    fi && \
    rm -rf /usr/local/lib/python*/ensurepip \
           /usr/local/lib/python*/site-packages/pip* \
           /usr/local/lib/python*/site-packages/setuptools* \
           /usr/local/lib/python*/site-packages/wheel*

WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

USER appuser

EXPOSE 8787
EXPOSE 4402

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import os, socket; s = socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_PORT', '8787'))), timeout=3); s.close()"]

CMD ["/app/.venv/bin/penpot-mcp"]
