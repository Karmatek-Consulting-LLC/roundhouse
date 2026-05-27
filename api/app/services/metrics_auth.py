"""Shared secret used by the platform-api to scrape /metrics on spawned
servers. Lives in its own module - and reads APP_KEY straight from os.environ
- so codegen can import it without pulling pydantic / sqlalchemy via the
larger config + server_auth modules."""
from __future__ import annotations

import hashlib
import os


def metrics_token_for(server_name: str, app_key: str | None = None) -> str:
    """Deterministic from (APP_KEY, server_name). Codegen bakes the value
    into the generated server.py; the platform-api recomputes it at scrape
    time. No extra storage required."""
    key = app_key if app_key is not None else os.environ.get("APP_KEY", "")
    return hashlib.sha256(f"{key}|{server_name}|metrics".encode("utf-8")).hexdigest()[:32]
