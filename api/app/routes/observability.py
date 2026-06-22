"""Read API for the realtime Observe console.

Backed by the persistent request_events table (populated by /api/ingest/events).
Every endpoint is user-authed and scoped to the servers the caller can see via
permissions.accessible_names — superadmins see everything, others see only their
own + teammates' servers. This is history/charts; the live point-in-time
/metrics scrape endpoints (/api/dashboard/usage, /api/servers/{name}/usage) are
unchanged and complementary.
"""
from __future__ import annotations

import asyncio
import json
import logging

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import db_session, get_db
from app.deps import current_user
from app.models import User
from app.services import permissions

router = APIRouter(prefix="/api/observability", tags=["observability"])
logger = logging.getLogger(__name__)

# Whitelisted to bound query cost (range/bucket must yield a sane bucket count).
RANGE_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
}
BUCKET_SECONDS: dict[str, int] = {
    "10s": 10,
    "30s": 30,
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "1h": 3600,
}
# Bucket chosen for "auto" so each range renders a smooth, bounded series.
AUTO_BUCKET: dict[str, str] = {
    "5m": "10s",
    "15m": "30s",
    "1h": "1m",
    "6h": "5m",
    "24h": "10m",
    "7d": "1h",
}
MAX_BUCKETS = 5000
KINDS = ("tool", "resource", "resource_template", "prompt")
FEED_MAX_LIMIT = 500


# ---- scoping -------------------------------------------------------------

def _resolve_scope(db: Session, user: User, server: str | None) -> list[str] | None:
    """Return None (no filter — superadmin/all) or a list of server names the
    caller may read. A list with the requested `server` is returned when one is
    given (403 if it isn't accessible). An empty list means 'nothing visible'."""
    names = permissions.accessible_names(db, user)  # None = superadmin
    if server is not None:
        if names is not None and server not in names:
            raise HTTPException(status_code=403, detail="Access denied")
        return [server]
    return names


def _scope_sql(names: list[str] | None) -> str:
    """SQL fragment + (implicitly) the :names bind. Empty string when no filter."""
    if names is None:
        return ""
    return " AND server_name = ANY(:names)"


# ---- helpers -------------------------------------------------------------

def _range_seconds(range_: str) -> int:
    secs = RANGE_SECONDS.get(range_)
    if secs is None:
        raise HTTPException(status_code=422, detail=f"invalid range (allowed: {', '.join(RANGE_SECONDS)})")
    return secs


def _bucket_seconds(range_: str, bucket: str) -> int:
    if bucket == "auto":
        bucket = AUTO_BUCKET[range_]
    secs = BUCKET_SECONDS.get(bucket)
    if secs is None:
        raise HTTPException(status_code=422, detail=f"invalid bucket (allowed: auto, {', '.join(BUCKET_SECONDS)})")
    return secs


# ---- endpoints -----------------------------------------------------------

