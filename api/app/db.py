from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = create_engine(get_settings().db_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(_engine, autoflush=False, autocommit=False, future=True)

# Stable 64-bit advisory-lock keys (retention uses 0x726F756E6431 = "round1").
# Each uvicorn worker runs the startup lifespan, so migrations + the one-time
# spec import must be single-flighted across workers.
MIGRATION_LOCK_KEY = 0x726F756E6432  # "round2"
IMPORT_LOCK_KEY = 0x726F756E6433  # "round3"

# alembic.ini sits next to the `app/` package: api/alembic.ini in dev,
# /app/alembic.ini in the container.
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Commits on success, rolls back on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Outside a request, e.g. startup tasks / background SIGTERM handlers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))

    insp = inspect(_engine)
    tables = set(insp.get_table_names())
    has_app_tables = "users" in tables  # any app table will do; users is always present
    has_alembic = "alembic_version" in tables

    if has_app_tables and not has_alembic:
        logger.info("Existing pre-alembic DB detected; stamping at baseline.")
        command.stamp(cfg, "head")

    command.upgrade(cfg, "head")


def init_db() -> None:
    """Bring the DB up to head via Alembic.

    Handles three cases:
      1. Fresh empty DB -> alembic upgrade head runs every migration.
      2. Existing DB managed by alembic -> upgrade head applies pending revs.
      3. Existing DB pre-dating alembic (has tables but no alembic_version) ->
         stamp head once to mark it at baseline, then upgrade is a no-op.

    Each uvicorn worker runs this in its own lifespan. On Postgres we serialize
    with a *blocking* session-level advisory lock so the workers don't run
    `alembic upgrade` concurrently — without it, two workers race on CREATE
    TABLE and one crashes with a duplicate-pg_type error. The first worker
    migrates; the rest wait, then upgrade is a no-op.
    """
    if _engine.dialect.name != "postgresql":
        _run_migrations()  # sqlite/tests: single process, no contention
        return

    with _engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        try:
            _run_migrations()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
