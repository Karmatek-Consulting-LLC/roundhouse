"""Per-context retention for the Logs console (log_events).

Each context keeps its own window, stored in platform_settings as
"log_retention.<context>" (days; "0" = keep forever), editable from the Logs
UI. Unset contexts fall back to the RH_LOG_RETENTION_DAYS env var (default
90). The prune runs hourly inside the request_events retention loop and on
demand via POST /api/logs/retention/prune.

Cutoffs are computed in Python (not SQL intervals) so the same code runs on
Postgres in production and sqlite in tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import logbook
from app.models import LogEvent
from app.platform_settings import SETTING_LOG_RETENTION_PREFIX, get_setting, put_setting

DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 3650  # 10 years; keeps the setting sane


def default_retention_days() -> int:
    try:
        return int(os.environ.get("RH_LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def retention_days(db: Session, context: str) -> int:
    """Effective window for one context. 0 (or negative) = keep forever."""
    raw = get_setting(db, SETTING_LOG_RETENTION_PREFIX + context)
    if raw is None:
        return default_retention_days()
    try:
        return int(raw)
    except ValueError:
        return default_retention_days()


def is_custom(db: Session, context: str) -> bool:
    return get_setting(db, SETTING_LOG_RETENTION_PREFIX + context) is not None


def set_retention_days(db: Session, context: str, days: int) -> None:
    put_setting(db, SETTING_LOG_RETENTION_PREFIX + context, str(days))
    # Sessions run autoflush=False; flush so a read-back in the same request
    # (the retention endpoint returns fresh stats) sees a first-time key.
    db.flush()


def stats(db: Session) -> list[dict]:
    """Per-context storage snapshot for the retention UI: row count, oldest
    event, effective window, and whether it's customized or the default."""
    rows = dict(
        db.query(LogEvent.context, func.count(LogEvent.id))
        .group_by(LogEvent.context)
        .all()
    )
    oldest = dict(
        db.query(LogEvent.context, func.min(LogEvent.ts))
        .group_by(LogEvent.context)
        .all()
    )
    out = []
    for context in logbook.ALL_CONTEXTS:
        oldest_ts = oldest.get(context)
        out.append({
            "context": context,
            "days": retention_days(db, context),
            "custom": is_custom(db, context),
            "count": int(rows.get(context, 0)),
            "oldest_ts": oldest_ts.isoformat() if oldest_ts else None,
        })
    return out


def prune_log_events(db: Session) -> dict[str, int]:
    """Delete rows older than each context's window. Returns per-context
    removal counts (only contexts where something was deleted). Idempotent —
    safe to run from any worker or on demand."""
    removed: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    for context in logbook.ALL_CONTEXTS:
        days = retention_days(db, context)
        if days <= 0:
            continue
        cutoff = now - timedelta(days=days)
        n = (
            db.query(LogEvent)
            .filter(LogEvent.context == context, LogEvent.ts < cutoff)
            .delete(synchronize_session=False)
        )
        if n:
            removed[context] = n
    return removed
