"""Tests for KubernetesClient — verify the manifests sent to the apiserver
and the failure handling paths. KubernetesHttp is mocked, so these run with
no live cluster."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.docker_http import DockerError
from app.services.kubernetes import KubernetesClient


@pytest.fixture
def http():
    return MagicMock()


@pytest.fixture
def client(http, monkeypatch):
    monkeypatch.setenv("MCP_K8S_NAMESPACE", "mcp-servers")
    monkeypatch.setenv("MCP_TRAEFIK_ENTRYPOINTS", "web,websecure")
    monkeypatch.setenv("MCP_K8S_IMAGE_PULL_SECRET", "mcp-registry")
    # Force the @lru_cache'd settings to re-read the new env.
    from app.config import get_settings

    get_settings.cache_clear()
    return KubernetesClient(http=http)


@pytest.fixture
def build_context():
    with tempfile.TemporaryDirectory() as d:
        ctx = Path(d) / "demo"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text("FROM alpine\n")
        (ctx / "server.py").write_text("# stub\n")
        yield ctx


def test_mode_and_supports_scaling(client):
    assert client.mode() == "kubernetes"
    assert client.supports_scaling() is True


def test_build_and_start_emits_manifests_and_returns_running_status(client, http, build_context, monkeypatch):
    # docker-mode build path: KubernetesClient delegates to DockerClient.
    # Force MCP_K8S_BUILDER=docker so the kaniko path isn't exercised here.
    monkeypatch.setenv("MCP_K8S_BUILDER", "docker")
    from app.config import get_settings

    get_settings.cache_clear()

    fake_builder = MagicMock()
    fake_builder.build_image.return_value = "registry.example/mcp/mcp-server-demo:latest"
    client._docker_builder = fake_builder  # noqa: SLF001 - explicit injection in test

    captured: list[tuple[str, dict]] = []

    def post(path, body):
        captured.append((path, body))
        return {"metadata": {"name": body["metadata"]["name"], "resourceVersion": "1"}}

    http.post.side_effect = post

    # getServer at end + pod aggregate lookup.
    def get(path, query=None):
        if path.endswith("/deployments/mcp-demo"):
            return {
                "metadata": {
                    "labels": {
                        "roundhouse.managed": "true",
                        "roundhouse.server-name": "demo",
                        "roundhouse.template": "custom",
                    },
                    "creationTimestamp": "2026-05-27T00:00:00Z",
                },
                "spec": {"replicas": 2},
                "status": {"replicas": 2, "availableReplicas": 2, "readyReplicas": 2},
            }
        if path.endswith("/pods"):
            return {
                "items": [
                    {
                        "metadata": {"name": "mcp-demo-abc"},
                        "status": {
                            "phase": "Running",
                            "containerStatuses": [{"ready": True, "restartCount": 1}],
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected GET {path}")

    http.get.side_effect = get

    result = client.build_and_start(
        server_name="demo",
        build_context=build_context,
        template_name="custom",
        env_vars={"FOO": "bar"},
        replicas=2,
        registry_prefix="registry.example/mcp",
        cpu_limit=0.5,
        memory_limit_mb=512,
    )

    kinds = {body.get("kind") for _, body in captured}
    assert kinds == {"Deployment", "Service", "Middleware", "IngressRoute"}

    deployment = next(b for _, b in captured if b["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 2
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.example/mcp/mcp-server-demo:latest"
    assert container["env"] == [{"name": "FOO", "value": "bar"}]
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    assert container["resources"]["requests"] == {"cpu": "500m", "memory": "512Mi"}
    assert deployment["spec"]["template"]["spec"]["imagePullSecrets"] == [
        {"name": "mcp-registry"}
    ]

    ingress = next(b for _, b in captured if b["kind"] == "IngressRoute")
    route = ingress["spec"]["routes"][0]
    assert route["match"] == "PathPrefix(`/s/demo`)"
    assert route["middlewares"] == [{"name": "mcp-demo-strip"}]
    assert ingress["spec"]["entryPoints"] == ["web", "websecure"]

    middleware = next(b for _, b in captured if b["kind"] == "Middleware")
    assert middleware["spec"]["stripPrefix"]["prefixes"] == ["/s/demo"]

    assert result["status"] == "running"
    assert result["health"] == "healthy"
    assert result["restart_count"] == 1


def test_build_and_start_requires_registry(client, build_context):
    with pytest.raises(DockerError, match="requires a Docker registry"):
        client.build_and_start(
            server_name="demo",
            build_context=build_context,
            template_name="custom",
        )


def test_scale_server_patches_scale_subresource(client, http):
    http.get.side_effect = [
        # findDeployment
        {
            "metadata": {"labels": {"roundhouse.managed": "true", "roundhouse.server-name": "demo"}},
            "spec": {"replicas": 3},
        },
        # getServer at the end
        {
            "metadata": {"labels": {"roundhouse.managed": "true", "roundhouse.server-name": "demo"}},
            "spec": {"replicas": 3},
            "status": {"availableReplicas": 3, "readyReplicas": 3},
        },
        # pod aggregate lookup
        {"items": [{"status": {"phase": "Running", "containerStatuses": [{"ready": True}]}}]},
    ]
    http.patch.return_value = {}

    out = client.scale_server("demo", 3)
    http.patch.assert_called_once_with(
        "apis/apps/v1/namespaces/mcp-servers/deployments/mcp-demo/scale",
        {"spec": {"replicas": 3}},
    )
    assert out["replicas_running"] == 3


def test_remove_server_deletes_all_four_resources(client, http):
    # findDeployment returns a managed deployment so we record existed=True.
    http.get.return_value = {
        "metadata": {"labels": {"roundhouse.managed": "true"}},
        "spec": {"replicas": 1},
    }
    deleted: list[str] = []
    http.delete.side_effect = lambda path: deleted.append(path)

    assert client.remove_server("demo") is True
    assert deleted == [
        "apis/apps/v1/namespaces/mcp-servers/deployments/mcp-demo",
        "api/v1/namespaces/mcp-servers/services/mcp-demo",
        "apis/traefik.io/v1alpha1/namespaces/mcp-servers/ingressroutes/mcp-demo",
        "apis/traefik.io/v1alpha1/namespaces/mcp-servers/middlewares/mcp-demo-strip",
    ]


def test_list_servers_maps_deployments_to_canonical_shape(client, http):
    http.get.side_effect = [
        # the listing call
        {
            "items": [
                {
                    "metadata": {
                        "labels": {
                            "roundhouse.managed": "true",
                            "roundhouse.server-name": "demo",
                            "roundhouse.template": "custom",
                        },
                        "creationTimestamp": "2026-05-27T00:00:00Z",
                    },
                    "spec": {"replicas": 1},
                    "status": {"availableReplicas": 0, "readyReplicas": 0},
                }
            ]
        },
        # _pod_aggregate_status looks pods up next
        {"items": []},
    ]
    out = client.list_servers()
    assert len(out) == 1
    assert out[0]["name"] == "demo"
    assert out[0]["template"] == "custom"
    assert out[0]["status"] == "pending"
    assert out[0]["replicas_running"] == 0


def test_kaniko_path_writes_job_with_node_affinity(client, http, build_context, monkeypatch):
    monkeypatch.setenv("MCP_K8S_BUILDER", "kaniko")
    monkeypatch.setenv("MCP_K8S_BUILDER_PVC", "mcp-server-data")
    monkeypatch.setenv("MCP_K8S_BUILDER_REGISTRY_SECRET", "mcp-registry-creds")
    monkeypatch.setenv("MCP_K8S_BUILDER_NAMESPACE", "roundhouse")
    monkeypatch.setenv("MCP_K8S_BUILDER_IMAGE", "gcr.io/kaniko-project/executor:v1.20.0")
    monkeypatch.setenv("MCP_K8S_BUILDER_TIMEOUT", "60")
    monkeypatch.setenv("NODE_NAME", "node-a")
    from app.config import get_settings

    get_settings.cache_clear()

    captured_post: list[tuple[str, dict]] = []

    def post(path, body):
        captured_post.append((path, body))
        return {"metadata": {"name": body.get("metadata", {}).get("name", "")}}

    # First GET on the Job returns succeeded=1.
    def get(path, query=None):
        if re.match(r"apis/batch/v1/namespaces/roundhouse/jobs/mcp-build-demo-\d+$", path):
            return {"status": {"succeeded": 1}}
        if path.endswith("/deployments/mcp-demo"):
            return {
                "metadata": {"labels": {"roundhouse.managed": "true", "roundhouse.server-name": "demo"}},
                "spec": {"replicas": 1},
                "status": {"availableReplicas": 1, "readyReplicas": 1},
            }
        if path.endswith("/pods"):
            return {"items": []}
        raise AssertionError(f"unexpected GET {path}")

    http.get.side_effect = get
    http.post.side_effect = post
    http.delete.return_value = None

    client.build_and_start(
        server_name="demo",
        build_context=build_context,
        template_name="custom",
        env_vars={},
        replicas=1,
        registry_prefix="registry.example/mcp",
    )

    jobs = [b for path, b in captured_post if path.startswith("apis/batch/v1/")]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["ttlSecondsAfterFinished"] == 600
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["containers"][0]["image"] == "gcr.io/kaniko-project/executor:v1.20.0"
    args = pod["containers"][0]["args"]
    assert "--destination=registry.example/mcp/mcp-server-demo:latest" in args
    assert "--context=tar:///workspace/.kaniko-context.tar" in args
    assert pod["volumes"][0]["persistentVolumeClaim"] == {
        "claimName": "mcp-server-data",
        "readOnly": True,
    }
    assert pod["volumes"][1]["secret"]["secretName"] == "mcp-registry-creds"
    affinity = pod["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]
    assert affinity["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["node-a"]


def test_kaniko_failure_surfaces_pod_logs(client, http, build_context, monkeypatch):
    monkeypatch.setenv("MCP_K8S_BUILDER", "kaniko")
    monkeypatch.setenv("MCP_K8S_BUILDER_PVC", "mcp-server-data")
    monkeypatch.setenv("MCP_K8S_BUILDER_REGISTRY_SECRET", "mcp-registry-creds")
    monkeypatch.setenv("MCP_K8S_BUILDER_NAMESPACE", "roundhouse")
    monkeypatch.setenv("MCP_K8S_BUILDER_TIMEOUT", "60")
    from app.config import get_settings

    get_settings.cache_clear()

    def get(path, query=None):
        if re.match(r"apis/batch/v1/namespaces/roundhouse/jobs/mcp-build-demo-\d+$", path):
            return {"status": {"failed": 1}}
        if path == "api/v1/namespaces/roundhouse/pods":
            return {"items": [{"metadata": {"name": "mcp-build-demo-pod-xyz"}}]}
        raise AssertionError(f"unexpected GET {path}")

    http.get.side_effect = get
    http.get_raw.return_value = "ERROR: unauthorized: authentication required\n"
    http.post.return_value = {}
    http.delete.return_value = None

    with pytest.raises(DockerError) as excinfo:
        client.build_and_start(
            server_name="demo",
            build_context=build_context,
            template_name="custom",
            env_vars={},
            replicas=1,
            registry_prefix="registry.example/mcp",
        )
    assert "kaniko build failed" in str(excinfo.value)
    assert "unauthorized" in str(excinfo.value)


def test_resource_limits_omitted_when_unset(client, http, build_context, monkeypatch):
    """If cpu_limit and memory_limit_mb are both unset, container.resources is omitted."""
    monkeypatch.setenv("MCP_K8S_BUILDER", "docker")
    from app.config import get_settings

    get_settings.cache_clear()

    fake_builder = MagicMock()
    fake_builder.build_image.return_value = "registry.example/mcp/mcp-server-demo:latest"
    client._docker_builder = fake_builder  # noqa: SLF001

    captured: list[dict] = []
    http.post.side_effect = lambda path, body: captured.append(body) or {
        "metadata": {"name": body["metadata"]["name"]}
    }
    http.get.return_value = {
        "metadata": {"labels": {"roundhouse.managed": "true", "roundhouse.server-name": "demo"}},
        "spec": {"replicas": 1},
        "status": {"availableReplicas": 1, "readyReplicas": 1},
    }

    client.build_and_start(
        server_name="demo",
        build_context=build_context,
        template_name="custom",
        env_vars={},
        replicas=1,
        registry_prefix="registry.example/mcp",
    )
    deployment = next(b for b in captured if b["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "resources" not in container


def test_pod_aggregate_status_includes_node_placement(client, http):
    # Pods carry their scheduled node in spec.nodeName / status.hostIP; surface
    # that as placement so the UI can show where a server runs on K8s, the same
    # way it does for Swarm tasks.
    http.get.return_value = {
        "items": [
            {
                "metadata": {"name": "mcp-demo-7c9-abc"},
                "spec": {"nodeName": "worker-2"},
                "status": {
                    "phase": "Running",
                    "hostIP": "10.0.1.5",
                    "containerStatuses": [{"ready": True, "restartCount": 2}],
                },
            }
        ]
    }
    health, restarts, placement = client._pod_aggregate_status("demo")  # noqa: SLF001
    assert health == "healthy"
    assert restarts == 2
    assert placement == [
        {
            "task_id": "mcp-demo-7c9-abc",
            "node_id": "10.0.1.5",
            "node_name": "worker-2",
            "state": "Running",
            "slot": None,
            "error": None,
        }
    ]
