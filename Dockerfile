ARG PYTHON_IMAGE_TAG=3.13-alpine
ARG UV_VERSION=0.12.10
ARG PENPOT_MCP_VERSION=0.3.0

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

ARG PENPOT_MCP_VERSION=0.3.0
LABEL org.opencontainers.image.version="${PENPOT_MCP_VERSION}"
ENV PENPOT_MCP_VERSION="${PENPOT_MCP_VERSION}" PATH="/app/.venv/bin:$PATH"

RUN if command -v apk >/dev/null 2>&1; then \
        DEPS=$(apk info -R .python-rundeps | tail -n +2 | grep -v sqlite | tr '\n' ' ') && \
        apk add --no-cache --virtual .python-rundeps $DEPS && \
        apk upgrade --no-cache && \
        apk upgrade --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main musl musl-utils libcrypto3 libssl3 && \
        adduser -D -h /home/appuser -s /sbin/nologin appuser; \
    else \
        apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
        useradd --create-home --shell /usr/sbin/nologin appuser; \
    fi && \
    rm -rf /usr/local/lib/python*/ensurepip \
           /usr/local/lib/python*/lib-dynload/_sqlite3* \
           /usr/local/lib/python*/sqlite3 \
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
