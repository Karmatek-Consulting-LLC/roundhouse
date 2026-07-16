"""Kubernetes implementation of the Orchestrator protocol.

Per MCP server we manage four resources in the workloads namespace:

    Deployment    (apps/v1)              — replicas, image, env, resources
    Service       (v1)                   — ClusterIP, port 8000
    Middleware    (traefik.io/v1alpha1)  — StripPrefix /s/{name}
    IngressRoute  (traefik.io/v1alpha1)  — PathPrefix(`/s/{name}`) -> Service

Image builds are handled either by delegating to DockerClient (legacy
MCP_K8S_BUILDER=docker) or by spawning a one-shot kaniko Job mounted with
the server's build context (default MCP_K8S_BUILDER=kaniko).
"""
from __future__ import annotations

import json
import logging
import tarfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.docker import CONTAINER_PREFIX, DockerClient, image_tag as docker_image_tag
from app.services.docker_http import DockerError, DockerNotFoundError
from app.services.kubernetes_http import KubernetesHttp

logger = logging.getLogger(__name__)

LABEL_MANAGED = "roundhouse.managed"
LABEL_SERVER_NAME = "roundhouse.server-name"
LABEL_TEMPLATE = "roundhouse.template"

TRAEFIK_API = "traefik.io/v1alpha1"


def _resource_name(server_name: str) -> str:
    return CONTAINER_PREFIX + server_name


def _all_labels(server_name: str, template_name: str) -> dict[str, str]:
    return {
        LABEL_MANAGED: "true",
        LABEL_SERVER_NAME: server_name,
        LABEL_TEMPLATE: _sanitize_label(template_name),
    }