@router.get("/timeseries")
def timeseries(
    range: str = Query(default="1h"),
    bucket: str = Query(default="auto"),
    server: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Bucketed call volume, error count and latency percentiles over time,
    plus a per-kind breakdown for stacked charts."""
    range_s = _range_seconds(range)
    bucket_s = _bucket_seconds(range, bucket)
    if range_s / bucket_s > MAX_BUCKETS:
        raise HTTPException(status_code=422, detail="bucket too small for range")

    names = _resolve_scope(db, user, server)
    if names == []:
        return {"buckets": []}

    sql = text(
        f"""
        SELECT
          (floor(extract(epoch FROM ts) / :b) * :b)::bigint AS bucket_epoch,
          count(*) AS calls,
          count(*) FILTER (WHERE status = 'error') AS errors,
          percentile_cont(0.5)  WITHIN GROUP (ORDER BY duration_ms) AS p50,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99,
          count(*) FILTER (WHERE kind = 'tool')              AS k_tool,
          count(*) FILTER (WHERE kind = 'resource')          AS k_resource,
          count(*) FILTER (WHERE kind = 'resource_template') AS k_resource_template,
          count(*) FILTER (WHERE kind = 'prompt')            AS k_prompt
        FROM request_events
        WHERE ts >= now() - make_interval(secs => :range_s){_scope_sql(names)}
        GROUP BY bucket_epoch
        ORDER BY bucket_epoch
        """
    )
    params: dict = {"b": bucket_s, "range_s": range_s}
    if names is not None:
        params["names"] = names

    rows = db.execute(sql, params).mappings().all()
    buckets = [
        {
            "ts": int(r["bucket_epoch"]),
            "calls": int(r["calls"]),
            "errors": int(r["errors"]),
            "p50_ms": round(float(r["p50"]), 2) if r["p50"] is not None else None,
            "p95_ms": round(float(r["p95"]), 2) if r["p95"] is not None else None,
            "p99_ms": round(float(r["p99"]), 2) if r["p99"] is not None else None,
            "by_kind": {
                "tool": int(r["k_tool"]),
                "resource": int(r["k_resource"]),
                "resource_template": int(r["k_resource_template"]),
                "prompt": int(r["k_prompt"]),
            },
        }
        for r in rows
    ]
    return {"buckets": buckets, "bucket_s": bucket_s}


def _feed_query(names: list[str] | None) -> text:
    return text(
        f"""
        SELECT id, server_name, extract(epoch FROM ts) AS ts, kind, name,
               client_id, duration_ms, status, error
        FROM request_events
        WHERE id > :since_id{_scope_sql(names)}
        ORDER BY id DESC
        LIMIT :limit
        """
    )


def _row_to_event(r) -> dict:
    return {
        "id": int(r["id"]),
        "ts": float(r["ts"]),
        "server_name": r["server_name"],
        "kind": r["kind"],
        "name": r["name"],
        "client_id": r["client_id"],
        "duration_ms": round(float(r["duration_ms"]), 2) if r["duration_ms"] is not None else None,
        "status": r["status"],
        "error": r["error"],
    }


@router.get("/feed")
def feed(
    since_id: int = Query(default=0),
    server: str | None = Query(default=None),
    limit: int = Query(default=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Recent events, newest-first, keyset-paginated on the BigInteger PK."""
    limit = max(1, min(limit, FEED_MAX_LIMIT))
    names = _resolve_scope(db, user, server)
    if names == []:
        return {"events": [], "last_id": since_id}

    params: dict = {"since_id": since_id, "limit": limit}
    if names is not None:
        params["names"] = names
    rows = db.execute(_feed_query(names), params).mappings().all()
    events = [_row_to_event(r) for r in rows]
    last_id = events[0]["id"] if events else since_id
    return {"events": events, "last_id": last_id}


@router.get("/stream")
def stream(
    request: Request,
    server: str | None = Query(default=None),
    since_id: int | None = Query(default=None),
    # EventSource can't set headers, so token comes in via query string.
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """SSE live feed. Polls request_events by an id cursor (~1s) so it is
    correct across multiple uvicorn workers without any shared in-process state.
    Each new row is emitted as a `data:` JSON frame."""
    from app.auth import resolve_token

    header_value = authorization or (f"Bearer {token}" if token else None)
    user = resolve_token(db, header_value)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthenticated")
    names = _resolve_scope(db, user, server)

    # Resolve the starting cursor before streaming: either the client's hint or
    # the current high-water mark (so we only stream genuinely new events).
    if since_id is not None:
        start_id = since_id
    else:
        start_id = db.execute(text("SELECT COALESCE(max(id), 0) FROM request_events")).scalar() or 0

    poll_sql = text(
        f"""
        SELECT id, server_name, extract(epoch FROM ts) AS ts, kind, name,
               client_id, duration_ms, status, error
        FROM request_events
        WHERE id > :cursor{_scope_sql(names)}
        ORDER BY id ASC
        LIMIT 500
        """
    )

    def _poll(cursor: int):
        if names == []:
            return []
        params: dict = {"cursor": cursor}
        if names is not None:
            params["names"] = names
        with db_session() as s:
            return s.execute(poll_sql, params).mappings().all()

    async def event_stream():
        # Open marker flushes headers so the client knows the stream is alive.
        yield "event: open\ndata: streaming\n\n"
        cursor = start_id
        try:
            while True:
                if await request.is_disconnected():
                    break
                rows = await anyio.to_thread.run_sync(_poll, cursor)
                for r in rows:
                    cursor = int(r["id"])
                    yield f"data: {json.dumps(_row_to_event(r))}\n\n"
                if not rows:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.info("observability stream ended: %s", e)
            yield f"event: error\ndata: {e}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/top")
def top(
    range: str = Query(default="24h"),
    by: str = Query(default="tool"),
    server: str | None = Query(default=None),
    limit: int = Query(default=10),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Ranked breakdown by tool, server or client, plus error and latency
    leaders, over the given range."""
    range_s = _range_seconds(range)
    limit = max(1, min(limit, 100))
    key_expr = {
        "tool": "name",
        "server": "server_name",
        "client": "coalesce(client_id, '(anonymous)')",
    }.get(by)
    if key_expr is None:
        raise HTTPException(status_code=422, detail="by must be one of: tool, server, client")

    names = _resolve_scope(db, user, server)
    if names == []:
        return {"by": by, "ranked": [], "error_leaders": [], "latency_leaders": []}

    sql = text(
        f"""
        SELECT {key_expr} AS key,
               count(*) AS calls,
               count(*) FILTER (WHERE status = 'error') AS errors,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95
        FROM request_events
        WHERE ts >= now() - make_interval(secs => :range_s){_scope_sql(names)}
        GROUP BY key
        ORDER BY calls DESC
        LIMIT 500
        """
    )
    params: dict = {"range_s": range_s}
    if names is not None:
        params["names"] = names
    rows = db.execute(sql, params).mappings().all()

    items = [
        {
            "key": r["key"],
            "label": r["key"],
            "calls": int(r["calls"]),
            "errors": int(r["errors"]),
            "p95_ms": round(float(r["p95"]), 2) if r["p95"] is not None else None,
        }
        for r in rows
    ]
    error_leaders = sorted(
        (i for i in items if i["errors"] > 0), key=lambda i: i["errors"], reverse=True
    )[:limit]
    latency_leaders = sorted(
        (i for i in items if i["p95_ms"] is not None), key=lambda i: i["p95_ms"], reverse=True
    )[:limit]
    return {
        "by": by,
        "ranked": items[:limit],
        "error_leaders": error_leaders,
        "latency_leaders": latency_leaders,
    }
