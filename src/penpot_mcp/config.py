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

    # Valkey 8.1 read-through cache (DB 1; Penpot uses DB 0)
    valkey_url: str = ""
    valkey_cache_ttl: int = 120

    # MCP Server & DNS-rebinding / Host header protection
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8787
    mcp_log_level: str = "info"
    mcp_allowed_hosts: list[str] = [
        "127.0.0.1",
        "localhost",
        "penpot-mcp",
        "host.docker.internal",
    ]

    # WebSocket / Plugin channel (opt-in, token + origin gated)
    ws_enabled: bool = False
    ws_host: str = "127.0.0.1"
    ws_port: int = 4402
    ws_token: str = ""
    plugin_ws_url: str = "ws://localhost:4402"
    plugin_origin: str = "http://localhost:8787"

    # API Key Vault Encryption (pgcrypto AES-256 + local AES-256-GCM)
    penpot_mcp_encryption_key: str = ""

    # SDK Agent Pipeline & AI Gateway (RE from sdk-agent-pipeline & vanguard-control)
    penpot_agent_mode: str = "auto"
    penpot_agent_model: str = "gemini-3.5-flash-lite"
    penpot_agent_fallback_model: str = "gemini-2.5-flash"
    google_api_key: str = ""
    google_api_key_backup: str = ""
    google_gemini_base_url: str = "https://generativelanguage.googleapis.com"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "gemma4:e2b"
    penpot_agent_rpm: int = 15
    penpot_agent_max_turns: int = 6

    # Agentic Layer Dual-DB & Sandbox Isolation
    # Backends: "penpot_postgres" (default shared DB), "postgres" (2nd PG instance), "sqlite" (local file)
    agent_db_backend: str = "penpot_postgres"
    agent_db_url: str = ""
    sandbox_root: str = "/sandbox"
    sandbox_ephemeral_ttl_sec: int = 1800

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

    @property
    def has_llm_key(self) -> bool:
        return bool(
            self.google_api_key.strip()
            or self.google_api_key_backup.strip()
            or self.openai_api_key.strip()
            or self.anthropic_api_key.strip()
        )


settings = Settings()
