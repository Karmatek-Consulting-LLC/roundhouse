"""Global MCP env vars stored as JSON in platform_settings."""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.platform_settings import (
    SETTING_GLOBAL_ENV_VARS,
    get_setting,
    put_setting,
)
from app.services.spec import EnvVar

logger = logging.getLogger(__name__)


def list_globals(db: Session) -> list[EnvVar]:
    raw = (get_setting(db, SETTING_GLOBAL_ENV_VARS, "") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON for %s; treating as empty", SETTING_GLOBAL_ENV_VARS)
        return []
    if not isinstance(data, list):
        return []
    out: list[EnvVar] = []
    for item in data:
        ev = EnvVar.from_dict(item)
        if ev is not None:
            out.append(ev)
    return out


def save_globals(db: Session, vars_: list[EnvVar]) -> None:
    put_setting(db, SETTING_GLOBAL_ENV_VARS, json.dumps([v.to_dict() for v in vars_]))


def globals_as_dict(db: Session) -> dict[str, str]:
    return {ev.name: ev.value for ev in list_globals(db)}
