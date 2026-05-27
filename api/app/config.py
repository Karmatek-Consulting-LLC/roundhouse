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

    # Workload backend selector.
    mcp_orchestrator: str = Field(default="docker", alias="MCP_ORCHESTRATOR")

    # Force the docker backend's mode instead of probing `docker info`.
    # "auto" (default) detects from the daemon; "standalone" / "swarm" override.
    # Use "standalone" when the host is a swarm node but the platform is being
    # run via plain `docker compose`.
    mcp_docker_mode: str = Field(default="auto", alias="MCP_DOCKER_MODE")

    # ---- Kubernetes (only used when mcp_orchestrator == "kubernetes") ----
    mcp_k8s_api_url: str = Field(
        default="https://kubernetes.default.svc", alias="MCP_K8S_API_URL"
    )
    mcp_k8s_namespace: str = Field(default="mcp-servers", alias="MCP_K8S_NAMESPACE")
    mcp_k8s_token_path: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/token",
        alias="MCP_K8S_TOKEN_PATH",
    )
    mcp_k8s_ca_path: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        alias="MCP_K8S_CA_PATH",
    )
    mcp_k8s_image_pull_secret: str = Field(default="", alias="MCP_K8S_IMAGE_PULL_SECRET")

    # ---- Image builder (Kubernetes backend) ----
    # "docker" -> talk to MCP_DOCKER_HOST (legacy). "kaniko" -> launch one-shot Jobs.
    mcp_k8s_builder: str = Field(default="docker", alias="MCP_K8S_BUILDER")
    mcp_k8s_builder_namespace: str = Field(default="", alias="MCP_K8S_BUILDER_NAMESPACE")
    mcp_k8s_builder_image: str = Field(
        default="gcr.io/kaniko-project/executor:latest", alias="MCP_K8S_BUILDER_IMAGE"
    )
    mcp_k8s_builder_pvc: str = Field(default="", alias="MCP_K8S_BUILDER_PVC")
    mcp_k8s_builder_registry_secret: str = Field(
        default="", alias="MCP_K8S_BUILDER_REGISTRY_SECRET"
    )
    mcp_k8s_builder_timeout: int = Field(default=600, alias="MCP_K8S_BUILDER_TIMEOUT")
    # Set via Downward API (fieldRef: spec.nodeName) in the Helm chart so kaniko
    # Jobs land on the same node as the api pod and can share the RWO PVC.
    node_name: str = Field(default="", alias="NODE_NAME")
    pod_namespace: str = Field(default="", alias="POD_NAMESPACE")

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
