"""Admin user-update route: role assignment + auth-source toggle (PATCH /users/{id}).

Calls the route handler directly with a session, mirroring test_role_mappings_builtin.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app.db import Base
from app.models import RoleMapping, User
from app.routes.users import UpdateUserIn, update
from app.services.claim_mapping import resolve_grants
from app.services.sso import sync_grants, upsert_sso_user


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


def _local(db, **over):
    over.setdefault("email", "u@corp.com")
    over.setdefault("role", "user")
    u = User(display_name="U", auth_source="local", password_hash="x", **over)
    db.add(u)
    db.flush()
    return u


def _entra(db, **over):
    over.setdefault("email", "sso@corp.com")
    over.setdefault("role", "user")
    over.setdefault("oidc_sub", "sub-1")
    u = User(display_name="S", auth_source="entra", **over)
    db.add(u)
    db.flush()
    return u


def test_assign_role_to_local_user(db, admin):
    u = _local(db)
    out = update(u.id, UpdateUserIn(role="superadmin"), me=admin, db=db)
    assert u.role == "superadmin"
    assert out["role"] == "superadmin"


def test_convert_entra_user_to_local_keeps_sso_link(db, admin):
    # Break-glass: drop to local + set a role, but KEEP oidc_sub so SSO resumes
    # seamlessly once the admin flips them back to "entra".
    u = _entra(db)
    assert u.oidc_sub == "sub-1"
    update(u.id, UpdateUserIn(auth_source="local", role="superadmin"), me=admin, db=db)
    assert u.auth_source == "local"
    assert u.oidc_sub == "sub-1"  # link preserved for a clean return to SSO
    assert u.role == "superadmin"


def test_flip_local_back_to_entra_preserves_sub(db, admin):
    # A user parked in break-glass local (sub retained) flips back to SSO cleanly.
    u = _local(db, oidc_sub="sub-9")
    update(u.id, UpdateUserIn(auth_source="entra"), me=admin, db=db)
    assert u.auth_source == "entra"
    assert u.oidc_sub == "sub-9"


def test_demote_last_superadmin_rejected(db):
    # `admin` fixture isn't used: this user is the ONLY superadmin.
    only = User(email="boss@corp.com", display_name="Boss", role="superadmin",
                auth_source="local", password_hash="x")
    db.add(only)
    db.flush()
    with pytest.raises(HTTPException) as e:
        update(only.id, UpdateUserIn(role="user"), me=only, db=db)
    assert e.value.status_code == 400
    assert only.role == "superadmin"  # unchanged


def test_demote_superadmin_allowed_when_another_exists(db, admin):
    other = User(email="co@corp.com", display_name="Co", role="superadmin",
                 auth_source="local", password_hash="x")
    db.add(other)
    db.flush()
    update(other.id, UpdateUserIn(role="user"), me=admin, db=db)
    assert other.role == "user"


def test_convert_last_superadmin_to_local_keeps_role(db):
    # auth_source change alone never orphans: role is untouched.
    boss = _entra(db, email="boss@corp.com", role="superadmin")
    update(boss.id, UpdateUserIn(auth_source="local"), me=boss, db=db)
    assert boss.auth_source == "local"
    assert boss.role == "superadmin"


def test_partial_update_leaves_other_fields(db, admin):
    u = _entra(db)
    update(u.id, UpdateUserIn(role="superadmin"), me=admin, db=db)
    assert u.role == "superadmin"
    assert u.auth_source == "entra"  # untouched
    assert u.oidc_sub == "sub-1"  # untouched


def test_breakglass_self_heals_on_entra_login(db, admin):
    # The far end of the break-glass round-trip, with NO admin flip: a user parked
    # in local break-glass (oidc_sub retained, manually elevated) signs in via
    # Entra again once it's healthy. upsert matches by oidc_sub, auto-promotes them
    # back to "entra", and sync re-governs the role from claims — dropping the
    # manual elevation. `admin` is a second superadmin so the demotion isn't floored.
    u = _local(db, email="user@corp.com", oidc_sub="sub-rt", role="superadmin")
    assert u.auth_source == "local"
    db.add(RoleMapping(id=1, entra_app_role="R.User", roundhouse_role="user",
                       team_id=None, team_role="member"))
    db.flush()
    claims = {"sub": "sub-rt", "email": "user@corp.com", "name": "User", "roles": ["R.User"]}
    matched = upsert_sso_user(db, claims)
    assert matched.id == u.id  # same account, matched by retained oidc_sub
    assert matched.auth_source == "entra"  # self-healed back to SSO, no admin flip
    sync_grants(db, matched, resolve_grants(db, claims))
    assert matched.role == "user"  # Entra is source of truth again


def test_unknown_user_404(db, admin):
    with pytest.raises(HTTPException) as e:
        update("00000000-0000-0000-0000-000000000000", UpdateUserIn(role="user"),
               me=admin, db=db)
    assert e.value.status_code == 404