def _sanitize_label(value: str) -> str:
    """K8s label values: max 63 chars, [A-Za-z0-9._-] only."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", value or "")
    return cleaned[:63] or "unknown"


def _container_resources(cpu_limit: float | None, memory_limit_mb: int | None) -> dict[str, Any] | None:
    """Translate spec limits into K8s container.resources. Lab also enforces
    these as requests (matching what the Docker daemon does with HostConfig)."""
    if (cpu_limit is None or cpu_limit <= 0) and (memory_limit_mb is None or memory_limit_mb <= 0):
        return None
    limits: dict[str, str] = {}
    if cpu_limit is not None and cpu_limit > 0:
        # millicpu — k8s accepts decimals but milli is the canonical form.
        limits["cpu"] = f"{int(cpu_limit * 1000)}m"
    if memory_limit_mb is not None and memory_limit_mb > 0:
        limits["memory"] = f"{memory_limit_mb}Mi"
    return {"limits": limits, "requests": dict(limits)}


class KubernetesClient:
    def __init__(self, http: KubernetesHttp | None = None, image_builder: DockerClient | None = None):
        cfg = get_settings()
        self._http = http or KubernetesHttp(
            base_url=cfg.mcp_k8s_api_url,
            token_path=cfg.mcp_k8s_token_path or None,
            ca_path=cfg.mcp_k8s_ca_path or None,
        )
        self._namespace = cfg.mcp_k8s_namespace
        self._image_pull_secret = cfg.mcp_k8s_image_pull_secret or None
        # Used for the docker-builder fallback path. Lazily instantiated since
        # kaniko mode doesn't need it.
        self._docker_builder = image_builder

    # ---- Identity ----

    def mode(self) -> str:
        return "kubernetes"

    def supports_scaling(self) -> bool:
        return True

    def list_node_labels(self) -> list[dict]:
        # Node-label placement is a Swarm feature; K8s node affinity is not
        # wired up here, so expose no selectors (the UI hides the picker).
        return []

    # ---- Workload lifecycle ----

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
        route_port: int = 8000,
        # Accepted for Orchestrator-protocol parity; K8s placement is not wired.
        placement_constraints: list[dict] | None = None,
        # Accepted for parity; base-registry auth for the Kaniko/docker builder
        # is configured on the builder itself, not passed per-build here.
        base_registry_auth: dict[str, dict[str, str]] | None = None,
    ) -> dict:
        if not registry_prefix:
            raise DockerError(
                "Kubernetes mode requires a Docker registry; configure one in "
                "platform settings before deploying."
            )

        tag = self._build_image(server_name, build_context, registry_prefix, registry_auth)

        self._apply_deployment(
            server_name=server_name,
            tag=tag,
            template_name=template_name,
            env_vars=env_vars or {},
            replicas=replicas,
            cpu_limit=cpu_limit,
            memory_limit_mb=memory_limit_mb,
            route_port=route_port,
        )
        self._apply_service(server_name, template_name, route_port)
        self._apply_middleware(server_name)
        self._apply_ingressroute(server_name)

        got = self.get_server(server_name)
        if got is None:
            raise DockerError(f"Kubernetes Deployment for {server_name} missing after create")
        return got

    def list_servers(self) -> list[dict]:
        resp = self._http.get(
            self._path("apis/apps/v1", "deployments"),
            {"labelSelector": f"{LABEL_MANAGED}=true"},
        )
        items = resp.get("items") or []
        return [self._deployment_to_dict(d) for d in items]

    def get_server(self, server_name: str) -> dict | None:
        d = self._find_deployment(server_name)
        if d is None:
            return None
        return self._deployment_to_dict(d)

    def start_server(self, server_name: str, replicas: int = 1) -> dict | None:
        if self._find_deployment(server_name) is None:
            return None
        self._scale_deployment(server_name, max(1, replicas))
        return self.get_server(server_name)

    def scale_server(self, server_name: str, replicas: int) -> dict | None:
        if self._find_deployment(server_name) is None:
            return None
        self._scale_deployment(server_name, replicas)
        return self.get_server(server_name)

    def stop_server(self, server_name: str) -> dict | None:
        if self._find_deployment(server_name) is None:
            return None
        self._scale_deployment(server_name, 0)
        return self.get_server(server_name)

    def remove_server(self, server_name: str, registry_prefix: str | None = None) -> bool:
        name = _resource_name(server_name)
        existed = self._find_deployment(server_name) is not None
        self._http.delete(self._path("apis/apps/v1", "deployments") + "/" + name)
        self._http.delete(self._path("api/v1", "services") + "/" + name)
        self._http.delete(self._path(f"apis/{TRAEFIK_API}", "ingressroutes") + "/" + name)
        self._http.delete(
            self._path(f"apis/{TRAEFIK_API}", "middlewares") + "/" + name + "-strip"
        )
        if self._docker_builder is not None:
            self._docker_builder.remove_image(self.image_tag(server_name, registry_prefix))
        return existed

    def update_runtime_env(self, server_name: str, env_vars: dict[str, str]) -> dict | None:
        if self._find_deployment(server_name) is None:
            return None
        name = _resource_name(server_name)
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "mcp",
                                "env": _env_list(env_vars),
                            }
                        ]
                    }
                }
            }
        }
        self._http.patch(self._path("apis/apps/v1", "deployments") + "/" + name, patch)
        return self.get_server(server_name)

    def get_server_logs(self, server_name: str, tail: int = 200) -> str:
        tail = max(1, min(tail, 5000))
        pod_name = self._pick_pod(server_name)
        if pod_name is None:
            raise DockerNotFoundError(f"Server '{server_name}' has no running pods")
        return self._http.get_raw(
            self._path("api/v1", "pods") + "/" + pod_name + "/log",
            {"tailLines": str(tail), "timestamps": "true"},
        )

    def stream_server_logs(self, server_name: str, tail: int = 100):
        tail = max(1, min(tail, 5000))
        pod_name = self._pick_pod(server_name)
        if pod_name is None:
            raise DockerNotFoundError(f"Server '{server_name}' has no running pods")
        chunks = self._http.stream_chunks(
            self._path("api/v1", "pods") + "/" + pod_name + "/log",
            {"tailLines": str(tail), "timestamps": "true", "follow": "true"},
        )
        return _PodLogStream(chunks)

    def image_tag(self, server_name: str, registry_prefix: str | None = None) -> str:
        # Reuse DockerClient's tag scheme so a registry switch between backends
        # doesn't change the image name layout.
        return docker_image_tag(server_name, registry_prefix)

    # ---- Image build dispatch ----

    def _build_image(
        self,
        server_name: str,
        build_context: Path | str,
        registry_prefix: str,
        registry_auth: dict[str, str] | None,
    ) -> str:
        builder = (get_settings().mcp_k8s_builder or "docker").strip().lower()
        if builder == "kaniko":
            return self._build_via_kaniko(server_name, build_context, registry_prefix)
        # Legacy: talk to a Docker daemon at MCP_DOCKER_HOST.
        if self._docker_builder is None:
            self._docker_builder = DockerClient()
        return self._docker_builder.build_image(
            server_name, build_context, registry_prefix, registry_auth
        )

    def _build_via_kaniko(
        self, server_name: str, build_context: Path | str, registry_prefix: str
    ) -> str:
        cfg = get_settings()
        pvc = cfg.mcp_k8s_builder_pvc
        registry_secret = cfg.mcp_k8s_builder_registry_secret
        if not pvc:
            raise DockerError("Kaniko builder requires MCP_K8S_BUILDER_PVC to be set.")
        if not registry_secret:
            raise DockerError(
                "Kaniko builder requires MCP_K8S_BUILDER_REGISTRY_SECRET to be set."
            )

        tag = self.image_tag(server_name, registry_prefix)
        builder_ns = cfg.mcp_k8s_builder_namespace or cfg.pod_namespace or "default"

        build_context = Path(build_context)
        tar_path = build_context / ".kaniko-context.tar"
        _write_context_tar(build_context, tar_path)

        job_name = _kaniko_job_name(server_name)
        # Best-effort cleanup of any previous Job with this name. We append a
        # timestamp so collisions are unlikely, but a leftover from a crashed
        # build can otherwise wedge the next attempt.
        try:
            self._http.delete(self._job_path(builder_ns) + "/" + job_name)
        except Exception:  # noqa: BLE001
            pass

        manifest = self._kaniko_job_manifest(
            job_name=job_name,
            server_name=server_name,
            tag=tag,
            pvc=pvc,
            registry_secret=registry_secret,
        )
        logger.info("Launching kaniko Job %s -> %s", job_name, tag)
        self._http.post(self._job_path(builder_ns), manifest)
        try:
            # The tar must stay on the PVC until the kaniko pod has actually
            # scheduled, pulled, and read it via --context=tar://. Creating the
            # Job object only enqueues that work, so cleanup waits for the Job to
            # finish (success or failure) rather than racing the consumer.
            self._wait_for_kaniko_job(builder_ns, job_name)
        finally:
            try:
                tar_path.unlink()
            except FileNotFoundError:
                pass
        return tag

    def _wait_for_kaniko_job(self, namespace: str, job_name: str) -> None:
        cfg = get_settings()
        timeout = max(60, cfg.mcp_k8s_builder_timeout)
        deadline = time.monotonic() + timeout
        poll = 3
        while time.monotonic() < deadline:
            job = self._http.get(self._job_path(namespace) + "/" + job_name)
            status = job.get("status") or {}
            if (status.get("succeeded") or 0) > 0:
                logger.info("kaniko Job %s succeeded", job_name)
                return
            if (status.get("failed") or 0) > 0:
                logs = self._fetch_job_pod_logs(namespace, job_name)
                raise DockerError(f"kaniko build failed for Job {job_name}:\n{logs}")
            time.sleep(poll)
        logs = self._fetch_job_pod_logs(namespace, job_name)
        raise DockerError(
            f"kaniko Job {job_name} timed out after {timeout}s. Last logs:\n{logs}"
        )

    def _fetch_job_pod_logs(self, namespace: str, job_name: str) -> str:
        try:
            pods = self._http.get(
                f"api/v1/namespaces/{namespace}/pods",
                {"labelSelector": f"job-name={job_name}"},
            )
        except Exception as e:  # noqa: BLE001
            return f"(failed to list pods: {e})"
        items = pods.get("items") or []
        if not items:
            return "(no pods found for Job)"
        pod_name = (items[0].get("metadata") or {}).get("name")
        if not pod_name:
            return "(pod had no name)"
        try:
            return self._http.get_raw(
                f"api/v1/namespaces/{namespace}/pods/{pod_name}/log",
                {"tailLines": "200"},
            )
        except Exception as e:  # noqa: BLE001
            return f"(failed to fetch logs: {e})"

    def _kaniko_job_manifest(
        self,
        job_name: str,
        server_name: str,
        tag: str,
        pvc: str,
        registry_secret: str,
    ) -> dict[str, Any]:
        cfg = get_settings()
        manifest: dict[str, Any] = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "labels": {
                    LABEL_MANAGED: "true",
                    LABEL_SERVER_NAME: server_name,
                    "app.kubernetes.io/component": "kaniko-builder",
                },
            },
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {
                        "labels": {"job-name": job_name, LABEL_MANAGED: "true"},
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [
                            {
                                "name": "kaniko",
                                "image": cfg.mcp_k8s_builder_image,
                                "args": [
                                    "--context=tar:///workspace/.kaniko-context.tar",
                                    f"--destination={tag}",
                                    "--snapshot-mode=redo",
                                    "--cleanup",
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "context",
                                        "mountPath": "/workspace",
                                        "subPath": _sanitize_label(server_name),
                                        "readOnly": True,
                                    },
                                    {
                                        "name": "docker-config",
                                        "mountPath": "/kaniko/.docker",
                                        "readOnly": True,
                                    },
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "context",
                                "persistentVolumeClaim": {
                                    "claimName": pvc,
                                    "readOnly": True,
                                },
                            },
                            {
                                "name": "docker-config",
                                "secret": {
                                    "secretName": registry_secret,
                                    "items": [
                                        {"key": ".dockerconfigjson", "path": "config.json"}
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        }
        if cfg.node_name:
            manifest["spec"]["template"]["spec"]["affinity"] = {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": [cfg.node_name],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        return manifest

    # ---- Apply (idempotent create-or-update) ----

    def _apply_deployment(
        self,
        server_name: str,
        tag: str,
        template_name: str,
        env_vars: dict[str, str],
        replicas: int,
        cpu_limit: float | None,
        memory_limit_mb: int | None,
        route_port: int = 8000,
    ) -> None:
        name = _resource_name(server_name)
        # Code-first pods run two processes: the user's server (8000) and the
        # platform proxy (route_port). Declare both so the proxy port is the
        # one the Service targets. dict.fromkeys dedups when they're equal.
        container_ports = [{"containerPort": p} for p in dict.fromkeys([8000, route_port])]
        container: dict[str, Any] = {
            "name": "mcp",
            "image": tag,
            "imagePullPolicy": "Always",
            "ports": container_ports,
            "env": _env_list(env_vars),
        }
        resources = _container_resources(cpu_limit, memory_limit_mb)
        if resources is not None:
            container["resources"] = resources
        pod_spec: dict[str, Any] = {"containers": [container]}
        if self._image_pull_secret is not None:
            pod_spec["imagePullSecrets"] = [{"name": self._image_pull_secret}]

        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": _all_labels(server_name, template_name),
            },
            "spec": {
                "replicas": replicas,
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app": name,
                            LABEL_MANAGED: "true",
                            LABEL_SERVER_NAME: server_name,
                        }
                    },
                    "spec": pod_spec,
                },
            },
        }
        self._create_or_replace("apis/apps/v1", "deployments", name, manifest)

    def _apply_service(
        self, server_name: str, template_name: str, route_port: int = 8000
    ) -> None:
        name = _resource_name(server_name)
        # Keep the Service port at 8000 (the stable contract the IngressRoute and
        # the platform's internal McpClient depend on) but aim targetPort at the
        # routed port, so code-first traffic lands on the proxy.
        manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": _all_labels(server_name, template_name),
            },
            "spec": {
                "selector": {"app": name},
                "ports": [
                    {"port": 8000, "targetPort": route_port, "protocol": "TCP"}
                ],
                "type": "ClusterIP",
            },
        }
        self._create_or_replace("api/v1", "services", name, manifest)

    def _apply_middleware(self, server_name: str) -> None:
        name = _resource_name(server_name) + "-strip"
        manifest = {
            "apiVersion": TRAEFIK_API,
            "kind": "Middleware",
            "metadata": {"name": name, "namespace": self._namespace},
            "spec": {"stripPrefix": {"prefixes": [f"/s/{server_name}"]}},
        }
        self._create_or_replace(f"apis/{TRAEFIK_API}", "middlewares", name, manifest)

    def _apply_ingressroute(self, server_name: str) -> None:
        name = _resource_name(server_name)
        entrypoints = [
            ep.strip()
            for ep in (get_settings().mcp_traefik_entrypoints or "web").split(",")
            if ep.strip()
        ]
        manifest = {
            "apiVersion": TRAEFIK_API,
            "kind": "IngressRoute",
            "metadata": {"name": name, "namespace": self._namespace},
            "spec": {
                "entryPoints": entrypoints,
                "routes": [
                    {
                        "match": f"PathPrefix(`/s/{server_name}`)",
                        "kind": "Rule",
                        "services": [{"name": name, "port": 8000}],
                        "middlewares": [{"name": name + "-strip"}],
                    }
                ],
            },
        }
        self._create_or_replace(f"apis/{TRAEFIK_API}", "ingressroutes", name, manifest)

    def _create_or_replace(
        self, api_group: str, kind: str, name: str, manifest: dict
    ) -> None:
        try:
            self._http.post(self._path(api_group, kind), manifest)
            return
        except DockerError as e:
            msg = str(e)
            if "409" not in msg and "AlreadyExists" not in msg:
                raise
        # Read current resourceVersion so PUT is accepted.
        existing = self._http.get(self._path(api_group, kind) + "/" + name)
        rv = ((existing.get("metadata") or {}).get("resourceVersion"))
        if rv:
            manifest.setdefault("metadata", {})["resourceVersion"] = rv
        self._http.put(self._path(api_group, kind) + "/" + name, manifest)

    def _scale_deployment(self, server_name: str, replicas: int) -> None:
        name = _resource_name(server_name)
        self._http.patch(
            self._path("apis/apps/v1", "deployments") + "/" + name + "/scale",
            {"spec": {"replicas": replicas}},
        )
        logger.info("Scaled k8s deployment %s to %d replicas", name, replicas)

    # ---- Read helpers ----

    def _find_deployment(self, server_name: str) -> dict | None:
        name = _resource_name(server_name)
        try:
            d = self._http.get(self._path("apis/apps/v1", "deployments") + "/" + name)
        except DockerNotFoundError:
            return None
        labels = ((d.get("metadata") or {}).get("labels")) or {}
        if labels.get(LABEL_MANAGED) != "true":
            return None
        return d

    def _pick_pod(self, server_name: str) -> str | None:
        """Pick a pod backing the Deployment to read logs from. Prefer Running."""
        name = _resource_name(server_name)
        pods = self._http.get(
            self._path("api/v1", "pods"), {"labelSelector": f"app={name}"}
        )
        items = pods.get("items") or []
        if not items:
            return None
        # First Running pod wins; otherwise first listed.
        for pod in items:
            phase = ((pod.get("status") or {}).get("phase")) or ""
            if phase == "Running":
                return (pod.get("metadata") or {}).get("name")
        return ((items[0].get("metadata") or {}).get("name"))

    def _deployment_to_dict(self, d: dict) -> dict:
        labels = ((d.get("metadata") or {}).get("labels")) or {}
        spec = d.get("spec") or {}
        status = d.get("status") or {}
        desired = int(spec.get("replicas") or 0)
        available = int(status.get("availableReplicas") or 0)
        ready = int(status.get("readyReplicas") or 0)

        # Surface pod-level data (health from readiness, restartCount from container statuses).
        # If the lookup fails we still return the shape with stub values rather than crash.
        health, restart_count, placement = self._pod_aggregate_status(
            labels.get(LABEL_SERVER_NAME, "")
        )

        # Lab's "status" field uses values like 'running' / 'pending' / 'exited'
        # to match the Docker container.State.Status convention; map K8s state in.
        if desired == 0:
            ph_status = "stopped"
        elif ready > 0:
            ph_status = "running"
        else:
            ph_status = "pending"

        return {
            "name": labels.get(LABEL_SERVER_NAME, ""),
            "template": labels.get(LABEL_TEMPLATE, ""),
            "status": ph_status,
            "health": health,
            "restart_count": restart_count,
            "created_at": (d.get("metadata") or {}).get("creationTimestamp", ""),
            "replicas_running": available,
            "placement": placement,
        }

    def _pod_aggregate_status(
        self, server_name: str
    ) -> tuple[str | None, int | None, list[dict]]:
        """Collapse the pods backing a Deployment into (health, restart_count,
        placement). health = 'healthy' if any pod has all containers ready;
        'starting' if any are still pending; 'unhealthy' otherwise. restart_count
        is the max across container statuses (matches Docker's per-server view).
        placement names the node each pod landed on (spec.nodeName / hostIP),
        mirroring the Swarm task-placement shape so the UI renders both the same.
        Built from the same pod list - no extra API call."""
        if not server_name:
            return None, None, []
        name = _resource_name(server_name)
        try:
            pods = self._http.get(
                self._path("api/v1", "pods"), {"labelSelector": f"app={name}"}
            )
        except DockerError:
            return None, None, []
        items = pods.get("items") or []
        if not items:
            return None, None, []

        any_ready = False
        any_pending = False
        max_restarts = 0
        placement: list[dict] = []
        for pod in items:
            meta = pod.get("metadata") or {}
            pod_spec = pod.get("spec") or {}
            pod_status = pod.get("status") or {}
            phase = pod_status.get("phase") or ""
            statuses = pod_status.get("containerStatuses") or []
            ready = bool(statuses) and all(bool(s.get("ready")) for s in statuses)
            if ready:
                any_ready = True
            elif phase in ("Pending", "ContainerCreating", ""):
                any_pending = True
            for s in statuses:
                rc = s.get("restartCount")
                if isinstance(rc, int) and rc > max_restarts:
                    max_restarts = rc
            node_name = pod_spec.get("nodeName") or None
            placement.append({
                "task_id": meta.get("name", ""),
                "node_id": pod_status.get("hostIP") or node_name or "",
                "node_name": node_name,
                "state": phase or "unknown",
                "slot": None,
                "error": None,
            })
        if any_ready:
            health = "healthy"
        elif any_pending:
            health = "starting"
        else:
            health = "unhealthy"
        return health, max_restarts, placement

    # ---- Path helpers ----

    def _path(self, api_group: str, kind: str) -> str:
        return f"{api_group.rstrip('/')}/namespaces/{self._namespace}/{kind}"

    def _job_path(self, namespace: str) -> str:
        return f"apis/batch/v1/namespaces/{namespace}/jobs"


def _env_list(env_vars: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": str(k), "value": str(v)} for k, v in env_vars.items()]


def _kaniko_job_name(server_name: str) -> str:
    """DNS-1123, max 63 chars. Append a short timestamp so back-to-back retries
    don't collide with the previous Job (which lingers via ttlSecondsAfterFinished)."""
    suffix = str(int(time.time()))[-8:]
    base = "mcp-build-" + _sanitize_label(server_name)
    return f"{base[:54]}-{suffix}"


def _write_context_tar(build_context: Path, tar_path: Path) -> None:
    """Mirror DockerClient._tar_bytes — but write to a known path instead of a
    bytes buffer, since kaniko reads from disk via --context=tar:///.

    kaniko's tar:// context handler always gunzips the stream, so the tarball
    must be gzip-compressed; a plain tar fails with 'gzip: invalid header'. We
    build it with the stdlib tarfile module rather than shelling out, so the api
    image needs neither the tar nor the gzip binary (the slim base ships neither
    reliably).
    """
    if not build_context.is_dir():
        raise DockerError(f"Build context not a directory: {build_context}")
    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            for entry in sorted(build_context.iterdir()):
                if entry.name == tar_path.name:
                    continue
                tar.add(entry, arcname=entry.name)
    except OSError as e:
        raise DockerError(f"failed to build context tar for {build_context}: {e}") from e


class _PodLogStream:
    """Decode pod /log chunks for the SSE route. Pod logs are plain UTF-8
    bytes (no Docker frame headers), so we just decode and yield."""

    def __init__(self, source: Iterable[bytes]):
        self._source = source

    def __iter__(self):
        for chunk in self._source:
            if not chunk:
                continue
            yield chunk.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._source.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
