"""Tests for Swarm node-label placement on DockerClient — verify the service
spec sent to the engine carries the right `node.labels.*` constraints and that
`list_node_labels` derives distinct pairs from real node labels. DockerHttp is
mocked, so these run with no live Docker."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.docker import DockerClient, _placement_constraints_to_docker


@pytest.fixture
def http():
    return MagicMock()


@pytest.fixture
def client(http):
    c = DockerClient(http=http)
    c._swarm_cache = True  # force swarm mode without an /info round-trip
    return c


@pytest.fixture
def build_context():
    with tempfile.TemporaryDirectory() as d:
        ctx = Path(d) / "demo"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text("FROM alpine\n")
        (ctx / "server.py").write_text("# stub\n")
        yield ctx


# ---- constraint translation ----

def test_translate_pairs_to_docker_constraints():
    assert _placement_constraints_to_docker(
        [{"key": "gpu", "value": "true"}, {"key": "region", "value": "us-east"}]
    ) == ["node.labels.gpu==true", "node.labels.region==us-east"]


def test_translate_skips_blank_and_none():
    assert _placement_constraints_to_docker(None) == []
    assert _placement_constraints_to_docker([{"key": "", "value": "x"}, {"key": "k", "value": ""}]) == []


# ---- _create_service ----

def _capture_service_create(http):
    """Wire the mock so services/create records its spec and _get_service after
    returns a managed service so _create_service doesn't raise."""
    captured: dict = {}

    def post(path, query=None, body=None, headers=None):
        if path == "services/create":
            captured["spec"] = body
            return {"ID": "svc1"}
        return {}

    def get(path, query=None):
        if path == "services":
            return [{
                "ID": "svc1",
                "Spec": {"Name": "mcp-demo", "Labels": {"roundhouse.managed": "true"},
                         "Mode": {"Replicated": {"Replicas": 1}}},
                "Version": {"Index": 1},
                "CreatedAt": "2026-06-01T00:00:00Z",
            }]
        if path == "tasks":
            return []
        if path == "nodes":
            return []
        return {}

    http.post.side_effect = post
    http.get.side_effect = get
    return captured


def test_create_service_sets_placement_constraints(client, http):
    captured = _capture_service_create(http)
    client._create_service(
        "demo", "img:latest", "custom", {}, replicas=1,
        placement_constraints=[{"key": "gpu", "value": "true"}],
    )
    placement = captured["spec"]["TaskTemplate"]["Placement"]
    assert placement == {"Constraints": ["node.labels.gpu==true"]}


def test_create_service_omits_placement_when_empty(client, http):
    captured = _capture_service_create(http)
    client._create_service("demo", "img:latest", "custom", {}, replicas=1)
    assert "Placement" not in captured["spec"]["TaskTemplate"]


# ---- list_node_labels ----

def test_list_node_labels_dedupes_and_counts(client, http):
    http.get.side_effect = lambda path, query=None: [
        {"Spec": {"Labels": {"gpu": "true", "region": "us-east"}}},
        {"Spec": {"Labels": {"gpu": "true", "region": "us-west"}}},
        {"Spec": {"Labels": {}}},
    ] if path == "nodes" else {}
    labels = client.list_node_labels()
    assert labels == [
        {"key": "gpu", "value": "true", "nodes": 2},
        {"key": "region", "value": "us-east", "nodes": 1},
        {"key": "region", "value": "us-west", "nodes": 1},
    ]


def test_list_node_labels_empty_off_swarm(http):
    c = DockerClient(http=http)
    c._swarm_cache = False
    assert c.list_node_labels() == []
