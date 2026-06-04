"""Code-first servers don't get the platform middleware baked into their
server.py (we don't author it), so they're fronted by a generated proxy that
re-applies the same middleware. These tests pin that wiring: codegen emits the
proxy + a proxy-aware Dockerfile, and the orchestrators route to the proxy
port. See codegen.route_port_for / generate_proxy_py."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from app.services import codegen
from app.services.docker import _all_labels
from app.services.server_service import ServerService
from app.services.spec import ServerSpec


def _code_spec() -> ServerSpec:
    return ServerSpec(name="acme", mode="code", source="import x\n")


def _structured_spec() -> ServerSpec:
    return ServerSpec(name="widgets")


def test_route_port_splits_by_mode():
    assert codegen.route_port_for(_code_spec()) == codegen.PROXY_PORT == 8001
    assert codegen.route_port_for(_structured_spec()) == codegen.BACKEND_PORT == 8000


def test_generated_proxy_is_valid_and_reuses_platform_middleware():
    src = codegen.generate_proxy_py(_code_spec(), format_output=False)
    ast.parse(src)  # must be importable Python
    # Forwards to the user's server over loopback on the backend port.
    assert "http://127.0.0.1:8000/mcp" in src
    assert "FastMCP.as_proxy(" in src
    # Same middleware structured servers get: the class, its registration,
    # and the metrics/health routes all come from the shared codegen template.
    assert "class _PlatformMiddleware(_Middleware):" in src
    assert "mcp.add_middleware(_PlatformMiddleware())" in src
    assert "@mcp.custom_route(\"/metrics\"" in src
    assert "@mcp.custom_route(\"/healthz\"" in src
    # Supervises the user's server.py and serves on the proxy port.
    assert 'subprocess.Popen([sys.executable, "server.py"])' in src
    assert "port=8001," in src


def test_dockerfile_targets_proxy_for_code_mode_only():
    code_df = codegen.generate_dockerfile(_code_spec())
    assert 'CMD ["python", "proxy.py"]' in code_df
    assert "EXPOSE 8001" in code_df
    assert "127.0.0.1:8001/healthz" in code_df

    struct_df = codegen.generate_dockerfile(_structured_spec())
    assert 'CMD ["python", "server.py"]' in struct_df
    assert "EXPOSE 8000" in struct_df
    assert "127.0.0.1:8000/healthz" in struct_df


def test_write_build_context_manages_proxy_file_lifecycle():
    out = Path(tempfile.mkdtemp())

    # Code mode: proxy.py is written; server.py is the user's source verbatim.
    codegen.write_build_context(_code_spec(), out)
    assert (out / "proxy.py").exists()
    assert (out / "server.py").read_text() == "import x\n"

    # Redeploying the same dir as a structured server clears the stale proxy.
    codegen.write_build_context(_structured_spec(), out)
    assert not (out / "proxy.py").exists()


def test_docker_labels_route_to_proxy_port():
    # Traefik's loadbalancer port + the persisted route-port label track the
    # port we hand in, so code-first traffic lands on the proxy.
    labels = _all_labels("acme", "custom", 8001)
    assert labels["roundhouse.route-port"] == "8001"
    assert (
        labels["traefik.http.services.mcp-acme.loadbalancer.server.port"] == "8001"
    )

    # Default keeps structured servers pointed straight at their own process.
    default_labels = _all_labels("widgets", "custom")
    assert default_labels["roundhouse.route-port"] == "8000"
    assert (
        default_labels["traefik.http.services.mcp-widgets.loadbalancer.server.port"]
        == "8000"
    )


def _service(mode: str, spec: ServerSpec | None) -> ServerService:
    docker = MagicMock()
    docker.mode.return_value = mode
    store = MagicMock()
    store.load.return_value = spec
    return ServerService(docker, store, MagicMock())


def test_metrics_url_targets_the_proxy_where_needed():
    # Docker/Swarm hit the container directly, so code-first /metrics (which
    # lives on the proxy) must be scraped on the proxy port.
    assert (
        _service("docker", _code_spec()).metrics_url("acme")
        == "http://mcp-acme:8001/metrics"
    )
    assert (
        _service("docker-swarm", _code_spec()).metrics_url("acme")
        == "http://mcp-acme:8001/metrics"
    )
    # Structured servers expose /metrics on their own process.
    assert (
        _service("docker", _structured_spec()).metrics_url("widgets")
        == "http://mcp-widgets:8000/metrics"
    )
    # K8s reaches servers via a Service pinned to 8000 that remaps to the proxy
    # internally, so the scrape URL stays on 8000 even for code-first.
    assert (
        _service("kubernetes", _code_spec()).metrics_url("acme")
        == "http://mcp-acme:8000/metrics"
    )
    # No spec on disk -> safe default, never a crash.
    assert (
        _service("docker", None).metrics_url("ghost")
        == "http://mcp-ghost:8000/metrics"
    )
