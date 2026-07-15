"""Base-image registry: credential resolution for authenticated FROM pulls +
UI-configurable base images. Covers server_service helpers and the docker
X-Registry-Config encoding used to authenticate the build daemon's base pull."""
from __future__ import annotations

import base64
import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app.crypto import encrypt
from app.db import Base
from app.platform_settings import (
    SETTING_BASE_REGISTRY,
    SETTING_BASE_REGISTRY_PASSWORD,
    SETTING_BASE_REGISTRY_USERNAME,
    SETTING_MCP_BASE_BUILD_IMAGE,
    SETTING_MCP_BASE_RUNTIME_IMAGE,
    put_setting,
)
from app.services import docker as docker_mod
from app.services.server_service import ServerService, _registry_host


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, autoflush=True, future=True)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _app_key(monkeypatch):
    from app.config import get_settings

    key = "base64:" + base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("APP_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _service() -> ServerService:
    # These helpers only touch db + settings, never the orchestrator/store.
    return ServerService(docker=None, store=None, templates=None)  # type: ignore[arg-type]


# ---- Registry host parsing ----

def test_registry_host_extracts_private_registry():
    assert _registry_host("dhi.io/python:3.14-debian13-dev") == "dhi.io"
    assert _registry_host("registry.example.com:5000/foo/bar:tag") == "registry.example.com:5000"
    assert _registry_host("localhost/img") == "localhost"


def test_registry_host_none_for_docker_hub():
    assert _registry_host("python:3.12-slim") is None
    assert _registry_host("library/python:3.12") is None
    assert _registry_host("") is None


# ---- Effective base images (DB override vs env default) ----

def test_effective_base_images_defaults_to_env(db):
    build, runtime = _service().effective_base_images(db)
    assert build == "dhi.io/python:3.14-debian13-dev"
    assert runtime == "dhi.io/python:3.14-debian13"


def test_effective_base_images_uses_db_override(db):
    put_setting(db, SETTING_MCP_BASE_BUILD_IMAGE, "myorg/python:3.14-dev")
    put_setting(db, SETTING_MCP_BASE_RUNTIME_IMAGE, "myorg/python:3.14")
    build, runtime = _service().effective_base_images(db)
    assert build == "myorg/python:3.14-dev"
    assert runtime == "myorg/python:3.14"


# ---- base_registry_auth ----

def test_base_registry_auth_none_when_unconfigured(db):
    assert _service().base_registry_auth(db) is None


def test_base_registry_auth_derives_host_from_base_images(db):
    from app.config import get_settings

    put_setting(db, SETTING_BASE_REGISTRY_USERNAME, "robot")
    put_setting(db, SETTING_BASE_REGISTRY_PASSWORD, encrypt("s3cret", get_settings().app_key))
    auth = _service().base_registry_auth(db)
    assert auth == {"dhi.io": {"username": "robot", "password": "s3cret"}}


def test_base_registry_auth_uses_explicit_host(db):
    from app.config import get_settings

    put_setting(db, SETTING_BASE_REGISTRY, "mirror.example.com")
    put_setting(db, SETTING_BASE_REGISTRY_USERNAME, "u")
    put_setting(db, SETTING_BASE_REGISTRY_PASSWORD, encrypt("p", get_settings().app_key))
    auth = _service().base_registry_auth(db)
    assert auth == {"mirror.example.com": {"username": "u", "password": "p"}}


def test_base_registry_auth_none_when_password_missing(db):
    put_setting(db, SETTING_BASE_REGISTRY_USERNAME, "u")
    assert _service().base_registry_auth(db) is None


# ---- X-Registry-Config encoding ----

def test_encode_registry_config_round_trips():
    encoded = docker_mod._encode_registry_config(
        {"dhi.io": {"username": "u", "password": "p"}}
    )
    # urlsafe base64 without padding; decode by re-padding.
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    assert decoded == {"dhi.io": {"username": "u", "password": "p"}}


class _FakeHttp:
    def __init__(self):
        self.calls: list[dict] = []

    def post_stream(self, path, query, body, headers):
        self.calls.append({"path": path, "query": query, "headers": headers})
        return iter(())  # no frames -> build reports success


def test_build_image_sends_x_registry_config(tmp_path):
    client = docker_mod.DockerClient.__new__(docker_mod.DockerClient)
    fake = _FakeHttp()
    client._http = fake
    client._tar_bytes = lambda ctx: b"tar"  # type: ignore[method-assign]
    client.build_image(
        "srv", tmp_path,
        base_registry_auth={"dhi.io": {"username": "u", "password": "p"}},
    )
    hdr = fake.calls[0]["headers"]
    assert "X-Registry-Config" in hdr
    padded = hdr["X-Registry-Config"] + "=" * (-len(hdr["X-Registry-Config"]) % 4)
    assert json.loads(base64.urlsafe_b64decode(padded)) == {
        "dhi.io": {"username": "u", "password": "p"}
    }


def test_build_image_omits_header_without_base_auth(tmp_path):
    client = docker_mod.DockerClient.__new__(docker_mod.DockerClient)
    fake = _FakeHttp()
    client._http = fake
    client._tar_bytes = lambda ctx: b"tar"  # type: ignore[method-assign]
    client.build_image("srv", tmp_path)
    assert "X-Registry-Config" not in fake.calls[0]["headers"]

