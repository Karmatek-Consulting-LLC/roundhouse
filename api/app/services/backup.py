"""Full-platform backup & restore.

Every piece of authoritative deployment state now lives in Postgres — users,
teams, ownership, server_tokens (AES-encrypted with APP_KEY), scopes, platform
settings, audit log, and (since the specs/assets→Postgres migration) the server
specs, uploaded assets, and cloned-repo/template build files. So a backup is a
`pg_dump` of the database, and a restore is a `pg_restore` followed by an
orchestrator reconcile.

The reconcile is the part `pg_restore` alone can't do: the database is *desired*
state, but the running MCP server containers are *actual* state living in
Docker/Swarm/K8s. Restoring last night's DB re-adds rows for servers deleted
since, drops rows for servers created since, and reverts edited specs — none of
which touches the running workloads. So after loading the dump we redeploy every
server the restored DB knows about and reap any running workload it doesn't.

Archive layout — a gzip-compressed tar:

    manifest.json     # readable metadata (schema rev, APP_KEY fingerprint, ...)
    database.dump     # `pg_dump -Fc` custom-format archive

Restore validates the manifest against the live deployment before touching
anything:
  * APP_KEY fingerprint must match — else the encrypted server_tokens in the
    dump are undecryptable and every token would be silently dead.
  * Alembic revision must match the running code's head — restoring a schema the
    code doesn't expect would break the app.
Both checks can be overridden with force=True for deliberate recovery.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import get_settings
from app.crypto import DecryptError, key_fingerprint
from app.db import _ALEMBIC_INI, _engine, db_session

logger = logging.getLogger(__name__)

# Bump when the archive layout or manifest contract changes incompatibly.
BACKUP_FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
DUMP_NAME = "database.dump"


class BackupError(RuntimeError):
    """Any backup/restore failure. Routes map this to an HTTP 4xx."""


# --------------------------------------------------------------------------- #
# Environment / identity helpers
# --------------------------------------------------------------------------- #

def is_postgres() -> bool:
    """Backup/restore shell out to pg_dump/pg_restore, so they only work against
    Postgres (the SQLite path exists for local-only dev + tests)."""
    return _engine.dialect.name == "postgresql"


def app_key_fingerprint() -> str | None:
    """Fingerprint of the configured APP_KEY, or None when no key is set."""
    try:
        return key_fingerprint(get_settings().app_key)
    except DecryptError:
        return None


def code_head_revision() -> str | None:
    """The Alembic head the running code ships (target of `upgrade head`)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI)))
    return script.get_current_head()


def db_revision() -> str | None:
    """The Alembic revision the live database is currently stamped at."""
    from alembic.migration import MigrationContext

    with _engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _summary_counts() -> dict:
    """A few row counts for the manifest, so an operator can eyeball what a
    backup holds (and what a restore would replace) without restoring it."""
    from app.models import Server, ServerToken, User

    with db_session() as s:
        return {
            "servers": s.query(Server).count(),
            "users": s.query(User).count(),
            "server_tokens": s.query(ServerToken).count(),
        }


def _pg_conn_args() -> tuple[dict[str, str], list[str]]:
    """(env, [connection flags]) for pg_dump / pg_restore. Password rides in the
    environment via PGPASSWORD so it never lands in argv / process listings."""
    cfg = get_settings()
    env = dict(os.environ)
    env["PGPASSWORD"] = cfg.db_password
    flags = [
        "-h", cfg.db_host,
        "-p", str(cfg.db_port),
        "-U", cfg.db_username,
        "-d", cfg.db_database,
    ]
    return env, flags


# --------------------------------------------------------------------------- #
# Manifest + archive packing
# --------------------------------------------------------------------------- #

def deployment_info() -> dict:
    """What the UI shows before an export, and what a restore is validated
    against. Cheap enough to compute on demand."""
    cfg = get_settings()
    return {
        "postgres": is_postgres(),
        "alembic_revision": db_revision(),
        "app_key_fingerprint": app_key_fingerprint(),
        "base_url": cfg.mcp_base_url,
        "orchestrator": cfg.mcp_orchestrator,
        "counts": _summary_counts(),
    }


