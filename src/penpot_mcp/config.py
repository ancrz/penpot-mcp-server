"""Configuration for Penpot MCP server."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Penpot connection
    penpot_base_url: str = "http://penpot-frontend:8080"
    penpot_public_url: str = "http://localhost:9001"

    # Auth - access token (preferred)
    penpot_access_token: str = ""

    # Auth - credentials (fallback)
    penpot_email: str = ""
    penpot_password: str = ""

    # PostgreSQL direct access
    penpot_db_host: str = "penpot-postgres"
    penpot_db_port: int = 5432
    penpot_db_name: str = "penpot"
    penpot_db_user: str = "penpot"
    penpot_db_pass: str = ""

    # MCP Server
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8787
    mcp_log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

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
