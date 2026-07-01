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
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.docker_http import DockerError, DockerHttp, DockerNotFoundError, demux_log_frames

logger = logging.getLogger(__name__)


LABEL_MANAGED = "roundhouse.managed"
LABEL_SERVER_NAME = "roundhouse.server-name"
LABEL_TEMPLATE = "roundhouse.template"
# Records the container port Traefik routes to (8000 for structured servers,
# 8001 for code-first servers fronted by the platform proxy). Persisted so an
# env update, which recreates the container, can restore the same routing.
LABEL_ROUTE_PORT = "roundhouse.route-port"
CONTAINER_PREFIX = "mcp-"
DEFAULT_ROUTE_PORT = 8000


def _container_name(server_name: str) -> str:
    return CONTAINER_PREFIX + server_name


def image_tag(server_name: str, registry_prefix: str | None = None) -> str:
    name = f"mcp-server-{server_name}"
    if registry_prefix:
        p = registry_prefix.strip().rstrip("/")
        return f"{p}/{name}:latest"
    return f"{name}:latest"


class RegistryRequiredError(DockerError):
    """Raised when a deploy would produce an image that can't be distributed
    across a multi-node Swarm (no registry configured), so we refuse up front
    instead of creating a service whose tasks get stuck 'No such image'."""


def _split_tag(full_tag: str) -> tuple[str, str]:
    i = full_tag.rfind(":")
    if i < 0:
        return full_tag, "latest"
    return full_tag[:i], full_tag[i + 1 :]


def _encode_auth(auth: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"username": auth["username"], "password": auth["password"]}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")


def _traefik_labels(server_name: str, route_port: int = DEFAULT_ROUTE_PORT) -> dict[str, str]:
    router = f"mcp-{server_name}"
    entrypoints = get_settings().mcp_traefik_entrypoints
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"PathPrefix(`/s/{server_name}`)",
        f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes": f"/s/{server_name}",
        f"traefik.http.routers.{router}.middlewares": f"{router}-strip",
        f"traefik.http.services.{router}.loadbalancer.server.port": str(route_port),
        f"traefik.http.routers.{router}.entrypoints": entrypoints,
    }


def _all_labels(
    server_name: str, template_name: str, route_port: int = DEFAULT_ROUTE_PORT
) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_SERVER_NAME: server_name,
        LABEL_TEMPLATE: template_name,
        LABEL_ROUTE_PORT: str(route_port),
        **_traefik_labels(server_name, route_port),
    }


def _placement_constraints_to_docker(constraints: list[dict] | None) -> list[str]:
    """Translate [{"key","value"}, ...] node-label selectors into Docker Swarm
    service constraint strings `node.labels.<key>==<value>`. Blank keys/values
    are skipped so a malformed entry can't produce an unschedulable service."""
    out: list[str] = []
    for c in constraints or []:
        if not isinstance(c, dict):
            continue
        key = str(c.get("key") or "").strip()
        value = str(c.get("value") or "").strip()
        if key and value:
            out.append(f"node.labels.{key}=={value}")
    return out


def _parse_health_from_summary(status_str: str) -> str | None:
    """Docker's `ps` Status column carries health in parens, e.g.
    'Up 2 minutes (healthy)'. Parse it out without a full inspect."""
    if not isinstance(status_str, str):
        return None
    for token in ("(healthy)", "(unhealthy)", "(starting)", "(health: starting)"):
        if token in status_str:
            return token.strip("()").replace("health: ", "")
    return None


def _parse_ts(value: str | None) -> float | None:
    """Parse a Docker RFC3339 timestamp to epoch seconds. Docker emits these
    with up to nanosecond precision and a trailing 'Z' (e.g. container
    State.StartedAt, swarm task Status.Timestamp); Python can't parse 9-digit
    fractions, so trim to microseconds. The zero value '0001-01-01...' -> None."""
    if not value or value.startswith("0001-01-01"):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, _, tail = s.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        s = f"{head}.{frac[:6]}{rest}"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
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
        # When the process actually started serving — used for the readiness
        # grace window so a fresh container shows "starting", not "unhealthy".
        "running_since": _parse_ts(state_obj.get("StartedAt")),
        "has_running_task": running,
        "placement": [],
    }


