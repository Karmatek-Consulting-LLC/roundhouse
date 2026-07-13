"""Built-in role-mapping reconcile logic (PUT /api/role-mappings/builtin)."""
from __future__ import annotations

import itertools

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables
from app.db import Base
from app.models import RoleMapping, Team, User
from app.routes.role_mappings import BuiltinMappingsIn, update_builtin

_ids = itertools.count(1)


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


def _builtin(db):
    return {
        m.entra_app_role: m.roundhouse_role
        for m in db.query(RoleMapping).filter(RoleMapping.team_id.is_(None)).all()
    }


def test_reconcile_creates_updates_deletes(db, admin):
    update_builtin(BuiltinMappingsIn(superadmin=["R.Admins"], user=["R.Users"]), me=admin, db=db)
    assert _builtin(db) == {"R.Admins": "superadmin", "R.Users": "user"}

    # Move R.Users up to superadmin, drop nothing else; R.Admins stays.
    update_builtin(BuiltinMappingsIn(superadmin=["R.Admins", "R.Users"], user=[]), me=admin, db=db)
    assert _builtin(db) == {"R.Admins": "superadmin", "R.Users": "superadmin"}

    # Empty lists clear all built-in rows.
    update_builtin(BuiltinMappingsIn(superadmin=[], user=[]), me=admin, db=db)
    assert _builtin(db) == {}


def test_dedupes_and_trims(db, admin):
    update_builtin(BuiltinMappingsIn(superadmin=[" R.A ", "R.A", ""], user=[]), me=admin, db=db)
    assert _builtin(db) == {"R.A": "superadmin"}


def test_same_role_in_both_lists_rejected(db, admin):
    with pytest.raises(HTTPException) as e:
        update_builtin(BuiltinMappingsIn(superadmin=["R.Dup"], user=["R.Dup"]), me=admin, db=db)
    assert e.value.status_code == 422


def test_collision_with_team_mapping_rejected(db, admin):
    team = Team(name="t")
    db.add(team)
    db.flush()
    db.add(RoleMapping(id=next(_ids), entra_app_role="R.Team", roundhouse_role="user",
                       team_id=team.id, team_role="member"))
    db.flush()
    with pytest.raises(HTTPException) as e:
        update_builtin(BuiltinMappingsIn(superadmin=["R.Team"], user=[]), me=admin, db=db)
    assert e.value.status_code == 409


def test_team_mappings_untouched(db, admin):
    team = Team(name="t")
    db.add(team)
    db.flush()
    db.add(RoleMapping(id=next(_ids), entra_app_role="R.Team", roundhouse_role="user",
                       team_id=team.id, team_role="admin"))
    db.flush()
    update_builtin(BuiltinMappingsIn(superadmin=["R.Admins"], user=[]), me=admin, db=db)
    # Team row survives the built-in reconcile.
    survivors = {m.entra_app_role for m in db.query(RoleMapping).all()}
    assert survivors == {"R.Team", "R.Admins"}
