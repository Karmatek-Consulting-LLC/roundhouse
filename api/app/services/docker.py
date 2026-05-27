"""High-level Docker orchestration for MCP servers.

Supports standalone containers and Swarm services through a unified API
(buildAndStart, startServer, stopServer, etc.). Mode is detected once from
`/info` and cached for the lifetime of the client instance."""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.docker_http import DockerError, DockerHttp, DockerNotFoundError, demux_log_frames

logger = logging.getLogger(__name__)


LABEL_MANAGED = "mcp-platform.managed"
LABEL_SERVER_NAME = "mcp-platform.server-name"
LABEL_TEMPLATE = "mcp-platform.template"
CONTAINER_PREFIX = "mcp-"


def _container_name(server_name: str) -> str:
    return CONTAINER_PREFIX + server_name


def image_tag(server_name: str, registry_prefix: str | None = None) -> str:
    name = f"mcp-server-{server_name}"
    if registry_prefix:
        p = registry_prefix.strip().rstrip("/")
        return f"{p}/{name}:latest"
    return f"{name}:latest"


def _split_tag(full_tag: str) -> tuple[str, str]:
    i = full_tag.rfind(":")
    if i < 0:
        return full_tag, "latest"
    return full_tag[:i], full_tag[i + 1 :]


def _encode_auth(auth: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"username": auth["username"], "password": auth["password"]}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")


def _traefik_labels(server_name: str) -> dict[str, str]:
    router = f"mcp-{server_name}"
    entrypoints = get_settings().mcp_traefik_entrypoints
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"PathPrefix(`/s/{server_name}`)",
        f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes": f"/s/{server_name}",
        f"traefik.http.routers.{router}.middlewares": f"{router}-strip",
        f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
        f"traefik.http.routers.{router}.entrypoints": entrypoints,
    }


def _all_labels(server_name: str, template_name: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_SERVER_NAME: server_name,
        LABEL_TEMPLATE: template_name,
        **_traefik_labels(server_name),
    }


def _parse_health_from_summary(status_str: str) -> str | None:
    """Docker's `ps` Status column carries health in parens, e.g.
    'Up 2 minutes (healthy)'. Parse it out without a full inspect."""
    if not isinstance(status_str, str):
        return None
    for token in ("(healthy)", "(unhealthy)", "(starting)", "(health: starting)"):
        if token in status_str:
            return token.strip("()").replace("health: ", "")
    return None


def _container_summary_to_dict(c: dict) -> dict:
    labels = c.get("Labels") or {}
    state = c.get("State") or "unknown"
    running = state == "running"
    import datetime as _dt

    created_at = c.get("Created")
    created_str = ""
    if isinstance(created_at, (int, float)):
        created_str = _dt.datetime.fromtimestamp(int(created_at), tz=_dt.timezone.utc).isoformat()
    return {
        "name": labels.get(LABEL_SERVER_NAME, ""),
        "template": labels.get(LABEL_TEMPLATE, ""),
        "status": state,
        "health": _parse_health_from_summary(c.get("Status") or ""),
        "created_at": created_str,
        "replicas_running": 1 if running else 0,
        "placement": [],
    }


def _container_to_dict(c: dict) -> dict:
    labels = (c.get("Config") or {}).get("Labels") or {}
    state_obj = c.get("State") or {}
    state = state_obj.get("Status")
    if state is None:
        state = "running" if state_obj.get("Running") else "exited"
    running = state == "running"
    health = (state_obj.get("Health") or {}).get("Status")
    restart_count = c.get("RestartCount") if isinstance(c.get("RestartCount"), int) else None
    return {
        "name": labels.get(LABEL_SERVER_NAME, ""),
        "template": labels.get(LABEL_TEMPLATE, ""),
        "status": state,
        "health": health if isinstance(health, str) else None,
        "restart_count": restart_count,
        "created_at": c.get("Created", ""),
        "replicas_running": 1 if running else 0,
        "placement": [],
    }


