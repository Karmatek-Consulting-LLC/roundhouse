import os
from pathlib import Path


DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "mcp-network")
SERVERS_DATA_DIR = Path(os.environ.get("SERVERS_DATA_DIR", "/app/data/servers"))
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", "/app/templates"))
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:3080")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mcp:mcp@postgres:5432/mcp")
JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
TRAEFIK_DYNAMIC_DIR = Path(os.environ.get("TRAEFIK_DYNAMIC_DIR", "/app/traefik/dynamic"))
TRAEFIK_CERTS_DIR = Path(os.environ.get("TRAEFIK_CERTS_DIR", "/app/traefik/certs"))
