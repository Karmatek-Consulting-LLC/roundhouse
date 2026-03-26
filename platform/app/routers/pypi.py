from __future__ import annotations

import logging
import re
import time

import httpx
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory cache of PyPI package names
_package_cache: list[str] = []
_cache_time: float = 0
_CACHE_TTL = 3600  # refresh every hour


async def _load_package_index() -> list[str]:
    """Fetch the PyPI Simple API index and extract package names."""
    global _package_cache, _cache_time

    if _package_cache and (time.time() - _cache_time) < _CACHE_TTL:
        return _package_cache

    logger.info("Fetching PyPI package index...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://pypi.org/simple/",
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        )
        resp.raise_for_status()

    data = resp.json()
    _package_cache = [p["name"] for p in data.get("projects", [])]
    _cache_time = time.time()
    logger.info("Cached %d PyPI package names", len(_package_cache))
    return _package_cache


async def _get_package_info(name: str) -> dict | None:
    """Fetch package metadata from PyPI JSON API."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://pypi.org/pypi/{name}/json")
            if resp.status_code != 200:
                return None
            info = resp.json()["info"]
            return {
                "name": info["name"],
                "version": info["version"],
                "summary": info.get("summary") or "",
            }
    except Exception:
        return None


@router.get("/pypi/search")
async def search_pypi(q: str = Query(min_length=2)):
    """Search PyPI packages with prefix matching."""
    query = q.strip().lower()
    packages = await _load_package_index()

    # Prefix match, limited to 10 results
    # Normalize: PyPI names are case-insensitive and treat - _ . as equivalent
    normalized_q = re.sub(r"[-_.]", "-", query)
    matches = []
    for name in packages:
        normalized_name = re.sub(r"[-_.]", "-", name.lower())
        if normalized_name.startswith(normalized_q):
            matches.append(name)
            if len(matches) >= 10:
                break

    # Fetch metadata for each match in parallel
    results = []
    async with httpx.AsyncClient(timeout=5) as client:
        for name in matches:
            try:
                resp = await client.get(f"https://pypi.org/pypi/{name}/json")
                if resp.status_code == 200:
                    info = resp.json()["info"]
                    results.append({
                        "name": info["name"],
                        "version": info["version"],
                        "summary": info.get("summary") or "",
                    })
            except Exception:
                results.append({"name": name, "version": "", "summary": ""})

    return results