class DockerClient:
    def __init__(self, http: DockerHttp | None = None):
        cfg = get_settings()
        self._http = http or DockerHttp(cfg.docker_host)
        self._network = cfg.mcp_docker_network
        self._swarm_cache: bool | None = None

    # ---- Mode detection ----

    def swarm_mode(self) -> bool:
        if self._swarm_cache is not None:
            return self._swarm_cache
        info = self._http.get("info")
        state = (info.get("Swarm") or {}).get("LocalNodeState")
        self._swarm_cache = state == "active"
        logger.info("Docker mode: %s", "swarm" if self._swarm_cache else "standalone")
        return self._swarm_cache

    # ---- Image build + push ----

    def build_image(
        self,
        server_name: str,
        build_context: Path | str,
        registry_prefix: str | None = None,
        registry_auth: dict[str, str] | None = None,
    ) -> str:
        tag = image_tag(server_name, registry_prefix)
        logger.info("Building image %s from %s", tag, build_context)
        tar_bytes = self._tar_bytes(Path(build_context))
        frames = self._http.post_stream(
            "build",
            {"t": tag, "rm": "1"},
            tar_bytes,
            {"Content-Type": "application/x-tar"},
        )
        for frame in frames:
            if "error" in frame:
                raise DockerError(f"Image build failed: {frame['error']}")
        if registry_prefix:
            self.push_image(tag, registry_auth)
        return tag

    def push_image(self, full_tag: str, auth: dict[str, str] | None = None) -> None:
        if ":" not in full_tag:
            full_tag += ":latest"
        repo, tag = _split_tag(full_tag)
        logger.info("Pushing image %s", full_tag)
        headers: dict[str, str] = {}
        if auth:
            headers["X-Registry-Auth"] = _encode_auth(auth)
        for frame in self._http.post_stream(
            f"images/{repo}/push", {"tag": tag}, b"", headers
        ):
            err = frame.get("error")
            detail = frame.get("errorDetail")
            if isinstance(detail, dict):
                err = detail.get("message") or err
            elif detail is not None and not err:
                err = str(detail)
            if err:
                raise DockerError(f"Registry push failed: {err}")

    def remove_image(self, tag: str) -> None:
        try:
            self._http.delete(f"images/{tag}", {"force": "1"})
        except DockerNotFoundError:
            return
        except DockerError as e:
            logger.warning("Skipping removal of image %s: %s", tag, e)

    # ---- Unified server API ----

    def build_and_start(
        self,
        server_name: str,
        build_context: Path | str,
        template_name: str,
        env_vars: dict[str, str] | None = None,
        replicas: int = 1,
        registry_prefix: str | None = None,
        registry_auth: dict[str, str] | None = None,
    ) -> dict:
        env_vars = env_vars or {}
        tag = self.build_image(server_name, build_context, registry_prefix, registry_auth)
        if self.swarm_mode():
            return self._create_service(server_name, tag, template_name, env_vars, replicas, registry_auth)
        return self._create_container(server_name, tag, template_name, env_vars)

    def list_servers(self) -> list[dict]:
        return self._list_services() if self.swarm_mode() else self._list_containers()

    def get_server(self, server_name: str) -> dict | None:
        return self._get_service(server_name) if self.swarm_mode() else self._get_container(server_name)

    def start_server(self, server_name: str, replicas: int = 1) -> dict | None:
        if self.swarm_mode():
            return self._start_service(server_name, replicas)
        return self._start_container(server_name)

    def scale_server(self, server_name: str, replicas: int) -> dict | None:
        if not self.swarm_mode():
            return None
        return self._start_service(server_name, replicas)

    def stop_server(self, server_name: str) -> dict | None:
        if self.swarm_mode():
            return self._stop_service(server_name)
        return self._stop_container(server_name)

    def remove_server(self, server_name: str, registry_prefix: str | None = None) -> bool:
        if self.swarm_mode():
            result = self._remove_service(server_name)
        else:
            result = self._remove_container(server_name)
        self.remove_image(image_tag(server_name, registry_prefix))
        return result

    def update_runtime_env(self, server_name: str, env_vars: dict[str, str]) -> dict | None:
        env_list = [f"{k}={v}" for k, v in env_vars.items()]

        if self.swarm_mode():
            svc = self._find_service_raw(server_name)
            if not svc:
                return None
            spec = svc.get("Spec", {})
            task_template = spec.get("TaskTemplate")
            if not isinstance(task_template, dict) or "ContainerSpec" not in task_template:
                logger.warning("Swarm service %s has no ContainerSpec; cannot update env", server_name)
                return None
            task_template["ContainerSpec"]["Env"] = env_list
            new_spec = {**spec, "TaskTemplate": task_template}
            version = (svc.get("Version") or {}).get("Index", 0)
            self._http.post(f"services/{svc['ID']}/update", {"version": version}, new_spec)
            return self._get_service(server_name)

        name = _container_name(server_name)
        try:
            container = self._http.get(f"containers/{name}/json")
        except DockerNotFoundError:
            return None
        labels = (container.get("Config") or {}).get("Labels") or {}
        template_name = labels.get(LABEL_TEMPLATE, "custom")
        image = (container.get("Config") or {}).get("Image") or container.get("Image", "")
        self._stop_container(server_name)
        self._remove_container(server_name)
        return self._create_container(server_name, image, template_name, env_vars)

    def get_server_logs(self, server_name: str, tail: int = 200) -> str:
        tail = max(1, min(tail, 5000))
        query = {
            "stdout": "1",
            "stderr": "1",
            "tail": str(tail),
            "timestamps": "1",
        }
        if self.swarm_mode():
            svc = self._find_service_raw(server_name)
            if not svc:
                raise DockerNotFoundError(f"Server '{server_name}' not found")
            raw = self._http.get_raw(f"services/{svc['ID']}/logs", query)
        else:
            name = _container_name(server_name)
            try:
                raw = self._http.get_raw(f"containers/{name}/logs", query)
            except DockerNotFoundError as e:
                raise DockerNotFoundError(f"Server '{server_name}' not found") from e
        return demux_log_frames(raw)

    def stream_server_logs(self, server_name: str, tail: int = 100):
        """Open a long-lived log stream, yielding decoded log text chunks as
        Docker pushes them. The caller closes when done.

        Returns an iterator of (text_chunk: str). Frame demux happens here
        so the SSE endpoint just forwards lines."""
        tail = max(1, min(tail, 5000))
        query = {
            "stdout": "1",
            "stderr": "1",
            "tail": str(tail),
            "timestamps": "1",
            "follow": "1",
        }
        if self.swarm_mode():
            svc = self._find_service_raw(server_name)
            if not svc:
                raise DockerNotFoundError(f"Server '{server_name}' not found")
            chunks = self._http.stream_chunks(f"services/{svc['ID']}/logs", query)
        else:
            name = _container_name(server_name)
            try:
                chunks = self._http.stream_chunks(f"containers/{name}/logs", query)
            except DockerNotFoundError as e:
                raise DockerNotFoundError(f"Server '{server_name}' not found") from e
        return _LogStream(chunks)

    # ---- Container backend ----

    def _create_container(
        self, server_name: str, tag: str, template_name: str, env_vars: dict[str, str]
    ) -> dict:
        name = _container_name(server_name)
        labels = _all_labels(server_name, template_name)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]
        logger.info("Creating container %s", name)
        created = self._http.post(
            "containers/create",
            {"name": name},
            {
                "Image": tag,
                "Labels": labels,
                "Env": env_list,
                "HostConfig": {
                    "NetworkMode": self._network,
                    "RestartPolicy": {"Name": "unless-stopped"},
                },
                "NetworkingConfig": {"EndpointsConfig": {self._network: {}}},
            },
        )
        cid = created.get("Id")
        if not cid:
            raise DockerError("Container create returned no ID")
        self._http.post(f"containers/{cid}/start")
        return _container_to_dict(self._http.get(f"containers/{cid}/json"))

    def _list_containers(self) -> list[dict]:
        filters = {"label": [f"{LABEL_MANAGED}=true"]}
        resp = self._http.get(
            "containers/json", {"all": "1", "filters": json.dumps(filters)}
        )
        return [_container_summary_to_dict(c) for c in resp]

    def _get_container(self, server_name: str) -> dict | None:
        name = _container_name(server_name)
        try:
            c = self._http.get(f"containers/{name}/json")
        except DockerNotFoundError:
            return None
        labels = (c.get("Config") or {}).get("Labels") or {}
        if labels.get(LABEL_MANAGED) != "true":
            return None
        return _container_to_dict(c)

    def _start_container(self, server_name: str) -> dict | None:
        name = _container_name(server_name)
        try:
            self._http.post(f"containers/{name}/start")
        except DockerNotFoundError:
            return None
        except DockerError as e:
            if "already started" not in str(e):
                raise
        return self._get_container(server_name)

    def _stop_container(self, server_name: str) -> dict | None:
        name = _container_name(server_name)
        try:
            self._http.post(f"containers/{name}/stop")
        except DockerNotFoundError:
            return None
        except DockerError:
            # 304 already stopped, ignore
            pass
        return self._get_container(server_name)

    def _remove_container(self, server_name: str) -> bool:
        name = _container_name(server_name)
        try:
            self._http.post(f"containers/{name}/stop")
        except DockerError:
            pass
        try:
            self._http.delete(f"containers/{name}", {"force": "1"})
            return True
        except DockerNotFoundError:
            return False

    # ---- Swarm backend ----

    def _create_service(
        self,
        server_name: str,
        tag: str,
        template_name: str,
        env_vars: dict[str, str],
        replicas: int,
        registry_auth: dict[str, str] | None = None,
    ) -> dict:
        name = _container_name(server_name)
        labels = _all_labels(server_name, template_name)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]
        logger.info("Creating swarm service %s (replicas=%d)", name, replicas)
        spec = {
            "Name": name,
            "Labels": labels,
            "TaskTemplate": {
                "ContainerSpec": {"Image": tag, "Env": env_list},
                "Networks": [{"Target": self._network}],
            },
            "Mode": {"Replicated": {"Replicas": replicas}},
            "EndpointSpec": {"Mode": "vip"},
        }
        headers = {"X-Registry-Auth": _encode_auth(registry_auth)} if registry_auth else None
        self._http.post("services/create", None, spec, headers)
        got = self._get_service(server_name)
        if not got:
            raise DockerError(f"Swarm service {name} missing after create")
        return got

    def _list_services(self) -> list[dict]:
        filters = {"label": [f"{LABEL_MANAGED}=true"]}
        services = self._http.get("services", {"filters": json.dumps(filters)})
        return [self._service_to_dict(s, include_placement=False) for s in services]

    def _get_service(self, server_name: str) -> dict | None:
        svc = self._find_service_raw(server_name)
        if not svc:
            return None
        labels = (svc.get("Spec") or {}).get("Labels") or {}
        if labels.get(LABEL_MANAGED) != "true":
            return None
        return self._service_to_dict(svc, include_placement=True)

    def _start_service(self, server_name: str, replicas: int) -> dict | None:
        svc = self._find_service_raw(server_name)
        if not svc:
            return None
        self._scale_service_raw(svc, replicas)
        return self._get_service(server_name)

    def _stop_service(self, server_name: str) -> dict | None:
        svc = self._find_service_raw(server_name)
        if not svc:
            return None
        self._scale_service_raw(svc, 0)
        return self._get_service(server_name)

    def _remove_service(self, server_name: str) -> bool:
        svc = self._find_service_raw(server_name)
        if not svc:
            return False
        self._http.delete(f"services/{svc['ID']}")
        return True

    def _find_service_raw(self, server_name: str) -> dict | None:
        name = _container_name(server_name)
        try:
            services = self._http.get("services", {"filters": json.dumps({"name": [name]})})
        except DockerError:
            return None
        for svc in services:
            if (svc.get("Spec") or {}).get("Name") == name:
                return svc
        return None

    def _scale_service_raw(self, svc: dict, replicas: int) -> None:
        spec = svc.get("Spec") or {}
        spec["Mode"] = {"Replicated": {"Replicas": replicas}}
        version = (svc.get("Version") or {}).get("Index", 0)
        self._http.post(f"services/{svc['ID']}/update", {"version": version}, spec)

    def _task_placement_for(self, svc: dict) -> list[dict]:
        try:
            tasks = self._http.get(
                "tasks", {"filters": json.dumps({"service": [svc["ID"]]})}
            )
        except DockerError:
            return []
        node_map: dict[str, str] = {}
        try:
            nodes = self._http.get("nodes")
            for n in nodes:
                node_map[n["ID"]] = (n.get("Description") or {}).get("Hostname") or n["ID"]
        except DockerError:
            pass
        out: list[dict] = []
        for t in tasks:
            status = t.get("Status") or {}
            err = status.get("Err") or None
            node_id = t.get("NodeID", "")
            out.append({
                "task_id": t.get("ID", ""),
                "node_id": node_id,
                "node_name": node_map.get(node_id) if node_id else None,
                "state": status.get("State", "unknown"),
                "slot": t.get("Slot"),
                "error": err,
            })
        return out

    def _service_to_dict(self, svc: dict, include_placement: bool) -> dict:
        spec = svc.get("Spec") or {}
        labels = spec.get("Labels") or {}
        replicas = ((spec.get("Mode") or {}).get("Replicated") or {}).get("Replicas", 0)
        status = "running" if replicas > 0 else "stopped"
        placement = self._task_placement_for(svc) if (include_placement and self.swarm_mode()) else []
        # Swarm doesn't expose container-level health on the service object;
        # leaving health=None preserves the existing UI contract for swarm.
        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": status,
            "health": None,
            "restart_count": None,
            "created_at": svc.get("CreatedAt", ""),
            "replicas_running": int(replicas),
            "placement": placement,
        }

    # ---- Build context tar ----

    @staticmethod
    def _tar_bytes(directory: Path) -> bytes:
        if not directory.is_dir():
            raise DockerError(f"Build context not a directory: {directory}")
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
            tar_path = Path(tmp.name)
        try:
            result = subprocess.run(
                ["tar", "-cf", str(tar_path), "-C", str(directory), "."],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise DockerError(
                    f"tar failed (exit {result.returncode}) for context {directory}: "
                    f"{result.stderr.decode('utf-8', errors='replace')}"
                )
            return tar_path.read_bytes()
        finally:
            tar_path.unlink(missing_ok=True)


class _LogStream:
    """Adapter that takes a byte-chunk iterator (from DockerHttp.stream_chunks)
    carrying Docker multiplexed log frames and yields decoded text chunks
    suitable for SSE. Frames are demuxed lazily so we don't buffer the whole
    stream."""

    def __init__(self, source):
        self._source = source
        self._buffer = b""

    def __iter__(self):
        for chunk in self._source:
            if not chunk:
                continue
            self._buffer += chunk
            text, self._buffer = _drain_frames(self._buffer)
            if text:
                yield text

    def close(self) -> None:
        try:
            self._source.close()
        except Exception:  # noqa: BLE001
            pass


def _drain_frames(buf: bytes) -> tuple[str, bytes]:
    """Parse as many complete 8-byte-header Docker log frames out of `buf` as
    possible. Return (decoded_text, leftover_bytes_for_next_chunk)."""
    out: list[bytes] = []
    pos = 0
    n = len(buf)
    while pos + 8 <= n:
        stream = buf[pos]
        if stream > 2:
            # Doesn't look multiplexed - dump the rest as text.
            return (buf[pos:].decode("utf-8", errors="replace"), b"")
        size = int.from_bytes(buf[pos + 4 : pos + 8], "big")
        if pos + 8 + size > n:
            break  # frame not complete yet, wait for more bytes
        out.append(buf[pos + 8 : pos + 8 + size])
        pos += 8 + size
    return (b"".join(out).decode("utf-8", errors="replace"), buf[pos:])


# Module-level singleton so the swarm-mode cache is shared across requests.
_singleton: DockerClient | None = None


def get_docker() -> DockerClient:
    global _singleton
    if _singleton is None:
        _singleton = DockerClient()
    return _singleton