def _build_manifest(created_at: datetime) -> dict:
    cfg = get_settings()
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": created_at.isoformat(),
        "alembic_revision": db_revision(),
        "app_key_fingerprint": app_key_fingerprint(),
        "base_url": cfg.mcp_base_url,
        "orchestrator": cfg.mcp_orchestrator,
        "pg_dump_format": "custom",
        "counts": _summary_counts(),
    }


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0  # deterministic; the real timestamp lives in the manifest
    tar.addfile(info, io.BytesIO(data))


def pack_archive(manifest: dict, dump: bytes) -> bytes:
    """Bundle a manifest + dump into the .tar.gz wire format. Pure/testable —
    no DB or pg_dump involved."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_bytes(tar, MANIFEST_NAME, json.dumps(manifest, indent=2).encode("utf-8"))
        _add_bytes(tar, DUMP_NAME, dump)
    return buf.getvalue()


def read_archive(blob: bytes) -> tuple[dict, bytes]:
    """Inverse of pack_archive: pull the manifest + dump back out, or raise
    BackupError if the upload isn't a Roundhouse backup."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            manifest_raw = _read_member(tar, MANIFEST_NAME)
            dump = _read_member(tar, DUMP_NAME)
        manifest = json.loads(manifest_raw)
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError) as e:
        raise BackupError(f"Not a valid Roundhouse backup archive: {e}") from e
    if not isinstance(manifest, dict):
        raise BackupError("Backup manifest is malformed.")
    return manifest, dump


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.extractfile(name)
    if member is None:
        raise KeyError(name)
    return member.read()


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _pg_dump_bytes() -> bytes:
    env, flags = _pg_conn_args()
    cmd = ["pg_dump", *flags, "-Fc", "--no-owner", "--no-privileges"]
    proc = subprocess.run(cmd, env=env, capture_output=True)
    if proc.returncode != 0:
        raise BackupError(
            "pg_dump failed: " + (proc.stderr.decode(errors="replace").strip() or "unknown error")
        )
    return proc.stdout


def create_backup() -> tuple[bytes, str]:
    """Produce (archive_bytes, suggested_filename) for the whole deployment."""
    if not is_postgres():
        raise BackupError("Backup is only supported on the Postgres backend.")
    created_at = datetime.now(timezone.utc)
    manifest = _build_manifest(created_at)
    dump = _pg_dump_bytes()
    archive = pack_archive(manifest, dump)
    filename = f"roundhouse-backup-{created_at.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    logger.info(
        "Created backup %s (%d bytes, rev=%s, servers=%s)",
        filename, len(archive), manifest.get("alembic_revision"),
        manifest.get("counts", {}).get("servers"),
    )
    return archive, filename


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #

def validate_manifest(manifest: dict) -> list[str]:
    """Return a list of blocking problems; empty means safe to restore.

    These are the silent-data-loss traps: a schema the code can't run, or an
    APP_KEY that can't decrypt the restored tokens."""
    problems: list[str] = []

    fmt = manifest.get("format_version")
    if fmt != BACKUP_FORMAT_VERSION:
        problems.append(
            f"Unsupported backup format version {fmt!r} (this build expects "
            f"{BACKUP_FORMAT_VERSION})."
        )

    want_rev = code_head_revision()
    got_rev = manifest.get("alembic_revision")
    if got_rev != want_rev:
        problems.append(
            f"Schema mismatch: backup is at Alembic revision {got_rev!r}, but this "
            f"deployment runs {want_rev!r}. Restore onto a Roundhouse build at the "
            "same version."
        )

    want_fp = app_key_fingerprint()
    got_fp = manifest.get("app_key_fingerprint")
    if got_fp and got_fp != want_fp:
        problems.append(
            "APP_KEY mismatch: this deployment's key differs from the one that "
            "encrypted the backup. Restoring would leave every server token "
            "undecryptable. Restore onto a deployment using the same APP_KEY."
        )

    return problems


def restore_preview(blob: bytes) -> dict:
    """Validate an uploaded backup without applying it. Surfaces the manifest,
    any blocking problems, and current-vs-backup counts for a sanity check."""
    manifest, _dump = read_archive(blob)
    return {
        "manifest": manifest,
        "problems": validate_manifest(manifest),
        "current": deployment_info(),
    }


