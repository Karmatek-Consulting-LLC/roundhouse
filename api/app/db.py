from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = create_engine(get_settings().db_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(_engine, autoflush=False, autocommit=False, future=True)


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


def init_db() -> None:
    """Create any missing tables. Idempotent — if Laravel already migrated this
    DB, only newly added SQLAlchemy tables show up. For brand-new databases,
    every Laravel-equivalent table is created."""
    # Import models so SQLAlchemy registers them on Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(_engine)
