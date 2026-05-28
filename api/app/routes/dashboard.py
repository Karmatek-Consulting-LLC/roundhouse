from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import ServerOwner, User
from app.services import permissions
from app.services.docker import get_docker
from app.services.metrics_auth import metrics_token_for

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)

TOP_SERVERS = 8


def _visible_names(db: Session, user: User) -> list[str]:
    if user.is_superadmin():
        return [n for (n,) in db.query(ServerOwner.server_name).all()]
    return permissions.accessible_names(db, user) or []


def _scrape_metrics(name: str) -> dict | None:
    """Pull the running container's /metrics snapshot. Returns None when the
    server is unreachable or hasn't served its first request - callers treat
    that as 'no data' rather than an error."""
    token = metrics_token_for(name)
    url = f"http://mcp-{name}:8000/metrics"
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        logger.info("dashboard usage scrape failed for %s: %s", name, e)
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


@router.get("/usage")
def usage_summary(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Aggregate per-tool usage across every running server the caller can see.

    Point-in-time only - each call fans out a live /metrics scrape; there is no
    historical storage. Down or never-called servers simply contribute nothing."""
    docker = get_docker()
    running: list[str] = []
    for name in _visible_names(db, user):
        snap = docker.get_server(name)
        if snap and snap.get("status") == "running":
            running.append(name)

    total_calls = 0
    total_errors = 0
    by_kind: dict[str, int] = {}
    per_server: list[dict] = []
    scraped = 0

    for name in running:
        data = _scrape_metrics(name)
        if data is None:
            continue
        scraped += 1
        prims = data.get("primitives", []) or []
        s_calls = 0
        s_errors = 0
        s_p95 = 0.0
        for p in prims:
            calls = int(p.get("calls", 0) or 0)
            errors = int(p.get("errors", 0) or 0)
            s_calls += calls
            s_errors += errors
            kind = p.get("kind") or "tool"
            by_kind[kind] = by_kind.get(kind, 0) + calls
            p95 = p.get("p95_ms")
            if p95 is not None:
                s_p95 = max(s_p95, float(p95))
        total_calls += s_calls
        total_errors += s_errors
        per_server.append(
            {
                "name": name,
                "calls": s_calls,
                "errors": s_errors,
                "p95_ms": round(s_p95, 1) if s_p95 else None,
            }
        )

    per_server.sort(key=lambda r: r["calls"], reverse=True)
    error_rate = (total_errors / total_calls) if total_calls else 0.0

    return {
        "running_servers": len(running),
        "scraped_servers": scraped,
        "total_calls": total_calls,
        "total_errors": total_errors,
        "error_rate": round(error_rate, 4),
        "by_kind": by_kind,
        "top_servers": per_server[:TOP_SERVERS],
    }
