"""Auth log (log_events): recorder field mapping/redaction, the /api/logs
list/search/export handlers, and the login-route instrumentation.

Calls route handlers directly with a session, mirroring test_users.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app import audit, logbook
from app.db import Base
from app.models import AuditEvent, LogEvent, User
from app.routes.logs import (
    RetentionIn,
    event_types,
    export,
    get_retention,
    list_events,
    prune_now,
    put_retention,
)
from app.services import log_retention


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


@pytest.fixture
def admin(db):
    a = User(email="admin@corp.com", display_name="Admin", role="superadmin",
             auth_source="local", password_hash="x")
    db.add(a)
    db.flush()
    return a


class FakeRequest:
    """Just enough of starlette.Request for logbook: headers + client."""

    def __init__(self, headers: dict[str, str] | None = None, host: str = "10.0.0.5"):
        self.headers = headers or {}

        class _Client:
            def __init__(self, h):
                self.host = h

        self.client = _Client(host)


def _add(db, **over):
    over.setdefault("context", "auth")
    over.setdefault("event_type", "login")
    over.setdefault("outcome", "failure")
    e = LogEvent(**over)
    db.add(e)
    db.flush()
    return e


# ---- logbook.build_event ---------------------------------------------------

def test_build_event_maps_user_and_request_fields(admin):
    req = FakeRequest(headers={"user-agent": "pytest-agent"})
    e = logbook.build_event(
        "auth", "login", "success", request=req, user=admin, message="ok"
    )
    assert e.context == "auth"
    assert e.actor_id == str(admin.id)
    assert e.actor_email == "admin@corp.com"
    assert e.ip == "10.0.0.5"
    assert e.user_agent == "pytest-agent"


def test_build_event_prefers_forwarded_for_first_hop():
    req = FakeRequest(headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    e = logbook.build_event("auth", "login", "failure", request=req, email="a@b.c")
    assert e.ip == "203.0.113.9"
    assert e.actor_email == "a@b.c"
    assert e.actor_id is None


def test_build_event_redacts_sensitive_detail_keys():
    e = logbook.build_event(
        "auth", "login", "failure",
        detail={"reason": "bad_password", "password": "hunter2"},
    )
    assert e.detail == {"reason": "bad_password", "password": "[redacted]"}


def test_record_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(logbook, "db_session", boom)
    logbook.record("auth", "login", "failure")  # must swallow, not raise


# ---- GET /api/logs ---------------------------------------------------------

def _list(db, admin, **over):
    params = dict(context="auth", q=None, event_type=None, outcome=None,
                  since_id=0, before_id=None, limit=100)
    params.update(over)
    return list_events(_=admin, db=db, **params)


def test_list_is_newest_first_and_scoped_to_context(db, admin):
    _add(db, context="auth", event_type="login")
    _add(db, context="deploy", event_type="deploy.start")
    newest = _add(db, context="auth", event_type="logout", outcome="success")

    out = _list(db, admin)
    assert [e["id"] for e in out["events"]] == [newest.id, newest.id - 2]
    assert all(e["context"] == "auth" for e in out["events"])
    assert out["last_id"] == newest.id


def test_list_filters_event_type_outcome_and_search(db, admin):
    _add(db, event_type="login", outcome="failure", actor_email="alice@corp.com")
    _add(db, event_type="login", outcome="success", actor_email="bob@corp.com")
    _add(db, event_type="sso.callback", outcome="denied", actor_email="alice@corp.com")

    assert len(_list(db, admin, event_type="login")["events"]) == 2
    assert len(_list(db, admin, outcome="denied")["events"]) == 1
    # q matches email (case-insensitive) across all rows
    assert len(_list(db, admin, q="ALICE")["events"]) == 2
    assert len(_list(db, admin, q="alice", event_type="login")["events"]) == 1


def test_list_keyset_pagination(db, admin):
    ids = [_add(db).id for _ in range(5)]

    newer = _list(db, admin, since_id=ids[2])
    assert [e["id"] for e in newer["events"]] == [ids[4], ids[3]]

    older = _list(db, admin, before_id=ids[2], limit=2)
    assert [e["id"] for e in older["events"]] == [ids[1], ids[0]]
    assert older["has_more"] is True


def test_list_rejects_unknown_context(db, admin):
    with pytest.raises(HTTPException) as exc:
        _list(db, admin, context="nope")
    assert exc.value.status_code == 422


# ---- GET /api/logs/export --------------------------------------------------

def test_export_csv_and_json(db, admin):
    _add(db, actor_email="alice@corp.com", message="Invalid email or password",
         detail={"reason": "bad_password"})

    csv_resp = export(context="auth", q=None, event_type=None, outcome=None,
                      format="csv", limit=100, _=admin, db=db)
    assert "attachment" in csv_resp.headers["content-disposition"]
    text = csv_resp.body.decode()
    assert "alice@corp.com" in text
    assert text.splitlines()[0].startswith("id,ts,context,event_type,outcome")

    json_resp = export(context="auth", q=None, event_type=None, outcome=None,
                       format="json", limit=100, _=admin, db=db)
    payload = json.loads(json_resp.body)
    assert payload["events"][0]["detail"] == {"reason": "bad_password"}


def test_export_rejects_unknown_format(db, admin):
    with pytest.raises(HTTPException) as exc:
        export(context="auth", q=None, event_type=None, outcome=None,
               format="xml", limit=100, _=admin, db=db)
    assert exc.value.status_code == 422


# ---- audit -> logbook bridge -----------------------------------------------

def test_audit_record_bridges_into_log_events(db, admin):
    audit.record(db, admin, "server.create", "server", "acme", {"template": "basic"})
    db.flush()

    assert db.query(AuditEvent).count() == 1  # audit trail unchanged
    e = db.query(LogEvent).one()
    assert e.context == "deploy"  # target_type "server" maps to deploy
    assert e.event_type == "server.create"
    assert e.outcome == "success"
    assert e.actor_email == admin.email
    assert e.detail == {"template": "basic"}


def test_audit_bridge_context_mapping(db, admin):
    audit.record(db, admin, "user.update", "user", "u1")
    audit.record(db, admin, "backup.export", "backup", "b1")
    audit.record(db, admin, "something.new", "unknown_type", "x")
    db.flush()
    contexts = {e.event_type: e.context for e in db.query(LogEvent).all()}
    assert contexts["user.update"] == "admin"
    assert contexts["backup.export"] == "backup"
    assert contexts["something.new"] == "admin"  # unknown types default to admin


# ---- retention ---------------------------------------------------------------

def _aged(db, context, days_old):
    from datetime import datetime, timedelta, timezone

    e = _add(db, context=context)
    e.ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    db.flush()
    return e


def test_retention_defaults_and_override(db):
    assert log_retention.retention_days(db, "auth") == 90  # env default
    log_retention.set_retention_days(db, "auth", 30)
    assert log_retention.retention_days(db, "auth") == 30
    assert log_retention.is_custom(db, "auth") is True
    assert log_retention.is_custom(db, "deploy") is False


def test_prune_respects_per_context_windows(db):
    old_auth = _aged(db, "auth", days_old=40)
    fresh_auth = _aged(db, "auth", days_old=5)
    old_deploy = _aged(db, "deploy", days_old=40)

    log_retention.set_retention_days(db, "auth", 30)
    log_retention.set_retention_days(db, "deploy", 0)  # keep forever

    removed = log_retention.prune_log_events(db)
    assert removed == {"auth": 1}
    remaining = {e.id for e in db.query(LogEvent).all()}
    assert old_auth.id not in remaining
    assert {fresh_auth.id, old_deploy.id} <= remaining


def test_retention_endpoints(db, admin):
    _add(db, context="auth")
    out = get_retention(_=admin, db=db)
    assert out["default_days"] == 90
    by_ctx = {c["context"]: c for c in out["contexts"]}
    assert by_ctx["auth"]["count"] == 1
    assert by_ctx["auth"]["custom"] is False
    assert set(by_ctx) == set(logbook.ALL_CONTEXTS)

    out = put_retention(RetentionIn(context="auth", days=7), me=admin, db=db)
    assert {c["context"]: c for c in out["contexts"]}["auth"]["days"] == 7

    _aged(db, "auth", days_old=10)
    out = prune_now(me=admin, db=db)
    assert out["removed"] == {"auth": 1}


def test_put_retention_rejects_unknown_context(db, admin):
    with pytest.raises(HTTPException) as exc:
        put_retention(RetentionIn(context="nope", days=7), me=admin, db=db)
    assert exc.value.status_code == 422


# ---- event types -------------------------------------------------------------

def test_event_types_distinct_per_context(db, admin):
    _add(db, context="auth", event_type="login")
    _add(db, context="auth", event_type="login")
    _add(db, context="auth", event_type="sso.callback")
    _add(db, context="deploy", event_type="server.create")

    out = event_types(context="auth", _=admin, db=db)
    assert out["event_types"] == ["login", "sso.callback"]


# ---- login instrumentation -------------------------------------------------

def test_login_failure_and_success_are_recorded(db, monkeypatch):
    from app.auth import hash_password
    from app.routes.auth import LoginIn, login

    recorded: list[tuple] = []
    monkeypatch.setattr(
        "app.routes.auth.logbook.record",
        lambda *a, **kw: recorded.append((a, kw)),
    )

    user = User(email="u@corp.com", display_name="U", role="user",
                auth_source="local", password_hash=hash_password("secret-pw"))
    db.add(user)
    db.flush()

    req = FakeRequest()
    with pytest.raises(HTTPException):
        login(LoginIn(email="u@corp.com", password="wrong"), req, db=db)
    (args, kwargs) = recorded[-1]
    assert args[:3] == ("auth", "login", "failure")
    assert kwargs["detail"]["reason"] == "bad_password"

    with pytest.raises(HTTPException):
        login(LoginIn(email="ghost@corp.com", password="x"), req, db=db)
    assert recorded[-1][1]["detail"]["reason"] == "unknown_email"
    assert recorded[-1][1]["email"] == "ghost@corp.com"

    out = login(LoginIn(email="u@corp.com", password="secret-pw"), req, db=db)
    assert out["access_token"]
    assert recorded[-1][0][:3] == ("auth", "login", "success")
