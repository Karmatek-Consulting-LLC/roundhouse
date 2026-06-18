"""DockerClient network resolution — explicit MCP_DOCKER_NETWORK wins, else it
derives the network from the API's own attached container so the stack file
doesn't have to hardcode the stack-prefixed name."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.services.docker import DockerClient
from app.services.docker_http import DockerError


@pytest.fixture
def http():
    return MagicMock()


def _client(http) -> DockerClient:
    get_settings.cache_clear()
    return DockerClient(http=http)


def test_explicit_network_wins_without_a_docker_call(http, monkeypatch):
    monkeypatch.setenv("MCP_DOCKER_NETWORK", "custom-net")
    client = _client(http)
    assert client.network == "custom-net"
    http.get.assert_not_called()


def test_derives_roundhouse_network_from_own_container(http):
    http.get.return_value = {
        "NetworkSettings": {
            "Networks": {"bridge": {}, "roundhouse_roundhouse-network": {}}
        }
    }
    client = _client(http)
    assert client.network == "roundhouse_roundhouse-network"
    # Cached: a second access does not re-query the daemon.
    assert client.network == "roundhouse_roundhouse-network"
    assert http.get.call_count == 1


def test_derive_falls_back_when_lookup_fails(http):
    http.get.side_effect = DockerError("no such container")
    client = _client(http)
    assert client.network == "roundhouse-network"
