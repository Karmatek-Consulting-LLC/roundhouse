"""Read API for the superadmin Logs console (backed by log_events).

Contexts partition the stream; "auth" ships first. Endpoints:

    GET /api/logs          list/search, newest-first, keyset-paginated
    GET /api/logs/stream   SSE live tail (poll-based, multi-worker safe)
    GET /api/logs/export   CSV / JSON download of the filtered slice

Everything is superadmin-only. The SSE stream authenticates via ?token=
(EventSource can't set headers) and re-checks the role explicitly.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, Query as OrmQuery

from app import logbook
from app.db import db_session, get_db
from app.deps import require_superadmin
from app.models import LogEvent, User

router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = logging.getLogger(__name__)

# Whitelist so a typo'd context is a 422, not a silently-empty result.
KNOWN_CONTEXTS = (logbook.CONTEXT_AUTH,)
LIST_MAX_LIMIT = 500
EXPORT_MAX_ROWS = 50_000
EXPORT_COLUMNS = (
    "id", "ts", "context", "event_type", "outcome",
    "actor_email", "ip", "user_agent", "message", "detail",
)


def _validate_context(context: str) -> str:
    if context not in KNOWN_CONTEXTS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown context (allowed: {', '.join(KNOWN_CONTEXTS)})",
        )
    return context


def _filtered(
    query: OrmQuery,
    *,
    context: str,
    q: str | None,
    event_type: str | None,
    outcome: str | None,
) -> OrmQuery:
    query = query.filter(LogEvent.context == context)
    if event_type:
        query = query.filter(LogEvent.event_type == event_type)
    if outcome:
        query = query.filter(LogEvent.outcome == outcome)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                LogEvent.actor_email.ilike(like),
                LogEvent.ip.ilike(like),
                LogEvent.message.ilike(like),
                LogEvent.event_type.ilike(like),
            )
        )
    return query


@router.get("")
def list_events(
    context: str = Query(default=logbook.CONTEXT_AUTH),
    q: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    since_id: int = Query(default=0, ge=0),
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=LIST_MAX_LIMIT),
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Newest-first slice of one context. `since_id` fetches only newer rows
    (stream backfill cursor); `before_id` pages older rows ("Load older")."""
    _validate_context(context)
    query = _filtered(
        db.query(LogEvent), context=context, q=q, event_type=event_type, outcome=outcome
    )
    if since_id:
        query = query.filter(LogEvent.id > since_id)
    if before_id is not None:
        query = query.filter(LogEvent.id < before_id)
    rows = query.order_by(LogEvent.id.desc()).limit(limit).all()
    events = [logbook.serialize(e) for e in rows]
    last_id = events[0]["id"] if events else since_id
    return {"events": events, "last_id": last_id, "has_more": len(events) == limit}


@router.get("/stream")
def stream(
    request: Request,
    context: str = Query(default=logbook.CONTEXT_AUTH),
    q: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    since_id: int | None = Query(default=None),
    # EventSource can't set headers, so token comes in via query string.
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """SSE live tail. Polls log_events by an id cursor (~1s) so it is correct
    across multiple uvicorn workers without shared in-process state. Each new
    row is emitted as a `data:` JSON frame."""
    from app.auth import resolve_token

    header_value = authorization or (f"Bearer {token}" if token else None)
    user = resolve_token(db, header_value)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthenticated")
    if not user.is_superadmin():
        raise HTTPException(status_code=403, detail="Forbidden")
    _validate_context(context)

    # Resolve the starting cursor before streaming: either the client's hint or
    # the current high-water mark (so we only stream genuinely new events).
    if since_id is not None:
        start_id = since_id
    else:
        from sqlalchemy import func as sa_func

        start_id = db.query(sa_func.max(LogEvent.id)).scalar() or 0

    def _poll(cursor: int) -> list[dict]:
        with db_session() as s:
            query = _filtered(
                s.query(LogEvent), context=context, q=q, event_type=event_type, outcome=outcome
            )
            rows = (
                query.filter(LogEvent.id > cursor)
                .order_by(LogEvent.id.asc())
                .limit(500)
                .all()
            )
            return [logbook.serialize(e) for e in rows]

    async def event_stream():
        # Open marker flushes headers so the client knows the stream is alive.
        yield "event: open\ndata: streaming\n\n"
        cursor = start_id
        try:
            while True:
                if await request.is_disconnected():
                    break
                events = await anyio.to_thread.run_sync(_poll, cursor)
                for e in events:
                    cursor = int(e["id"])
                    yield f"data: {json.dumps(e)}\n\n"
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.info("log stream ended: %s", e)
            yield f"event: error\ndata: {e}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/export")
def export(
    context: str = Query(default=logbook.CONTEXT_AUTH),
    q: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    format: str = Query(default="csv"),
    limit: int = Query(default=10_000, ge=1, le=EXPORT_MAX_ROWS),
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Download the filtered slice (newest-first) as CSV or JSON, for sharing
    outside the UI — the whole point when the operator has no host access."""
    _validate_context(context)
    if format not in ("csv", "json"):
        raise HTTPException(status_code=422, detail="format must be csv or json")

    query = _filtered(
        db.query(LogEvent), context=context, q=q, event_type=event_type, outcome=outcome
    )
    rows = [
        logbook.serialize(e)
        for e in query.order_by(LogEvent.id.desc()).limit(limit).all()
    ]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"roundhouse-{context}-log-{stamp}.{format}"

    if format == "json":
        body = json.dumps({"context": context, "exported_at": stamp, "events": rows}, indent=2)
        media_type = "application/json"
    else:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(EXPORT_COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    json.dumps(r[c]) if c == "detail" and r[c] is not None else r[c]
                    for c in EXPORT_COLUMNS
                ]
            )
        body = buf.getvalue()
        media_type = "text/csv"

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