class DockerClient:
    def __init__(self, http: DockerHttp | None = None):
        cfg = get_settings()
        self._http = http or DockerHttp(cfg.docker_host)
        self._network = cfg.mcp_docker_network
        self._swarm_cache: bool | None = None
        self._host_name_cache: str | None = None

        forced = (cfg.mcp_docker_mode or "auto").strip().lower()
        if forced == "swarm":
            self._swarm_cache = True
            logger.info("Docker mode: swarm (forced via MCP_DOCKER_MODE)")
        elif forced == "standalone":
            self._swarm_cache = False
            logger.info("Docker mode: standalone (forced via MCP_DOCKER_MODE)")
        elif forced != "auto":
            raise ValueError(
                f"MCP_DOCKER_MODE must be 'auto', 'standalone', or 'swarm' (got {forced!r})"
            )

    # ---- Mode detection ----

    def swarm_mode(self) -> bool:
        if self._swarm_cache is not None:
            return self._swarm_cache
        info = self._http.get("info")
        state = (info.get("Swarm") or {}).get("LocalNodeState")
        self._swarm_cache = state == "active"
        logger.info("Docker mode: %s (auto-detected)", "swarm" if self._swarm_cache else "standalone")
        return self._swarm_cache

    def mode(self) -> str:
        return "docker-swarm" if self.swarm_mode() else "docker"

    def node_count(self) -> int:
        """Number of nodes in the swarm. 1 when not in swarm or on error - the
        conservative answer (a single node can run locally-built images)."""
        if not self.swarm_mode():
            return 1
        try:
            return len(self._http.get("nodes") or []) or 1
        except DockerError:
            return 1

    def list_node_labels(self) -> list[dict]:
        """Distinct node-label key=value pairs across the swarm, for populating
        the placement selector. Only swarm-assigned node labels (Spec.Labels,
        i.e. `docker node update --label-add`) are returned — those are exactly
        what a `node.labels.*` constraint matches. Returns [] off swarm. Each
        entry is {"key","value","nodes"} where nodes counts matching hosts."""
        if not self.swarm_mode():
            return []
        try:
            nodes = self._http.get("nodes") or []
        except DockerError:
            return []
        counts: dict[tuple[str, str], int] = {}
        for n in nodes:
            node_labels = (n.get("Spec") or {}).get("Labels") or {}
            for key, value in node_labels.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                counts[(key, value)] = counts.get((key, value), 0) + 1
        return [
            {"key": k, "value": v, "nodes": c}
            for (k, v), c in sorted(counts.items())
        ]

    def _host_name(self) -> str:
        """The standalone Docker host's name (from /info). Cached per client so
        we can show 'where a server lives' even on a single host, mirroring the
        node placement Swarm/K8s expose."""
        if self._host_name_cache is None:
            try:
                self._host_name_cache = (self._http.get("info").get("Name") or "")
            except DockerError:
                self._host_name_cache = ""
        return self._host_name_cache

    def supports_scaling(self) -> bool:
        return self.swarm_mode()

    def image_tag(self, server_name: str, registry_prefix: str | None = None) -> str:
        return image_tag(server_name, registry_prefix)

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
        cpu_limit: float | None = None,
        memory_limit_mb: int | None = None,
        route_port: int = DEFAULT_ROUTE_PORT,
        placement_constraints: list[dict] | None = None,
    ) -> dict:
        env_vars = env_vars or {}
        # Guardrail: a multi-node Swarm distributes images via a registry. Without
        # one, build_image only tags locally on the build node and skips the push,
        # so other nodes can't pull it and the service's tasks get stuck rejected
        # with "No such image". Fail fast with an actionable message instead.
        if not registry_prefix and self.swarm_mode() and self.node_count() > 1:
            raise RegistryRequiredError(
                f"Refusing to deploy {server_name!r}: this is a multi-node Docker "
                f"Swarm ({self.node_count()} nodes) but no container registry is "
                "configured, so the locally-built image cannot be pulled by other "
                "nodes (their tasks fail with 'No such image'). Configure a registry "
                "under Platform Settings -> Docker registry, then redeploy."
            )
        tag = self.build_image(server_name, build_context, registry_prefix, registry_auth)
        if self.swarm_mode():
            return self._create_service(
                server_name, tag, template_name, env_vars, replicas, registry_auth,
                cpu_limit=cpu_limit, memory_limit_mb=memory_limit_mb, route_port=route_port,
                placement_constraints=placement_constraints,
            )
        return self._create_container(
            server_name, tag, template_name, env_vars,
            cpu_limit=cpu_limit, memory_limit_mb=memory_limit_mb, route_port=route_port,
        )

    def list_servers(self) -> list[dict]:
        return self._list_services() if self.swarm_mode() else self._list_containers()

    def get_server(self, server_name: str) -> dict | None:
        if self.swarm_mode():
            return self._get_service(server_name)
        snap = self._get_container(server_name)
        # Standalone has a single host, but operators still want to see WHERE a
        # server runs. Synthesize one placement entry naming the Docker host so
        # the UI renders standalone/swarm/k8s the same way.
        if snap and snap.get("replicas_running"):
            snap["placement"] = [{
                "task_id": "",
                "node_id": "",
                "node_name": self._host_name() or "this host",
                "state": snap.get("status") or "running",
                "slot": None,
                "error": None,
            }]
        return snap

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
        # Preserve the routed port across the recreate so a code-first server
        # keeps pointing Traefik at its proxy (8001), not the backend (8000).
        try:
            route_port = int(labels.get(LABEL_ROUTE_PORT) or DEFAULT_ROUTE_PORT)
        except (TypeError, ValueError):
            route_port = DEFAULT_ROUTE_PORT
        self._stop_container(server_name)
        self._remove_container(server_name)
        return self._create_container(
            server_name, image, template_name, env_vars, route_port=route_port
        )

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
        self,
        server_name: str,
        tag: str,
        template_name: str,
        env_vars: dict[str, str],
        cpu_limit: float | None = None,
        memory_limit_mb: int | None = None,
        route_port: int = DEFAULT_ROUTE_PORT,
    ) -> dict:
        name = _container_name(server_name)
        labels = _all_labels(server_name, template_name, route_port)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]
        logger.info("Creating container %s", name)
        host_config: dict[str, Any] = {
            "NetworkMode": self._network,
            "RestartPolicy": {"Name": "unless-stopped"},
        }
        if isinstance(cpu_limit, (int, float)) and cpu_limit > 0:
            host_config["NanoCpus"] = int(cpu_limit * 1_000_000_000)
        if isinstance(memory_limit_mb, int) and memory_limit_mb > 0:
            host_config["Memory"] = memory_limit_mb * 1024 * 1024
        created = self._http.post(
            "containers/create",
            {"name": name},
            {
                "Image": tag,
                "Labels": labels,
                "Env": env_list,
                "HostConfig": host_config,
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
        cpu_limit: float | None = None,
        memory_limit_mb: int | None = None,
        route_port: int = DEFAULT_ROUTE_PORT,
        placement_constraints: list[dict] | None = None,
    ) -> dict:
        name = _container_name(server_name)
        labels = _all_labels(server_name, template_name, route_port)
        env_list = [f"{k}={v}" for k, v in env_vars.items()]
        logger.info("Creating swarm service %s (replicas=%d)", name, replicas)
        task_template: dict[str, Any] = {
            "ContainerSpec": {"Image": tag, "Env": env_list},
            "Networks": [{"Target": self._network}],
        }
        constraints = _placement_constraints_to_docker(placement_constraints)
        if constraints:
            # Node-label selectors, ANDed by Swarm. A task only schedules onto a
            # node whose labels satisfy every constraint.
            task_template["Placement"] = {"Constraints": constraints}
        limits: dict[str, Any] = {}
        if isinstance(cpu_limit, (int, float)) and cpu_limit > 0:
            limits["NanoCPUs"] = int(cpu_limit * 1_000_000_000)
        if isinstance(memory_limit_mb, int) and memory_limit_mb > 0:
            limits["MemoryBytes"] = memory_limit_mb * 1024 * 1024
        if limits:
            task_template["Resources"] = {"Limits": limits}
        spec = {
            "Name": name,
            "Labels": labels,
            "TaskTemplate": task_template,
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

    # ---- Swarm secrets + arbitrary-service update (self-managed TLS) ----

    def create_secret(
        self, name: str, data: bytes, labels: dict[str, str] | None = None
    ) -> str:
        """Create a Swarm secret and return its ID. Swarm secrets are immutable,
        so callers use content-addressed names and swap references rather than
        updating in place."""
        spec = {
            "Name": name,
            "Data": base64.b64encode(data).decode("ascii"),
            "Labels": labels or {},
        }
        resp = self._http.post("secrets/create", None, spec)
        sid = resp.get("ID")
        if not sid:
            raise DockerError(f"Secret create for {name!r} returned no ID")
        return sid

    def find_secret(self, name: str) -> dict | None:
        """Return the raw secret whose Spec.Name matches exactly, or None. The
        `name` filter is a substring match, so we confirm the exact name."""
        try:
            secrets = self._http.get(
                "secrets", {"filters": json.dumps({"name": [name]})}
            )
        except DockerError:
            return None
        for s in secrets or []:
            if (s.get("Spec") or {}).get("Name") == name:
                return s
        return None

    def list_secrets(self, label: str | None = None) -> list[dict]:
        query = {"filters": json.dumps({"label": [label]})} if label else None
        return self._http.get("secrets", query) or []

    def remove_secret(self, secret_id: str) -> None:
        """Remove a secret. A secret still referenced by a service can't be
        removed (Docker 400); callers treat that as 'leave it, prune later'."""
        try:
            self._http.delete(f"secrets/{secret_id}")
        except DockerNotFoundError:
            return

    def get_service_by_name(self, name: str) -> dict | None:
        try:
            services = self._http.get(
                "services", {"filters": json.dumps({"name": [name]})}
            )
        except DockerError:
            return None
        for svc in services or []:
            if (svc.get("Spec") or {}).get("Name") == name:
                return svc
        return None

    def set_service_secrets(self, service_name: str, secret_refs: list[dict]) -> None:
        """Read-modify-write a service's spec so its ContainerSpec.Secrets is
        exactly `secret_refs`, preserving everything else. Triggers a rolling
        task update, which is how Traefik picks up a swapped cert."""
        svc = self.get_service_by_name(service_name)
        if not svc:
            raise DockerNotFoundError(f"Service '{service_name}' not found")
        spec = svc.get("Spec") or {}
        task_template = spec.get("TaskTemplate")
        if not isinstance(task_template, dict) or "ContainerSpec" not in task_template:
            raise DockerError(f"Service '{service_name}' has no ContainerSpec")
        task_template["ContainerSpec"]["Secrets"] = secret_refs
        version = (svc.get("Version") or {}).get("Index", 0)
        self._http.post(f"services/{svc['ID']}/update", {"version": version}, spec)

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
                # When the task entered its current state — for a running task
                # this is ~when it started serving (drives the readiness grace).
                "updated_at": status.get("Timestamp"),
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
        # Swarm doesn't expose container-level health on the service object, so
        # health is resolved by the API layer probing /healthz. We surface the
        # task lifecycle here so that probe can tell "still coming up" (no running
        # task yet, or just started) from "actually broken". has_running_task is
        # None when placement wasn't fetched (caller must treat as unknown).
        running_tasks = [p for p in placement if p.get("state") == "running"]
        has_running_task = bool(running_tasks) if placement else None
        running_since: float | None = None
        if running_tasks:
            stamps = [t for t in (_parse_ts(p.get("updated_at")) for p in running_tasks) if t]
            running_since = max(stamps) if stamps else None
        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": status,
            "health": None,
            "restart_count": None,
            "created_at": svc.get("CreatedAt", ""),
            "replicas_running": int(replicas),
            "running_since": running_since,
            "has_running_task": has_running_task,
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


# Back-compat alias for code that pre-dates the Orchestrator abstraction.
# Returns the configured backend, which may not be Docker — call sites that
# truly need Docker-specifics (e.g. swarm_mode()) should narrow with isinstance.
def get_docker():
    from app.services.orchestrator import get_orchestrator

    return get_orchestrator()
