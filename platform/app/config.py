import os
from pathlib import Path


def _read_secret(name: str, env_fallback: str | None = None, default: str | None = None) -> str | None:
    """Read a Docker secret from /run/secrets/, falling back to env var."""
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    if env_fallback:
        val = os.environ.get(env_fallback)
        if val is not None:
            return val
    return default


def _read_secret_required(name: str, env_fallback: str) -> str:
    val = _read_secret(name, env_fallback)
    if val is None:
        raise RuntimeError(f"Missing secret '{name}' and env var '{env_fallback}'")
    return val


DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "mcp-network")
SERVERS_DATA_DIR = Path(os.environ.get("SERVERS_DATA_DIR", "/app/data/servers"))
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", "/app/templates"))
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:3080")

# Database - build URL from secret if password is a placeholder
_db_url = os.environ.get("DATABASE_URL", "postgresql://mcp:mcp@postgres:5432/mcp")
_pg_password = _read_secret("mcp_postgres_password", "POSTGRES_PASSWORD", "mcp")
if "__SECRET__" in _db_url and _pg_password:
    _db_url = _db_url.replace("__SECRET__", _pg_password)
DATABASE_URL = _db_url

JWT_SECRET_KEY = _read_secret_required("mcp_jwt_secret_key", "JWT_SECRET_KEY")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))

ADMIN_EMAIL = _read_secret("mcp_admin_email", "ADMIN_EMAIL")
ADMIN_PASSWORD = _read_secret("mcp_admin_password", "ADMIN_PASSWORD")

TRAEFIK_DYNAMIC_DIR = Path(os.environ.get("TRAEFIK_DYNAMIC_DIR", "/app/traefik/dynamic"))
TRAEFIK_CERTS_DIR = Path(os.environ.get("TRAEFIK_CERTS_DIR", "/app/traefik/certs"))
