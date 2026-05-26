"""Tear down every container/service labelled mcp-platform.managed=true.

Invoked from the dev/prod entrypoint's SIGTERM trap so `docker compose down`
removes spawned MCP servers along with the platform. DB rows and on-disk
spec files are kept - redeploying after next boot is intentional.

Usage: `python -m app.cleanup` (no args)."""
from __future__ import annotations

import logging
import sys

from app.services.docker import get_docker

logger = logging.getLogger("mcp-platform-cleanup")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    docker = get_docker()
    try:
        servers = docker.list_servers()
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to list managed servers: %s", e)
        return 1
    if not servers:
        logger.info("No managed servers running - nothing to clean up.")
        return 0

    mode = "swarm service" if docker.swarm_mode() else "container"
    logger.info("Removing %d managed %s(s)...", len(servers), mode)

    failed = 0
    for s in servers:
        name = s.get("name") or "(unknown)"
        try:
            docker.remove_server(name)
            logger.info("  ✓ %s", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.info("  ✗ %s: %s", name, e)

    if failed:
        logger.warning(
            "%d removal(s) failed - inspect `docker ps --filter label=mcp-platform.managed=true`.",
            failed,
        )
        return 1
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
