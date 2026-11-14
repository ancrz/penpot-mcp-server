"""Configuration for Penpot MCP server."""

from __future__ import annotations

from pydantic_settings import BaseSettings

from penpot_mcp import __version__


class Settings(BaseSettings):
    # Version & protocol (parameterized via .env for builds, upgrades, and runtime)
    penpot_mcp_version: str = __version__
    mcp_protocol_version: str = "2025-03-26"
    mcp_tool_mode: str = "compressed"

    # Penpot connection
    penpot_base_url: str = "http://penpot-frontend:8080"
    penpot_public_url: str = "http://localhost:9001"
    ssrf_allow_private_media: bool = False

    # Auth - access token (preferred)
    penpot_access_token: str = ""

    # Auth - credentials (fallback, also used by /api/export cookie auth)
    penpot_email: str = ""
    penpot_password: str = ""

    # PostgreSQL direct access
    # Default is 'penpot' for backward compatibility with existing installations;
    # production deployments should set PENPOT_DB_USER=penpot_mcp (read-only role).
    penpot_db_host: str = "penpot-postgres"
    penpot_db_port: int = 5432
    penpot_db_name: str = "penpot"
    penpot_db_user: str = "penpot"
    penpot_db_pass: str = ""
    db_statement_timeout_ms: int = 30000

    # MCP Server & DNS-rebinding / Host header protection
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8787
    mcp_log_level: str = "info"
    mcp_allowed_hosts: list[str] = ["127.0.0.1", "localhost", "penpot-mcp"]

    # WebSocket / Plugin channel (opt-in, localhost-bound, token + origin gated)
    ws_enabled: bool = False
    ws_host: str = "127.0.0.1"
    ws_port: int = 4402
    ws_token: str = ""
    plugin_ws_url: str = "ws://localhost:4402"
    plugin_origin: str = "http://localhost:8787"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql://{self.penpot_db_user}:{self.penpot_db_pass}"
            f"@{self.penpot_db_host}:{self.penpot_db_port}/{self.penpot_db_name}"
        )

    @property
    def has_access_token(self) -> bool:
        return bool(self.penpot_access_token)

    @property
    def has_credentials(self) -> bool:
        return bool(self.penpot_email and self.penpot_password)


settings = Settings()
