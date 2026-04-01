from __future__ import annotations

import logging
from pathlib import Path

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from app.config import DOCKER_NETWORK, TRAEFIK_ENTRYPOINTS

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

    def _image_tag(self, server_name: str, registry_prefix: str | None = None) -> str:
        name = f"mcp-server-{server_name}"
        if registry_prefix:
            p = registry_prefix.strip().rstrip("/")
            return f"{p}/{name}:latest"
        return f"{name}:latest"

    def _traefik_labels(self, server_name: str) -> dict[str, str]:
        router = f"mcp-{server_name}"
        labels = {
            "traefik.enable": "true",
            f"traefik.http.routers.{router}.rule": f"PathPrefix(`/s/{server_name}`)",
            f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes": f"/s/{server_name}",
            f"traefik.http.routers.{router}.middlewares": f"{router}-strip",
            f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
        }
        labels[f"traefik.http.routers.{router}.entrypoints"] = TRAEFIK_ENTRYPOINTS
        return labels

    def _all_labels(self, server_name: str, template_name: str) -> dict[str, str]:
        return {
            LABEL_MANAGED: "true",
            LABEL_SERVER_NAME: server_name,
            LABEL_TEMPLATE: template_name,
            **self._traefik_labels(server_name),
        }

    # --- Build ---

    def _push_image(
        self, full_tag: str, auth_config: dict[str, str] | None = None
    ) -> None:
        """Push image to registry. Uses ``auth_config`` when set; else Docker config on the host."""
        if ":" not in full_tag:
            full_tag = f"{full_tag}:latest"
        repo, tag = full_tag.rsplit(":", 1)
        logger.info("Pushing image %s", full_tag)
        for line in self.client.api.push(
            repo,
            tag=tag,
            stream=True,
            decode=True,
            auth_config=auth_config,
        ):
            if isinstance(line, dict):
                err = line.get("error")
                ed = line.get("errorDetail")
                if isinstance(ed, dict):
                    err = ed.get("message") or err
                elif ed is not None and not err:
                    err = str(ed)
                if err:
                    raise RuntimeError(f"Registry push failed: {err}")
                if line.get("status") and "Pushed" in str(line.get("status", "")):
                    logger.info("Push: %s", line.get("status"))

    def _build_image(
        self,
        server_name: str,
        build_context: Path,
        registry_prefix: str | None,
        registry_auth: dict[str, str] | None = None,
    ) -> str:
        tag = self._image_tag(server_name, registry_prefix)
        logger.info("Building image %s from %s", tag, build_context)
        self.client.images.build(path=str(build_context), tag=tag, rm=True)
        if registry_prefix:
            self._push_image(tag, auth_config=registry_auth)
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
        running = container.status == "running"
        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": container.status,
            "created_at": container.attrs.get("Created", ""),
            "replicas_running": 1 if running else 0,
            "placement": [],
        }

    # --- Swarm (service) mode ---

    def _create_service(
        self,
        server_name: str,
        tag: str,
        template_name: str,
        env_vars: dict[str, str],
        replicas: int,
    ) -> dict:
        name = self._service_name(server_name)
        labels = self._all_labels(server_name, template_name)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]

        logger.info("Creating swarm service %s (replicas=%s)", name, replicas)
        service = self.client.services.create(
            tag,
            name=name,
            labels=labels,
            env=env_list,
            networks=[self.network_name],
            endpoint_spec=docker.types.EndpointSpec(mode="vip"),
            mode=docker.types.ServiceMode(mode="replicated", replicas=replicas),
        )
        service.reload()
        return self._service_to_dict(service, include_placement=True)

    def _list_services(self) -> list[dict]:
        services = self.client.services.list(
            filters={"label": f"{LABEL_MANAGED}=true"}
        )
        return [self._service_to_dict(s, include_placement=False) for s in services]

    def _get_service(self, server_name: str) -> dict | None:
        name = self._service_name(server_name)
        try:
            services = self.client.services.list(filters={"name": name})
            for s in services:
                if s.name == name and s.attrs.get("Spec", {}).get("Labels", {}).get(LABEL_MANAGED) == "true":
                    return self._service_to_dict(s, include_placement=True)
        except (NotFound, APIError):
            pass
        return None

    def _start_service(self, server_name: str, replicas: int) -> dict | None:
        """Scale service to the given replica count."""
        svc = self._find_service(server_name)
        if not svc:
            return None
        svc.scale(replicas)
        svc.reload()
        return self._service_to_dict(svc, include_placement=True)

    def _stop_service(self, server_name: str) -> dict | None:
        """Scale service to 0 replicas."""
        svc = self._find_service(server_name)
        if not svc:
            return None
        svc.scale(0)
        svc.reload()
        return self._service_to_dict(svc, include_placement=False)

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

    def _task_placement_for_service(self, service) -> list[dict]:
        """List Swarm tasks with node names (best-effort)."""
        try:
            tasks_raw = self.client.api.tasks(filters={"service": service.id})
        except APIError:
            return []

        node_map: dict[str, str] = {}
        try:
            for n in self.client.nodes.list():
                node_map[n.id] = n.attrs.get("Description", {}).get("Hostname", n.id)
        except APIError:
            pass

        out: list[dict] = []
        for t in tasks_raw:
            task_id = t.get("ID", "")
            status = t.get("Status", {}) or {}
            state = status.get("State", "unknown")
            err = status.get("Err") or None
            if err == "":
                err = None
            node_id = t.get("NodeID") or ""
            slot = t.get("Slot")
            out.append(
                {
                    "task_id": task_id,
                    "node_id": node_id,
                    "node_name": node_map.get(node_id) if node_id else None,
                    "state": state,
                    "slot": slot,
                    "error": err,
                }
            )
        return out

    def _service_to_dict(self, service, include_placement: bool = False) -> dict:
        spec = service.attrs.get("Spec", {})
        labels = spec.get("Labels", {})
        created = service.attrs.get("CreatedAt", "")

        # Determine status from replicas
        replicas = spec.get("Mode", {}).get("Replicated", {}).get("Replicas", 0)
        status = "running" if replicas > 0 else "stopped"

        placement: list[dict] = []
        if include_placement and self.swarm_mode:
            placement = self._task_placement_for_service(service)

        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": status,
            "created_at": created,
            "replicas_running": replicas,
            "placement": placement,
        }

    # --- Public API (delegates based on mode) ---

    def build_and_start(
        self,
        server_name: str,
        build_context: Path,
        template_name: str,
        env_vars: dict[str, str] | None = None,
        replicas: int = 1,
        registry_prefix: str | None = None,
        registry_auth: dict[str, str] | None = None,
    ) -> dict:
        tag = self._build_image(
            server_name, build_context, registry_prefix, registry_auth=registry_auth
        )
        ev = env_vars or {}
        if self.swarm_mode:
            return self._create_service(server_name, tag, template_name, ev, replicas)
        return self._create_container(server_name, tag, template_name, ev)

    def list_servers(self) -> list[dict]:
        if self.swarm_mode:
            return self._list_services()
        return self._list_containers()

    def get_server(self, server_name: str) -> dict | None:
        if self.swarm_mode:
            return self._get_service(server_name)
        return self._get_container(server_name)

    def start_server(self, server_name: str, replicas: int = 1) -> dict | None:
        if self.swarm_mode:
            return self._start_service(server_name, replicas)
        return self._start_container(server_name)

    def scale_server(self, server_name: str, replicas: int) -> dict | None:
        """Set desired replica count for a running Swarm service."""
        if not self.swarm_mode:
            return None
        svc = self._find_service(server_name)
        if not svc:
            return None
        svc.scale(replicas)
        svc.reload()
        return self._service_to_dict(svc, include_placement=True)

    def stop_server(self, server_name: str) -> dict | None:
        if self.swarm_mode:
            return self._stop_service(server_name)
        return self._stop_container(server_name)

    def remove_server(
        self, server_name: str, registry_prefix: str | None = None
    ) -> bool:
        if self.swarm_mode:
            result = self._remove_service(server_name)
        else:
            result = self._remove_container(server_name)

        # Best-effort image cleanup. Redeploy must not fail because a Swarm task or
        # stopped container still references the tag (409). The following build retags
        # the same name anyway.
        tag = self._image_tag(server_name, registry_prefix)
        try:
            self.client.images.remove(tag, force=True)
        except ImageNotFound:
            pass
        except APIError as e:
            logger.warning(
                "Skipping removal of image %s (%s); it may still be referenced by a container",
                tag,
                e.explanation or str(e),
            )

        return result

    def get_server_logs(self, server_name: str, tail: int = 200) -> str:
        """Return recent stdout/stderr from the MCP server container or swarm service."""
        name = self._service_name(server_name)
        tail = max(1, min(tail, 5000))
        if self.swarm_mode:
            svc = self._find_service(server_name)
            if not svc:
                raise ValueError(f"Server '{server_name}' not found")
            raw = self.client.api.service_logs(
                svc.id,
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=True,
            )
        else:
            try:
                container = self.client.containers.get(name)
            except NotFound:
                raise ValueError(f"Server '{server_name}' not found")
            raw = container.logs(tail=tail, timestamps=True)
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8", errors="replace")
        if hasattr(raw, "__iter__"):
            return b"".join(raw).decode("utf-8", errors="replace")
        return str(raw)
