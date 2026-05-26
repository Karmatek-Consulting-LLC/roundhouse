"""Tiny PyPI search proxy used by the package picker in the UI."""
from __future__ import annotations

import logging
import re
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.deps import current_user
from app.models import User

router = APIRouter(prefix="/api/pypi", tags=["pypi"])
logger = logging.getLogger(__name__)


_CACHE_TTL_SECONDS = 3600

# Simple in-process cache. The PyPI simple index is ~50MB JSON with 600k+
# projects - fetching once an hour is fine for a UI typeahead.
_package_cache: list[str] = []
_cache_loaded_at: float = 0.0


def _load_package_index() -> list[str]:
    global _package_cache, _cache_loaded_at
    if _package_cache and (time.time() - _cache_loaded_at) < _CACHE_TTL_SECONDS:
        return _package_cache
    headers = {"Accept": "application/vnd.pypi.simple.v1+json"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get("https://pypi.org/simple/", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    names = [p["name"] for p in data.get("projects", []) if isinstance(p, dict) and "name" in p]
    _package_cache = names
    _cache_loaded_at = time.time()
    return names


def _normalize(s: str) -> str:
    return re.sub(r"[-_.]", "-", s.lower())


@router.get("/search")
def search(q: str = "", _: User = Depends(current_user)):
    query = (q or "").strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="q must be at least 2 characters")
    normalized_query = _normalize(query)
    try:
        packages = _load_package_index()
    except httpx.HTTPError as e:
        logger.warning("Failed to load PyPI index: %s", e)
        return []

    matches: list[str] = []
    for name in packages:
        if _normalize(name).startswith(normalized_query):
            matches.append(name)
            if len(matches) >= 10:
                break

    results: list[dict] = []
    with httpx.Client(timeout=5.0) as client:
        for name in matches:
            try:
                resp = client.get(f"https://pypi.org/pypi/{name}/json")
                if resp.is_success:
                    info = resp.json().get("info", {}) or {}
                    results.append({
                        "name": info.get("name", name),
                        "version": info.get("version", ""),
                        "summary": info.get("summary", ""),
                    })
                    continue
            except httpx.HTTPError:
                pass
            results.append({"name": name, "version": "", "summary": ""})
    return results
