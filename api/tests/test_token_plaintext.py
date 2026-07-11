"""server_auth.token_plaintext — the built-in tester's server-side token
resolution (oldest-by-default, by-name, decryption). SQLite-backed via the
same SessionLocal-repoint pattern as test_store_assets."""
from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def session(monkeypatch, tmp_path):
    import app.db as appdb
    import app.models  # noqa: F401 - register tables on Base.metadata

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    TestSession = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(appdb, "SessionLocal", TestSession)
    appdb.Base.metadata.create_all(engine)
    s = TestSession()
    yield s
    s.close()
    engine.dispose()


def test_no_tokens_returns_none(session):
    from app.services.server_auth import token_plaintext

    assert token_plaintext(session, "demo") is None
    assert token_plaintext(session, "demo", "anything") is None


def test_oldest_by_default_and_by_name(session, monkeypatch):
    monkeypatch.delenv("APP_KEY", raising=False)
    from app.config import get_settings
    from app.services.server_auth import mint_token, token_plaintext

    get_settings.cache_clear()
    _, first = mint_token(session, "demo", "ci", [])
    _, second = mint_token(session, "demo", "claude", ["read"])

    assert token_plaintext(session, "demo") == first
    assert token_plaintext(session, "demo", "claude") == second
    assert token_plaintext(session, "demo", "nope") is None
    # Other servers' tokens are invisible.
    assert token_plaintext(session, "other") is None


def test_decrypts_encrypted_storage(session, monkeypatch):
    key = "base64:" + base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("APP_KEY", key)
    from app.config import get_settings
    from app.services.server_auth import mint_token, token_plaintext

    get_settings.cache_clear()
    row, plain = mint_token(session, "demo", "ci", [])
    assert row.token != plain  # actually encrypted at rest
    assert token_plaintext(session, "demo") == plain
    assert token_plaintext(session, "demo", "ci") == plain
