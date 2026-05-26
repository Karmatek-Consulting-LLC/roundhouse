from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config. Environment variable names match the Laravel app's
    so the docker-compose env block transfers over unchanged."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (Postgres in docker-compose; SQLite for local-only dev).
    db_connection: str = Field(default="pgsql", alias="DB_CONNECTION")
    db_host: str = Field(default="postgres", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_database: str = Field(default="mcp", alias="DB_DATABASE")
    db_username: str = Field(default="mcp", alias="DB_USERNAME")
    db_password: str = Field(default="mcp", alias="DB_PASSWORD")

    # App-level secret. Used to derive the symmetric key for ServerToken
    # encryption (compatible with Laravel's `base64:...` APP_KEY format).
    app_key: str = Field(default="", alias="APP_KEY")

    # Auth tokens
    sanctum_token_expiration_minutes: int = Field(
        default=1440, alias="SANCTUM_TOKEN_EXPIRATION"
    )

    # Initial admin (seeded once on first run if no users exist).
    admin_email: str = Field(default="admin@mcp.local", alias="ADMIN_EMAIL")
    admin_password: str = Field(default="admin", alias="ADMIN_PASSWORD")

    # MCP platform
    mcp_base_url: str = Field(default="http://localhost:3080", alias="MCP_BASE_URL")
    mcp_docker_network: str = Field(default="mcp-network", alias="MCP_DOCKER_NETWORK")
    mcp_docker_host: str = Field(
        default="/var/run/docker.sock", alias="MCP_DOCKER_HOST"
    )
    mcp_docker_socket: str = Field(
        default="/var/run/docker.sock", alias="MCP_DOCKER_SOCKET"
    )
    mcp_servers_data_dir: str = Field(
        default="/var/lib/mcp-platform/servers", alias="MCP_SERVERS_DATA_DIR"
    )
    mcp_templates_dir: str = Field(
        default="/var/lib/mcp-platform/templates", alias="MCP_TEMPLATES_DIR"
    )
    mcp_traefik_dynamic_dir: str = Field(
        default="/var/lib/mcp-platform/traefik-dynamic",
        alias="MCP_TRAEFIK_DYNAMIC_DIR",
    )
    mcp_traefik_entrypoints: str = Field(
        default="web", alias="MCP_TRAEFIK_ENTRYPOINTS"
    )
    mcp_default_server_replicas: int = Field(
        default=1, alias="MCP_DEFAULT_SERVER_REPLICAS"
    )
    mcp_max_server_replicas: int = Field(
        default=32, alias="MCP_MAX_SERVER_REPLICAS"
    )

    @property
    def docker_host(self) -> str:
        # MCP_DOCKER_HOST wins; MCP_DOCKER_SOCKET kept for backwards compat with
        # the Laravel app's env contract.
        return self.mcp_docker_host or self.mcp_docker_socket

    @property
    def db_url(self) -> str:
        if self.db_connection == "sqlite":
            return f"sqlite:///{self.db_database}"
        # psycopg v3 driver name is `psycopg`.
        return (
            f"postgresql+psycopg://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def servers_dir() -> Path:
    p = Path(get_settings().mcp_servers_data_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def templates_dir() -> Path:
    return Path(get_settings().mcp_templates_dir)


def traefik_dynamic_dir() -> Path:
    p = Path(get_settings().mcp_traefik_dynamic_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p
