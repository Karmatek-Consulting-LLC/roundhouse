from __future__ import annotations

import logging
from pathlib import Path

import docker
from docker.errors import ImageNotFound, NotFound

from app.config import DOCKER_NETWORK

logger = logging.getLogger(__name__)

LABEL_MANAGED = "mcp-platform.managed"
LABEL_SERVER_NAME = "mcp-platform.server-name"
LABEL_TEMPLATE = "mcp-platform.template"
CONTAINER_PREFIX = "mcp-"


class DockerManager:
    def __init__(self) -> None:
        self.client = docker.from_env()
        self.network_name = DOCKER_NETWORK

    def _container_name(self, server_name: str) -> str:
        return f"{CONTAINER_PREFIX}{server_name}"

    def _image_tag(self, server_name: str) -> str:
        return f"mcp-server-{server_name}:latest"

    def _traefik_labels(self, server_name: str) -> dict[str, str]:
        router = f"mcp-{server_name}"
        return {
            "traefik.enable": "true",
            f"traefik.http.routers.{router}.entrypoints": "web",
            f"traefik.http.routers.{router}.rule": f"PathPrefix(`/mcp/{server_name}`)",
            f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes": f"/mcp/{server_name}",
            f"traefik.http.routers.{router}.middlewares": f"{router}-strip",
            f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
        }

    def build_and_start(
        self, server_name: str, build_context: Path, template_name: str
    ) -> dict:
        tag = self._image_tag(server_name)
        container_name = self._container_name(server_name)

        logger.info("Building image %s from %s", tag, build_context)
        self.client.images.build(path=str(build_context), tag=tag, rm=True)

        labels = {
            LABEL_MANAGED: "true",
            LABEL_SERVER_NAME: server_name,
            LABEL_TEMPLATE: template_name,
            **self._traefik_labels(server_name),
        }

        logger.info("Creating container %s", container_name)
        container = self.client.containers.run(
            tag,
            detach=True,
            name=container_name,
            network=self.network_name,
            labels=labels,
        )
        return self._container_to_dict(container)

    def list_servers(self) -> list[dict]:
        containers = self.client.containers.list(
            all=True, filters={"label": f"{LABEL_MANAGED}=true"}
        )
        return [self._container_to_dict(c) for c in containers]

    def get_server(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._container_name(server_name))
            if container.labels.get(LABEL_MANAGED) != "true":
                return None
            return self._container_to_dict(container)
        except NotFound:
            return None

    def start_server(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._container_name(server_name))
            container.start()
            container.reload()
            return self._container_to_dict(container)
        except NotFound:
            return None

    def stop_server(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._container_name(server_name))
            container.stop()
            container.reload()
            return self._container_to_dict(container)
        except NotFound:
            return None

    def remove_server(self, server_name: str) -> bool:
        try:
            container = self.client.containers.get(self._container_name(server_name))
            container.stop()
            container.remove()
        except NotFound:
            return False

        # Clean up image
        tag = self._image_tag(server_name)
        try:
            self.client.images.remove(tag)
        except ImageNotFound:
            pass

        return True

    def _container_to_dict(self, container) -> dict:
        labels = container.labels
        created = container.attrs.get("Created", "")
        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": container.status,
            "created_at": created,
        }
