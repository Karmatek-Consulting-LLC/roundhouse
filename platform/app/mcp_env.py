"""Global and local MCP server environment variable merge (local wins over global)."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.db_models import PlatformSetting, ServerOwner
from app.models import EnvVar, ServerSpec

logger = logging.getLogger(__name__)

SETTING_GLOBAL_MCP_ENV = "mcp_global_env_vars"


def _get_setting_raw(db: Session, key: str, default: str = "") -> str:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    return row.value if row else default


def _set_setting_raw(db: Session, key: str, value: str) -> None:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(PlatformSetting(key=key, value=value))
    db.commit()


def global_env_vars_from_db(db: Session) -> list[EnvVar]:
    raw = _get_setting_raw(db, SETTING_GLOBAL_MCP_ENV).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON for %s; treating as empty", SETTING_GLOBAL_MCP_ENV)
        return []
    if not isinstance(data, list):
        return []
    out: list[EnvVar] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        val = item.get("value")
        out.append(EnvVar(name=name.strip(), value=val if isinstance(val, str) else ""))
    return out


def save_global_env_vars(db: Session, env_vars: list[EnvVar]) -> None:
    payload = [{"name": ev.name, "value": ev.value} for ev in env_vars]
    _set_setting_raw(db, SETTING_GLOBAL_MCP_ENV, json.dumps(payload))


def global_env_dict(db: Session) -> dict[str, str]:
    return {ev.name: ev.value for ev in global_env_vars_from_db(db)}


def effective_env_dict(db: Session, spec: ServerSpec) -> dict[str, str]:
    """Apply selected global imports, then local vars. Local overrides same name."""
    merged: dict[str, str] = {}
    gdict = global_env_dict(db)
    for name in spec.env_global_imports:
        if name in gdict:
            merged[name] = gdict[name]
    merged.update({ev.name: ev.value for ev in spec.env_vars})
    return merged


def all_registered_server_names(db: Session) -> list[str]:
    return [
        r[0]
        for r in db.query(ServerOwner.server_name).order_by(ServerOwner.server_name).all()
    ]


def reapply_runtime_env_for_server_name(db: Session, server_name: str, docker_mgr, store) -> None:
    """Push merged env into Docker for a deployed server (no image rebuild)."""
    spec = store.load(server_name)
    if spec is None:
        spec = ServerSpec(name=server_name)
    if not docker_mgr.get_server(server_name):
        return
    env = effective_env_dict(db, spec)
    try:
        docker_mgr.update_runtime_env(server_name, env)
    except Exception:
        logger.exception("Failed to update runtime env for server '%s'", server_name)


def reapply_runtime_env_for_servers(db: Session, server_names: list[str], docker_mgr, store) -> None:
    for name in server_names:
        reapply_runtime_env_for_server_name(db, name, docker_mgr, store)
