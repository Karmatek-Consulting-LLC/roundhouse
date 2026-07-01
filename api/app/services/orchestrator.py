"""Backend-agnostic contract for managing MCP server workloads.

Two implementations live alongside this module:

    * DockerClient  (services/docker.py)      — standalone Docker + Swarm
    * KubernetesClient (services/kubernetes.py) — Kubernetes apiserver

Pick one at process startup via MCP_ORCHESTRATOR; call sites should depend on
the Orchestrator protocol via `get_orchestrator()`, not on the concrete class.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class LogStream(Protocol):
    """A bounded iterator of decoded log chunks the SSE endpoint can drive.

    Both DockerHttp's frame-demux stream and the KubernetesHttp pod-log stream
    expose this minimal surface so the route handler in routes/servers.py
    doesn't care which backend produced it.
    """

    def __iter__(self) -> Iterable[str]: ...
    def close(self) -> None: ...


class Orchestrator(Protocol):
    """Operations every workload backend must implement.

    Method names mirror DockerClient's public surface so the swap is mechanical
    for existing call sites. `mode()` and `supports_scaling()` are new — they
    replace the Docker-specific `swarm_mode()` flag at API boundaries so the
    frontend can render the right scaling UI under any backend.
    """

    # ---- Identity ----

    def mode(self) -> str:
        """Stable id exposed to API/UI: 'docker', 'docker-swarm', or 'kubernetes'."""

    def supports_scaling(self) -> bool:
        """True when the backend can run >1 replica of a server."""

    def list_node_labels(self) -> list[dict]:
        """Distinct node-label selectors available for placement, as
        [{"key","value","nodes"}]. Empty on backends without node labels (e.g.
        standalone Docker); populates the deploy-time placement selector."""

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
        placement_constraints: list[dict] | None = None,
    ) -> dict: ...

    def list_servers(self) -> list[dict]: ...
    def get_server(self, server_name: str) -> dict | None: ...
    def start_server(self, server_name: str, replicas: int = 1) -> dict | None: ...

    def scale_server(self, server_name: str, replicas: int) -> dict | None:
        """Return None on backends that do not support scaling."""

    def stop_server(self, server_name: str) -> dict | None: ...
    def remove_server(self, server_name: str, registry_prefix: str | None = None) -> bool: ...
    def update_runtime_env(self, server_name: str, env_vars: dict[str, str]) -> dict | None: ...
    def get_server_logs(self, server_name: str, tail: int = 200) -> str: ...
    def stream_server_logs(self, server_name: str, tail: int = 100) -> LogStream: ...

    # ---- Image identity ----

    def image_tag(self, server_name: str, registry_prefix: str | None = None) -> str: ...


# ---- Factory ----

_singleton: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Return the configured backend. Singleton so the swarm-mode cache and
    KubernetesHttp connection pool are shared across requests."""
    global _singleton
    if _singleton is not None:
        return _singleton

    from app.config import get_settings

    backend = (get_settings().mcp_orchestrator or "docker").strip().lower()
    if backend == "docker":
        from app.services.docker import DockerClient

        _singleton = DockerClient()
    elif backend in ("kubernetes", "k8s"):
        from app.services.kubernetes import KubernetesClient

        _singleton = KubernetesClient()
    else:
        raise RuntimeError(
            f"Unknown MCP_ORCHESTRATOR={backend!r}; expected 'docker' or 'kubernetes'"
        )
    return _singleton


def reset_orchestrator_for_tests() -> None:
    """Drop the singleton so tests can swap implementations between cases."""
    global _singleton
    _singleton = None
