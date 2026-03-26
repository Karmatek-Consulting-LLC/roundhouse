import os
from pathlib import Path


DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "mcp-network")
SERVERS_DATA_DIR = Path(os.environ.get("SERVERS_DATA_DIR", "/app/data/servers"))
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", "/app/templates"))
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:3080")
