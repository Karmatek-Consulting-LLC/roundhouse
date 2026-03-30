from __future__ import annotations

import logging
from pathlib import Path

import docker
from docker.errors import APIError, ImageNotFound, NotFound

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
        self._swarm_mode: bool | None = None

    @property
    def swarm_mode(self) -> bool:
        if self._swarm_mode is None:
            try:
                info = self.client.info()
                swarm = info.get("Swarm", {})
                self._swarm_mode = swarm.get("LocalNodeState") == "active"
            except Exception:
                self._swarm_mode = False
            logger.info("Docker mode: %s", "swarm" if self._swarm_mode else "standalone")
        return self._swarm_mode

    def _service_name(self, server_name: str) -> str:
        return f"{CONTAINER_PREFIX}{server_name}"

    def _image_tag(self, server_name: str) -> str:
        return f"mcp-server-{server_name}:latest"

    def _traefik_labels(self, server_name: str) -> dict[str, str]:
        router = f"mcp-{server_name}"
        labels = {
            "traefik.enable": "true",
            f"traefik.http.routers.{router}.rule": f"PathPrefix(`/s/{server_name}`)",
            f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes": f"/s/{server_name}",
            f"traefik.http.routers.{router}.middlewares": f"{router}-strip",
            f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
        }
        if self.swarm_mode:
            labels[f"traefik.http.routers.{router}.entrypoints"] = "web,websecure"
        else:
            labels[f"traefik.http.routers.{router}.entrypoints"] = "web,websecure"
        return labels

    def _all_labels(self, server_name: str, template_name: str) -> dict[str, str]:
        return {
            LABEL_MANAGED: "true",
            LABEL_SERVER_NAME: server_name,
            LABEL_TEMPLATE: template_name,
            **self._traefik_labels(server_name),
        }

    # --- Build ---

    def _build_image(self, server_name: str, build_context: Path) -> str:
        tag = self._image_tag(server_name)
        logger.info("Building image %s from %s", tag, build_context)
        self.client.images.build(path=str(build_context), tag=tag, rm=True)
        return tag

    # --- Standalone (container) mode ---

    def _create_container(
        self, server_name: str, tag: str, template_name: str, env_vars: dict[str, str]
    ) -> dict:
        name = self._service_name(server_name)
        labels = self._all_labels(server_name, template_name)
        logger.info("Creating container %s", name)
        container = self.client.containers.run(
            tag,
            detach=True,
            name=name,
            network=self.network_name,
            labels=labels,
            environment=env_vars,
        )
        return self._container_to_dict(container)

    def _list_containers(self) -> list[dict]:
        containers = self.client.containers.list(
            all=True, filters={"label": f"{LABEL_MANAGED}=true"}
        )
        return [self._container_to_dict(c) for c in containers]

    def _get_container(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._service_name(server_name))
            if container.labels.get(LABEL_MANAGED) != "true":
                return None
            return self._container_to_dict(container)
        except NotFound:
            return None

    def _start_container(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._service_name(server_name))
            container.start()
            container.reload()
            return self._container_to_dict(container)
        except NotFound:
            return None

    def _stop_container(self, server_name: str) -> dict | None:
        try:
            container = self.client.containers.get(self._service_name(server_name))
            container.stop()
            container.reload()
            return self._container_to_dict(container)
        except NotFound:
            return None

    def _remove_container(self, server_name: str) -> bool:
        try:
            container = self.client.containers.get(self._service_name(server_name))
            container.stop()
            container.remove()
        except NotFound:
            return False
        return True

    def _container_to_dict(self, container) -> dict:
        labels = container.labels
        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": container.status,
            "created_at": container.attrs.get("Created", ""),
        }

    # --- Swarm (service) mode ---

    def _create_service(
        self, server_name: str, tag: str, template_name: str, env_vars: dict[str, str]
    ) -> dict:
        name = self._service_name(server_name)
        labels = self._all_labels(server_name, template_name)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]

        logger.info("Creating swarm service %s", name)
        service = self.client.services.create(
            tag,
            name=name,
            labels=labels,
            env=env_list,
            networks=[self.network_name],
            endpoint_spec=docker.types.EndpointSpec(mode="vip"),
        )
        return self._service_to_dict(service)

    def _list_services(self) -> list[dict]:
        services = self.client.services.list(
            filters={"label": f"{LABEL_MANAGED}=true"}
        )
        return [self._service_to_dict(s) for s in services]

    def _get_service(self, server_name: str) -> dict | None:
        name = self._service_name(server_name)
        try:
            services = self.client.services.list(filters={"name": name})
            for s in services:
                if s.name == name and s.attrs.get("Spec", {}).get("Labels", {}).get(LABEL_MANAGED) == "true":
                    return self._service_to_dict(s)
        except (NotFound, APIError):
            pass
        return None

    def _start_service(self, server_name: str) -> dict | None:
        """Scale service to 1 replica."""
        svc = self._find_service(server_name)
        if not svc:
            return None
        svc.scale(1)
        svc.reload()
        return self._service_to_dict(svc)

    def _stop_service(self, server_name: str) -> dict | None:
        """Scale service to 0 replicas."""
        svc = self._find_service(server_name)
        if not svc:
            return None
        svc.scale(0)
        svc.reload()
        return self._service_to_dict(svc)

    def _remove_service(self, server_name: str) -> bool:
        svc = self._find_service(server_name)
        if not svc:
            return False
        svc.remove()
        return True

    def _find_service(self, server_name: str):
        name = self._service_name(server_name)
        try:
            services = self.client.services.list(filters={"name": name})
            for s in services:
                if s.name == name:
                    return s
        except (NotFound, APIError):
            pass
        return None

    def _service_to_dict(self, service) -> dict:
        spec = service.attrs.get("Spec", {})
        labels = spec.get("Labels", {})
        created = service.attrs.get("CreatedAt", "")

        # Determine status from replicas
        replicas = spec.get("Mode", {}).get("Replicated", {}).get("Replicas", 0)
        status = "running" if replicas > 0 else "stopped"

        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": status,
            "created_at": created,
        }

    # --- Public API (delegates based on mode) ---

    def build_and_start(
        self,
        server_name: str,
        build_context: Path,
        template_name: str,
        env_vars: dict[str, str] | None = None,
    ) -> dict:
        tag = self._build_image(server_name, build_context)
        ev = env_vars or {}
        if self.swarm_mode:
            return self._create_service(server_name, tag, template_name, ev)
        return self._create_container(server_name, tag, template_name, ev)

    def list_servers(self) -> list[dict]:
        if self.swarm_mode:
            return self._list_services()
        return self._list_containers()

    def get_server(self, server_name: str) -> dict | None:
        if self.swarm_mode:
            return self._get_service(server_name)
        return self._get_container(server_name)

    def start_server(self, server_name: str) -> dict | None:
        if self.swarm_mode:
            return self._start_service(server_name)
        return self._start_container(server_name)

    def stop_server(self, server_name: str) -> dict | None:
        if self.swarm_mode:
            return self._stop_service(server_name)
        return self._stop_container(server_name)

    def remove_server(self, server_name: str) -> bool:
        if self.swarm_mode:
            result = self._remove_service(server_name)
        else:
            result = self._remove_container(server_name)

        # Clean up image
        try:
            self.client.images.remove(self._image_tag(server_name))
        except ImageNotFound:
            pass

        return result
