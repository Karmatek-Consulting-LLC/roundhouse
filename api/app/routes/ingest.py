"""Machine-to-machine ingest of MCP request events.

Each spawned server's middleware fire-and-forget POSTs batches of call
metadata here. Auth is the deterministic per-server metrics token
(app.services.metrics_auth.metrics_token_for) recomputed from APP_KEY — the
same scheme the platform uses to scrape /metrics — so a server can only write
rows for itself. This endpoint is intentionally NOT behind current_user: the
caller is a container on the internal network, not a human.
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RequestEvent
from app.services.metrics_auth import metrics_token_for

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)

# Bound per request so a buggy/hostile client can't ship an unbounded payload.
# Generated servers batch up to 500 per flush (codegen _ingest_flush_loop).
MAX_BATCH = 500


class IngestEvent(BaseModel):
    ts: float  # epoch seconds, from the originating server's clock
    kind: str
    name: str
    client_id: str | None = None
    duration_ms: float
    error: str | None = None


class IngestBatch(BaseModel):
    server: str
    events: list[IngestEvent] = Field(default_factory=list, max_length=MAX_BATCH)


@router.post("/events", status_code=202)
def ingest_events(
    batch: IngestBatch,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Accept a batch of call-metadata events for one server and persist them.

    Returns 202 (accepted) — capture is best-effort observability, never on the
    request's critical path, so we never make the caller retry."""
    expected = "Bearer " + metrics_token_for(batch.server)
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")

    if not batch.events:
        return {"accepted": 0}

    rows = []
    for e in batch.events:
        client_id = e.client_id[:255] if e.client_id else None
        rows.append(
            {
                "server_name": batch.server[:255],
                "ts": datetime.fromtimestamp(e.ts, tz=timezone.utc),
                "kind": (e.kind or "")[:32],
                "name": (e.name or "")[:255],
                "client_id": client_id,
                "duration_ms": float(e.duration_ms),
                "error": e.error,
                "status": "error" if e.error else "ok",
            }
        )

    # Single multi-row INSERT (Core), committed by get_db on return.
    db.execute(insert(RequestEvent), rows)
    return {"accepted": len(rows)}
