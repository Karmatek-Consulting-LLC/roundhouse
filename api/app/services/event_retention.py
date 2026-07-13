"""Retention prune for request_events and log_events.

The console keeps a rolling window of call history; request_events rows older
than RH_EVENT_RETENTION_DAYS (default 14) are deleted. log_events (the admin
Logs console) prunes per-context via app.services.log_retention, whose
windows are UI-editable. The loop runs in BOTH uvicorn workers, so the actual
DELETE is single-flighted with a Postgres session-level advisory lock —
whichever worker grabs it prunes, the other no-ops. The DELETE is idempotent
regardless, so the lock is an optimization, not a correctness requirement.
"""
from __future__ import annotations

import asyncio
import logging
import os

import anyio
from sqlalchemy import text

from app.db import db_session

logger = logging.getLogger(__name__)


def _retention_days() -> int:
    try:
        return max(0, int(os.environ.get("RH_EVENT_RETENTION_DAYS", "14")))
    except ValueError:
        return 14


PRUNE_INTERVAL_S = 3600
# Arbitrary stable 64-bit key so both workers contend for the same lock.
PRUNE_LOCK_KEY = 0x726F756E6431  # "round1"


def _prune_once() -> int:
    """Delete expired rows. Returns the number of rows removed (0 if another
    worker holds the lock or nothing was expired)."""
    days = _retention_days()
    with db_session() as db:
        got = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": PRUNE_LOCK_KEY}
        ).scalar()
        if not got:
            return 0
        try:
            result = db.execute(
                text(
                    "DELETE FROM request_events "
                    "WHERE ts < now() - make_interval(days => :d)"
                ),
                {"d": days},
            )
            removed = result.rowcount or 0

            from app import logbook
            from app.services.log_retention import prune_log_events

            log_removed = prune_log_events(db)
            if log_removed:
                logbook.record(
                    logbook.CONTEXT_SYSTEM, "logs.retention_prune", logbook.OUTCOME_INFO,
                    message=f"Pruned {sum(log_removed.values())} expired log events",
                    detail={"removed": log_removed}, db=db,
                )
                removed += sum(log_removed.values())
            return removed
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PRUNE_LOCK_KEY})


async def retention_loop() -> None:
    """Background task: prune expired request_events once per hour. Cancelled
    on app shutdown."""
    while True:
        try:
            removed = await anyio.to_thread.run_sync(_prune_once)
            if removed:
                logger.info("request_events retention: pruned %d rows", removed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the loop die on a transient DB error
            logger.exception("request_events retention prune failed")
        await asyncio.sleep(PRUNE_INTERVAL_S)