def _terminate_other_connections() -> None:
    """Drop every other backend on this database so pg_restore's --clean DROPs
    aren't blocked by the sibling uvicorn worker or in-flight requests holding
    AccessShareLocks. Our own connection is excluded; the workers reconnect."""
    if not is_postgres():
        return
    with _engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )


def _pg_restore(dump: bytes) -> None:
    """Load a custom-format dump in place. --single-transaction makes it atomic:
    if anything fails the current database is left exactly as it was, so a bad
    restore never half-replaces live data. --clean --if-exists drops the
    existing objects first so it's a true replace, not a merge."""
    env, flags = _pg_conn_args()
    _terminate_other_connections()
    with tempfile.NamedTemporaryFile(suffix=".dump") as f:
        f.write(dump)
        f.flush()
        cmd = [
            "pg_restore", *flags,
            "--clean", "--if-exists",
            "--no-owner", "--no-privileges",
            "--single-transaction",
            f.name,
        ]
        proc = subprocess.run(cmd, env=env, capture_output=True)
    if proc.returncode != 0:
        raise BackupError(
            "pg_restore failed (database left unchanged): "
            + (proc.stderr.decode(errors="replace").strip() or "unknown error")
        )


def _reconcile_plan(desired: set[str], actual: set[str]) -> tuple[list[str], list[str]]:
    """Pure planner: given server names the restored DB wants and names actually
    running, return (to_reap, to_redeploy). Every desired server is redeployed so
    its running image matches the restored spec (covers down/stale/reverted
    alike); every running workload with no spec is reaped."""
    to_reap = sorted(actual - desired)
    to_redeploy = sorted(desired)
    return to_reap, to_redeploy


def reconcile_orchestrator() -> dict:
    """Make the live workloads match the restored database."""
    from app.services.server_service import get_server_service

    svc = get_server_service()
    orch = svc.docker  # the configured Orchestrator (docker/swarm/k8s)

    desired_specs = svc.store.list_all()
    desired = {s.name for s in desired_specs}
    try:
        actual = {w.get("name") for w in orch.list_servers() if w.get("name")}
    except Exception as e:  # noqa: BLE001 - surface a clear reconcile failure
        raise BackupError(f"Could not list running workloads for reconcile: {e}") from e

    to_reap, _ = _reconcile_plan(desired, actual)
    summary: dict = {"redeployed": [], "reaped": [], "errors": []}

    # Reap orphans first so freed names/ports don't collide with redeploys.
    for name in to_reap:
        try:
            orch.remove_server(name)
            summary["reaped"].append(name)
        except Exception as e:  # noqa: BLE001 - one failure must not abort the rest
            logger.exception("Reconcile: failed to reap orphan %r", name)
            summary["errors"].append({"server": name, "op": "reap", "error": str(e)})

    for spec in desired_specs:
        try:
            with db_session() as db:
                svc.redeploy(db, spec)
            summary["redeployed"].append(spec.name)
        except Exception as e:  # noqa: BLE001
            logger.exception("Reconcile: failed to redeploy %r", spec.name)
            summary["errors"].append({"server": spec.name, "op": "redeploy", "error": str(e)})

    logger.info(
        "Reconcile complete: redeployed=%d reaped=%d errors=%d",
        len(summary["redeployed"]), len(summary["reaped"]), len(summary["errors"]),
    )
    return summary


def restore_backup(blob: bytes, *, force: bool = False) -> dict:
    """Validate, load the dump in place, then reconcile the live workloads."""
    if not is_postgres():
        raise BackupError("Restore is only supported on the Postgres backend.")

    manifest, dump = read_archive(blob)
    problems = validate_manifest(manifest)
    if problems and not force:
        raise BackupError("Backup failed validation: " + " ".join(problems))

    logger.info(
        "Restoring backup (rev=%s, created=%s, force=%s)",
        manifest.get("alembic_revision"), manifest.get("created_at"), force,
    )
    _pg_restore(dump)
    # The pooled connections point at the pre-restore catalog; drop them so
    # subsequent sessions reconnect cleanly to the restored database.
    _engine.dispose()

    reconcile = reconcile_orchestrator()
    return {"manifest": manifest, "problems": problems, "forced": force, "reconcile": reconcile}
