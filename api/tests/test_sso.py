"""Claim->grant mapping + SSO provisioning/sync invariants.

Uses an in-memory SQLite DB built from the ORM metadata, so these exercise the
real query paths in claim_mapping / sso without a Postgres.
"""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app.db import Base
from app.models import RoleMapping, Team, TeamMembership, User
from app.services.claim_mapping import AccessDenied, resolve_grants
from app.services.sso import SsoError, sync_grants, upsert_sso_user


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


# SQLite doesn't auto-assign BIGINT primary keys (Postgres does), so hand the
# mapping rows explicit ids in tests.
_ids = itertools.count(1)


def _mapping(db, app_role, role, team_id=None, team_role="member"):
    m = RoleMapping(
        id=next(_ids),
        entra_app_role=app_role,
        roundhouse_role=role,
        team_id=team_id,
        team_role=team_role,
    )
    db.add(m)
    db.flush()
    return m


# ---------- claim -> grant ----------

def test_no_matching_mapping_denies_access(db):
    # Access is mapping-gated: claims matching no row are turned away, NOT
    # provisioned at a default "user" role.
    with pytest.raises(AccessDenied):
        resolve_grants(db, {"roles": ["SomethingUnmapped"]})


def test_empty_roles_claim_denies_access(db):
    _mapping(db, "Roundhouse.Admin", "superadmin")
    with pytest.raises(AccessDenied):
        resolve_grants(db, {"roles": []})
    with pytest.raises(AccessDenied):
        resolve_grants(db, {})


def test_highest_role_wins(db):
    _mapping(db, "Roundhouse.User", "user")
    _mapping(db, "Roundhouse.Admin", "superadmin")
    grants = resolve_grants(db, {"roles": ["Roundhouse.User", "Roundhouse.Admin"]})
    assert grants.role == "superadmin"


def test_team_grant_collected(db):
    team = Team(name="platform")
    db.add(team)
    db.flush()
    _mapping(db, "Roundhouse.Platform", "user", team_id=team.id, team_role="admin")
    grants = resolve_grants(db, {"roles": ["Roundhouse.Platform"]})
    assert grants.role == "user"
    assert len(grants.teams) == 1
    assert grants.teams[0].team_id == team.id
    assert grants.teams[0].team_role == "admin"


def test_roles_claim_as_bare_string(db):
    _mapping(db, "Roundhouse.Admin", "superadmin")
    grants = resolve_grants(db, {"roles": "Roundhouse.Admin"})
    assert grants.role == "superadmin"


# ---------- JIT upsert ----------

def test_jit_creates_entra_user(db):
    claims = {"sub": "abc-123", "email": "Jane@Corp.com", "name": "Jane"}
    user = upsert_sso_user(db, claims)
    assert user.auth_source == "entra"
    assert user.oidc_sub == "abc-123"
    assert user.email == "jane@corp.com"  # normalized
    assert user.password_hash is None


def test_match_by_sub_updates_profile(db):
    db.add(User(email="old@corp.com", display_name="Old", role="user",
                auth_source="entra", oidc_sub="s1"))
    db.flush()
    user = upsert_sso_user(db, {"sub": "s1", "email": "new@corp.com", "name": "New"})
    assert user.email == "new@corp.com"
    assert user.display_name == "New"


def test_email_collision_with_local_user_refused(db):
    db.add(User(email="dup@corp.com", password_hash="x", display_name="Local",
                role="user", auth_source="local"))
    db.flush()
    with pytest.raises(SsoError):
        upsert_sso_user(db, {"sub": "s2", "email": "dup@corp.com", "name": "X"})


def test_link_local_account_when_enabled(db):
    local = User(email="dup@corp.com", password_hash="keep-me", display_name="Local",
                 role="superadmin", auth_source="local")
    db.add(local)
    db.flush()
    local_id = local.id

    user = upsert_sso_user(
        db, {"sub": "s2", "email": "dup@corp.com", "name": "Now Entra"},
        link_local_by_email=True,
    )
    # Same record adopted (id/teams/ownership preserved), now Entra-authenticated.
    assert user.id == local_id
    assert user.auth_source == "entra"
    assert user.oidc_sub == "s2"
    assert user.role == "superadmin"  # not provisioned fresh as "user"
    assert user.password_hash == "keep-me"  # kept as break-glass fallback


def test_link_disabled_by_default_still_refuses(db):
    db.add(User(email="dup@corp.com", password_hash="x", display_name="Local",
                role="user", auth_source="local"))
    db.flush()
    with pytest.raises(SsoError):
        upsert_sso_user(db, {"sub": "s2", "email": "dup@corp.com", "name": "X"},
                        link_local_by_email=False)


def test_missing_email_refused(db):
    with pytest.raises(SsoError):
        upsert_sso_user(db, {"sub": "s3", "name": "No Email"})


# ---------- sync guardrails ----------

def test_local_user_exempt_from_sync(db):
    _mapping(db, "R.User", "user")
    user = User(email="admin@mcp.local", password_hash="x", display_name="Admin",
                role="superadmin", auth_source="local")
    db.add(user)
    db.flush()
    sync_grants(db, user, resolve_grants(db, {"roles": ["R.User"]}))
    assert user.role == "superadmin"  # untouched


def test_last_superadmin_not_demoted(db):
    _mapping(db, "R.User", "user")
    user = User(email="boss@corp.com", display_name="Boss", role="superadmin",
                auth_source="entra", oidc_sub="s4")
    db.add(user)
    db.flush()
    # Grants would set role=user, but this is the only superadmin.
    sync_grants(db, user, resolve_grants(db, {"roles": ["R.User"]}))
    assert user.role == "superadmin"


def test_superadmin_demoted_when_another_exists(db):
    _mapping(db, "R.User", "user")
    db.add(User(email="other@corp.com", display_name="Other", role="superadmin",
                auth_source="local", password_hash="x"))
    user = User(email="demote@corp.com", display_name="D", role="superadmin",
                auth_source="entra", oidc_sub="s5")
    db.add(user)
    db.flush()
    sync_grants(db, user, resolve_grants(db, {"roles": ["R.User"]}))
    assert user.role == "user"


def test_team_memberships_made_authoritative(db):
    keep = Team(name="keep")
    drop = Team(name="drop")
    add = Team(name="add")
    db.add_all([keep, drop, add])
    db.flush()
    user = User(email="u@corp.com", display_name="U", role="user",
                auth_source="entra", oidc_sub="s6")
    db.add(user)
    db.flush()
    db.add_all([
        TeamMembership(user_id=user.id, team_id=keep.id, role="member"),
        TeamMembership(user_id=user.id, team_id=drop.id, role="member"),
    ])
    db.flush()
    _mapping(db, "R.Keep", "user", team_id=keep.id, team_role="admin")
    _mapping(db, "R.Add", "user", team_id=add.id, team_role="member")

    sync_grants(db, user, resolve_grants(db, {"roles": ["R.Keep", "R.Add"]}))
    db.flush()

    teams = {m.team_id: m.role for m in db.query(TeamMembership)
             .filter(TeamMembership.user_id == user.id).all()}
    assert teams == {keep.id: "admin", add.id: "member"}  # drop removed, role updated
