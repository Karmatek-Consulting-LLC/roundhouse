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
        redirect_uri="https://app/api/auth/oidc/callback",
        client_secret=secret,
    )
    db.flush()


def test_secret_encrypted_at_rest_and_roundtrips(db):
    _save(db)
    stored = get_setting(db, SETTING_ENTRA_CLIENT_SECRET)
    assert stored and stored != "s3cret"
    assert looks_encrypted(stored)  # not plaintext on disk
    assert sso_config.load(db).client_secret == "s3cret"  # decrypts back


def test_enabled_only_when_all_present(db):
    _save(db)
    assert sso_config.load(db).enabled is True
    # Clear the redirect URI -> no longer enabled.
    sso_config.save(
        db, tenant_id="tenant-1", client_id="client-1", redirect_uri="", client_secret=None
    )
    db.flush()
    assert sso_config.load(db).enabled is False


def test_secret_none_keeps_existing(db):
    _save(db, secret="keepme")
    sso_config.save(
        db,
        tenant_id="tenant-2",
        client_id="client-2",
        redirect_uri="https://app/api/auth/oidc/callback",
        client_secret=None,
    )
    db.flush()
    cfg = sso_config.load(db)
    assert cfg.tenant_id == "tenant-2"
    assert cfg.client_secret == "keepme"  # unchanged
    assert sso_config.secret_configured(db) is True


def test_secret_empty_clears(db):
    _save(db, secret="dropme")
    sso_config.save(
        db,
        tenant_id="tenant-1",
        client_id="client-1",
        redirect_uri="https://app/api/auth/oidc/callback",
        client_secret="",
    )
    db.flush()
    assert sso_config.secret_configured(db) is False
    assert sso_config.load(db).enabled is False


def test_discovery_and_issuer_scoped_to_tenant(db):
    _save(db)
    cfg = sso_config.load(db)
    assert cfg.issuer == "https://login.microsoftonline.com/tenant-1/v2.0"
    assert "tenant-1/v2.0/.well-known/openid-configuration" in cfg.discovery_url
