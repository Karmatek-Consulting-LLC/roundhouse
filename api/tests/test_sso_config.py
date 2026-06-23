"""DB-backed Entra SSO connection config: encryption at rest + write-only secret."""
from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app.crypto import looks_encrypted
from app.db import Base
from app.platform_settings import SETTING_ENTRA_CLIENT_SECRET, get_setting
from app.services import sso_config


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, autoflush=False, future=True)()
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


def _save(db, secret="s3cret"):
    sso_config.save(
        db,
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret=secret,
    )
    db.flush()


def test_secret_encrypted_at_rest_and_roundtrips(db):
    _save(db)
    stored = get_setting(db, SETTING_ENTRA_CLIENT_SECRET)
    assert stored and stored != "s3cret"
    assert looks_encrypted(stored)  # not plaintext on disk
    assert sso_config.load(db).client_secret == "s3cret"  # decrypts back


def test_enabled_only_when_credentials_present(db):
    _save(db)
    assert sso_config.load(db).enabled is True
    # Clear the client id -> no longer enabled.
    sso_config.save(db, tenant_id="tenant-1", client_id="", client_secret=None)
    db.flush()
    assert sso_config.load(db).enabled is False


def test_redirect_uri_derived_from_base_url(db):
    # No MCP_BASE_URL set in tests -> localhost default.
    assert sso_config.load(db).redirect_uri == "http://localhost:3080/api/auth/oidc/callback"


def test_redirect_uri_tracks_configured_base_url(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MCP_BASE_URL", "https://roundhouse.example.com")
    get_settings.cache_clear()
    assert (
        sso_config.load(db).redirect_uri
        == "https://roundhouse.example.com/api/auth/oidc/callback"
    )


def test_secret_none_keeps_existing(db):
    _save(db, secret="keepme")
    sso_config.save(db, tenant_id="tenant-2", client_id="client-2", client_secret=None)
    db.flush()
    cfg = sso_config.load(db)
    assert cfg.tenant_id == "tenant-2"
    assert cfg.client_secret == "keepme"  # unchanged
    assert sso_config.secret_configured(db) is True


def test_secret_empty_clears(db):
    _save(db, secret="dropme")
    sso_config.save(db, tenant_id="tenant-1", client_id="client-1", client_secret="")
    db.flush()
    assert sso_config.secret_configured(db) is False
    assert sso_config.load(db).enabled is False


def test_discovery_and_issuer_scoped_to_tenant(db):
    _save(db)
    cfg = sso_config.load(db)
    assert cfg.issuer == "https://login.microsoftonline.com/tenant-1/v2.0"
    assert "tenant-1/v2.0/.well-known/openid-configuration" in cfg.discovery_url
