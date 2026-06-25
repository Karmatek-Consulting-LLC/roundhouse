"""Unit tests for the backup/restore service.

The suite runs on SQLite (see conftest), so the pg_dump/pg_restore paths are
exercised only at their guards; everything testable without a live Postgres —
archive packing, manifest validation, the reconcile planner, key fingerprints —
is covered directly.
"""
from __future__ import annotations

import json

import pytest

from app.crypto import key_fingerprint
from app.services import backup as bk


# --------------------------------------------------------------------------- #
# Key fingerprint
# --------------------------------------------------------------------------- #

def test_key_fingerprint_is_stable_and_key_dependent():
    import base64

    k1 = "base64:" + base64.b64encode(b"\x01" * 32).decode()
    k2 = "base64:" + base64.b64encode(b"\x02" * 32).decode()
    assert key_fingerprint(k1) == key_fingerprint(k1)
    assert key_fingerprint(k1) != key_fingerprint(k2)
    # Short, hex, non-reversible.
    fp = key_fingerprint(k1)
    assert len(fp) == 16
    int(fp, 16)  # hex-decodable


def test_app_key_fingerprint_none_when_unset(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_key", "", raising=False)
    assert bk.app_key_fingerprint() is None


# --------------------------------------------------------------------------- #
# Archive round-trip
# --------------------------------------------------------------------------- #

def test_pack_and_read_archive_round_trip():
    manifest = {"format_version": bk.BACKUP_FORMAT_VERSION, "hello": "world"}
    dump = b"\x00\x01PGDMP fake custom dump \xff"
    blob = bk.pack_archive(manifest, dump)

    got_manifest, got_dump = bk.read_archive(blob)
    assert got_manifest == manifest
    assert got_dump == dump


def test_read_archive_rejects_garbage():
    with pytest.raises(bk.BackupError):
        bk.read_archive(b"this is not a tar.gz")


def test_read_archive_rejects_missing_members():
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps({"format_version": 1}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    # No database.dump member.
    with pytest.raises(bk.BackupError):
        bk.read_archive(buf.getvalue())


# --------------------------------------------------------------------------- #
# Manifest validation
# --------------------------------------------------------------------------- #

def _manifest(**over):
    base = {
        "format_version": bk.BACKUP_FORMAT_VERSION,
        "alembic_revision": "rev_head",
        "app_key_fingerprint": "deadbeefdeadbeef",
    }
    base.update(over)
    return base


def test_validate_clean_when_everything_matches(monkeypatch):
    monkeypatch.setattr(bk, "code_head_revision", lambda: "rev_head")
    monkeypatch.setattr(bk, "app_key_fingerprint", lambda: "deadbeefdeadbeef")
    assert bk.validate_manifest(_manifest()) == []


def test_validate_flags_schema_mismatch(monkeypatch):
    monkeypatch.setattr(bk, "code_head_revision", lambda: "rev_newer")
    monkeypatch.setattr(bk, "app_key_fingerprint", lambda: "deadbeefdeadbeef")
    problems = bk.validate_manifest(_manifest())
    assert any("Schema mismatch" in p for p in problems)


def test_validate_flags_app_key_mismatch(monkeypatch):
    monkeypatch.setattr(bk, "code_head_revision", lambda: "rev_head")
    monkeypatch.setattr(bk, "app_key_fingerprint", lambda: "0000000000000000")
    problems = bk.validate_manifest(_manifest())
    assert any("APP_KEY mismatch" in p for p in problems)


def test_validate_flags_format_version(monkeypatch):
    monkeypatch.setattr(bk, "code_head_revision", lambda: "rev_head")
    monkeypatch.setattr(bk, "app_key_fingerprint", lambda: "deadbeefdeadbeef")
    problems = bk.validate_manifest(_manifest(format_version=999))
    assert any("format version" in p for p in problems)


def test_validate_allows_backup_without_fingerprint(monkeypatch):
    # A backup taken when no APP_KEY was set carries no fingerprint; that must
    # not block a restore (there were no encrypted tokens to lose).
    monkeypatch.setattr(bk, "code_head_revision", lambda: "rev_head")
    monkeypatch.setattr(bk, "app_key_fingerprint", lambda: "deadbeefdeadbeef")
    assert bk.validate_manifest(_manifest(app_key_fingerprint=None)) == []


# --------------------------------------------------------------------------- #
# Reconcile planner
# --------------------------------------------------------------------------- #

def test_reconcile_plan_reaps_orphans_and_redeploys_all_desired():
    desired = {"alpha", "beta", "gamma"}
    actual = {"beta", "gamma", "orphan1", "orphan2"}
    to_reap, to_redeploy = bk._reconcile_plan(desired, actual)
    assert to_reap == ["orphan1", "orphan2"]            # running but not in DB
    assert to_redeploy == ["alpha", "beta", "gamma"]    # every desired server


def test_reconcile_plan_nothing_running():
    to_reap, to_redeploy = bk._reconcile_plan({"a", "b"}, set())
    assert to_reap == []
    assert to_redeploy == ["a", "b"]


def test_reconcile_plan_empty_desired_reaps_everything():
    to_reap, to_redeploy = bk._reconcile_plan(set(), {"x", "y"})
    assert to_reap == ["x", "y"]
    assert to_redeploy == []


# --------------------------------------------------------------------------- #
# Backend guards (SQLite test backend is not Postgres)
# --------------------------------------------------------------------------- #

def test_create_backup_requires_postgres():
    assert bk.is_postgres() is False
    with pytest.raises(bk.BackupError, match="Postgres"):
        bk.create_backup()


def test_restore_requires_postgres():
    blob = bk.pack_archive(_manifest(), b"dump")
    with pytest.raises(bk.BackupError, match="Postgres"):
        bk.restore_backup(blob)
