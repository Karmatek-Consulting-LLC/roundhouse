from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi import File as FastApiFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import logbook
from app.audit import record as audit_record
from app.config import get_settings
from app.db import get_db
from app.deps import current_user
from app.models import ServerOwner, ServerScope, User
from app.services import discovery, global_env, permissions
from app.services.docker import DockerError, DockerNotFoundError, RegistryRequiredError, get_docker
from app.services.git_manifest import parse_manifest
from app.services.server_service import get_server_service
from app.services.spec import (
    MODE_CODE,
    MODE_REMOTE,
    MODE_STRUCTURED,
    EnvVar,
    ServerSpec,
    normalize_env_imports,
    normalize_env_name,
)

router = APIRouter(prefix="/api/servers", tags=["servers"])
logger = logging.getLogger(__name__)


# ---- Helpers ----

_SERVER_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
_APT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+._:=~-]*$")


def _assert_access(db: Session, user: User, name: str) -> None:
    if not permissions.can_access(db, user, name):
        raise HTTPException(status_code=403, detail="Access denied")


def _log_deploy_failure(user: User | None, event_type: str, server: str, error: Exception) -> None:
    """Failed lifecycle ops never reach audit_record (the request transaction
    is about to roll back), so push them to the Logs console on the logbook's
    own session — a build/orchestrator failure must be visible in the UI."""
    logbook.record(
        logbook.CONTEXT_DEPLOY, event_type, logbook.OUTCOME_FAILURE,
        user=user, message=str(error),
        detail={"server": server, "error_type": type(error).__name__},
    )


def _missing_snapshot(name: str) -> dict:
    return {
        "name": name,
        "template": "custom",
        "status": "not_deployed",
        "created_at": "",
        "replicas_running": 0,
        "placement": [],
    }


def _unknown_snapshot(name: str) -> dict:
    return {
        "name": name,
        "template": "custom",
        "status": "unknown",
        "created_at": "",
        "replicas_running": 0,
        "placement": [],
    }


def _docker_snapshot(name: str) -> dict:
    try:
        d = get_docker().get_server(name)
    except Exception as e:  # noqa: BLE001 - log + degrade
        logger.warning("Docker get_server failed for %s: %s", name, e)
        return _unknown_snapshot(name)
    return d if d is not None else _missing_snapshot(name)


def _ensure_spec(db: Session, name: str) -> ServerSpec:
    service = get_server_service()
    spec = service.store.load(name)
    if spec is not None:
        return spec
    in_docker = get_docker().get_server(name)
    in_db = (
        db.query(ServerOwner).filter(ServerOwner.server_name == name).first() is not None
    )
    if in_docker or in_db:
        spec = ServerSpec(name=name)
        service.store.save(spec)
        return spec
    raise HTTPException(status_code=404, detail=f"Server '{name}' not found")


def _assert_structured(spec: ServerSpec, op: str) -> None:
    if spec.is_code_mode():
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {op} on a code-mode server - edit its server.py source instead.",
        )


def _assert_scopes_exist(db: Session, server: str, scopes: list[str]) -> None:
    if not scopes:
        return
    rows = (
        db.query(ServerScope.name)
        .filter(ServerScope.server_name == server, ServerScope.name.in_(scopes))
        .all()
    )
    known = {n for (n,) in rows}
    unknown = [s for s in scopes if s not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unknown scopes for this server: " + ", ".join(unknown),
        )


def _env_vars_for_response(env_vars: list[EnvVar]) -> list[dict[str, Any]]:
    """Project env_vars for outbound API responses. Secret values are never
    echoed - we surface a `has_value` flag so the UI knows whether the row
    holds something to preserve on the next save."""
    out: list[dict[str, Any]] = []
    for ev in env_vars:
        if ev.secret:
            out.append({
                "name": ev.name,
                "value": "",
                "secret": True,
                "has_value": bool(ev.value),
            })
        else:
            out.append({"name": ev.name, "value": ev.value, "secret": False, "has_value": bool(ev.value)})
    return out


# Active readiness probe. "running" from the orchestrator only means the
# container/task is scheduled - on Swarm it's literally just replica count, with
# no container health at all. A 200 from the server's own /healthz means FastMCP
# is actually serving. Short timeout + concurrent fan-out keeps the list fast.
_HEALTHZ_TIMEOUT = 1.5
_HEALTH_UNSET = object()
# Readiness grace: how long after a task starts serving we still call a
# not-yet-answering /healthz "starting" (amber) rather than "unhealthy" (red),
# so a normal deploy's pull+boot window doesn't look like a failure.
_HEALTH_GRACE_SECONDS = float(os.environ.get("RH_HEALTH_GRACE_SECONDS", "45"))


def _probe_healthz(name: str) -> str | None:
    """GET the server's /healthz. 'healthy' on 200, 'unhealthy' on any failure
    or non-200. None when no URL can be resolved."""
    try:
        url = get_server_service().healthz_url(name)
    except Exception:  # noqa: BLE001
        return None
    try:
        with httpx.Client(timeout=_HEALTHZ_TIMEOUT) as client:
            resp = client.get(url)
        return "healthy" if resp.status_code == 200 else "unhealthy"
    except httpx.HTTPError:
        return "unhealthy"


def _probe_healthz_many(names: list[str]) -> dict[str, str | None]:
    if not names:
        return {}
    out: dict[str, str | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(names))) as ex:
        for n, h in zip(names, ex.map(_probe_healthz, names)):
            out[n] = h
    return out


def _effective_health(snap: dict, probe: str | None) -> str | None:
    """Combine the orchestrator's view with the active /healthz probe.

    The probe is the only signal that reflects real FastMCP readiness (esp. on
    Swarm, where container health isn't exposed). But a failing probe during a
    normal deploy's pull+boot window must NOT read as "unhealthy" — that alarms
    operators. So a failing probe is reported as "starting" while the workload is
    still coming up:
      - no running task yet (Swarm: image pulling / scheduling), or
      - a task started within the readiness grace window.
    Only once it's been up past the grace and still isn't serving do we go red."""
    if probe == "healthy":
        return "healthy"
    # Standalone Docker's own start-period grace.
    if snap.get("health") == "starting":
        return "starting"
    if probe == "unhealthy":
        if snap.get("has_running_task") is False:
            return "starting"  # pulling / scheduling — not broken, just not up yet
        since = snap.get("running_since")
        if since is not None and (time.time() - since) < _HEALTH_GRACE_SECONDS:
            return "starting"
        return "unhealthy"
    # No probe ran (e.g. not running) — defer to the orchestrator's view.
    return snap.get("health")


def _to_response(
    db: Session, snap: dict, spec: ServerSpec | None, health_override: Any = _HEALTH_UNSET
) -> dict:
    service = get_server_service()
    name = snap.get("name") or ""
    owner_row = (
        db.query(ServerOwner).filter(ServerOwner.server_name == name).first()
    )
    owner_email: str | None = None
    if owner_row and owner_row.owner:
        owner_email = owner_row.owner.email
    return {
        "name": name,
        "template": snap.get("template") or "custom",
        "status": snap.get("status") or "unknown",
        "health": snap.get("health") if health_override is _HEALTH_UNSET else health_override,
        "restart_count": snap.get("restart_count"),
        "url": f"{service.base_url(db)}/s/{name}/mcp",
        "description": spec.description if spec else "",
        "mode": spec.mode if spec else MODE_STRUCTURED,
        "source": spec.source if spec else None,
        "imports": spec.imports if spec else [],
        "primitives": spec.primitives if spec else [],
        "pip_packages": spec.pip_packages if spec else [],
        "apt_packages": spec.apt_packages if spec else [],
        "env_global_imports": spec.env_global_imports if spec else [],
        "env_vars": _env_vars_for_response(spec.env_vars if spec else []),
        "global_env": [v.to_dict() for v in global_env.list_globals(db)],
        "owner_id": str(owner_row.owner_id) if owner_row else None,
        "owner_email": owner_email,
        "redeploy_required_at": (
            owner_row.redeploy_required_at.isoformat()
            if owner_row and owner_row.redeploy_required_at
            else None
        ),
        "created_at": snap.get("created_at"),
        "replicas_desired": service.effective_replicas(spec),
        "replicas_running": int(snap.get("replicas_running") or 0),
        "orchestrator": get_docker().mode(),
        "supports_scaling": get_docker().supports_scaling(),
        # Back-compat: frontend reads docker_swarm_mode as a "supports scaling" flag.
        "docker_swarm_mode": get_docker().supports_scaling(),
        "placement": snap.get("placement") or [],
        # Desired node-label placement selectors (input); distinct from
        # `placement` above, which is where tasks currently run (output).
        "placement_constraints": spec.placement_constraints if spec else [],
        "cpu_limit": spec.cpu_limit if spec else None,
        "memory_limit_mb": spec.memory_limit_mb if spec else None,
        "git_url": spec.git_url if spec else None,
        "git_ref": spec.git_ref if spec else None,
        # Remote-proxy config (secret header values are never echoed - only the
        # header->env mapping, mirroring how secret env_vars are masked above).
        "remote_url": spec.remote_url if spec else None,
        "remote_headers": spec.remote_headers if spec else [],
        "deny_unlisted": spec.deny_unlisted if spec else False,
    }


def _registered_names_for_user(db: Session, user: User) -> list[str]:
    if user.is_superadmin():
        return [
            n
            for (n,) in db.query(ServerOwner.server_name).order_by(ServerOwner.server_name).all()
        ]
    names = permissions.accessible_names(db, user) or []
    return sorted(names)


# ---- Listing / reads ----

@router.get("")
def index(user: User = Depends(current_user), db: Session = Depends(get_db)):
    service = get_server_service()
    names = _registered_names_for_user(db, user)
    snaps = {name: _docker_snapshot(name) for name in names}
    specs = {name: service.store.load(name) for name in names}
    # Probe /healthz concurrently for servers the orchestrator reports as up, so
    # "running" reflects real FastMCP readiness rather than just task state.
    probes = _probe_healthz_many([n for n in names if snaps[n].get("status") == "running"])
    return [
        _to_response(
            db, snaps[name], specs[name],
            health_override=_effective_health(snaps[name], probes.get(name)),
        )
        for name in names
    ]


@router.get("/limits")
def limits(_: User = Depends(current_user)):
    cfg = get_settings()
    return {
        "default_mcp_server_replicas": cfg.mcp_default_server_replicas,
        "max_mcp_server_replicas": cfg.mcp_max_server_replicas,
        "orchestrator": get_docker().mode(),
        "supports_scaling": get_docker().supports_scaling(),
        "docker_swarm_mode": get_docker().supports_scaling(),
    }


@router.get("/node-labels")
def node_labels(_: User = Depends(current_user)):
    """Node-label key=value pairs available for placement selection, derived
    from the swarm's actual node labels (not free-form). `supported` is false
    off Swarm, so the UI can hide the picker rather than offer an empty list."""
    docker = get_docker()
    return {"supported": docker.supports_scaling(), "labels": docker.list_node_labels()}


@router.get("/{name}")
def show(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access(db, user, name)
    snap = _docker_snapshot(name)
    spec = get_server_service().store.load(name)
    probe = _probe_healthz(name) if snap.get("status") == "running" else None
    return _to_response(db, snap, spec, health_override=_effective_health(snap, probe))


@router.get("/{name}/logs", response_class=Response)
def logs(
    name: str,
    tail: int = Query(default=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    docker = get_docker()
    if not docker.get_server(name):
        raise HTTPException(
            status_code=404,
            detail=f"No Docker service for '{name}'; logs are unavailable until deployed.",
        )
    try:
        text = docker.get_server_logs(name, tail)
    except DockerNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DockerError as e:
        logger.error("Failed to read logs for server '%s': %s", name, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=text, media_type="text/plain; charset=utf-8")


@router.get("/{name}/logs/stream")
def logs_stream(
    name: str,
    tail: int = Query(default=100),
    # EventSource can't set headers, so token comes in via query string.
    # The Authorization header path also works for non-browser clients.
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """SSE log stream. Sends each chunk as a `data:` event. The connection
    stays open until the client closes it or the container exits."""
    from app.auth import resolve_token
    header_value = authorization or (f"Bearer {token}" if token else None)
    user = resolve_token(db, header_value)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthenticated")
    _assert_access(db, user, name)

    docker = get_docker()
    if not docker.get_server(name):
        raise HTTPException(
            status_code=404,
            detail=f"No Docker service for '{name}'; logs are unavailable until deployed.",
        )

    async def event_stream():
        import anyio
        # Open marker so clients know the stream is alive even before Docker
        # pushes the first chunk. Also forces uvicorn to flush headers.
        yield "event: open\ndata: streaming\n\n"
        try:
            stream = await anyio.to_thread.run_sync(docker.stream_server_logs, name, tail)
        except Exception as e:  # noqa: BLE001
            yield f"event: error\ndata: {e}\n\n"
            return
        try:
            iterator = iter(stream)

            def next_chunk():
                try:
                    return next(iterator)
                except StopIteration:
                    return None

            while True:
                chunk = await anyio.to_thread.run_sync(next_chunk)
                if chunk is None:
                    break
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"event: error\ndata: {e}\n\n"
        finally:
            stream.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx-style proxy buffering
        },
    )


@router.get("/{name}/usage")
def usage(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Scrape the spawned container's /metrics endpoint and return the
    snapshot. Uses the deterministic per-server metrics token so the
    endpoint stays closed to external callers.

    Returns {primitives: [...], tokens: [...], started_ts, now_ts}. When the
    container is down or hasn't yet served its first request, returns an
    empty snapshot rather than 404 - the editor wants to render the panel
    either way."""
    import httpx
    from app.services.metrics_auth import metrics_token_for

    _assert_access(db, user, name)
    snap = get_docker().get_server(name)
    if not snap or snap.get("status") != "running":
        return {"primitives": [], "tokens": [], "started_ts": 0, "now_ts": 0, "available": False}

    token = metrics_token_for(name)
    url = get_server_service().metrics_url(name)
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        logger.info("usage scrape failed for %s: %s", name, e)
        return {"primitives": [], "tokens": [], "started_ts": 0, "now_ts": 0, "available": False}
    if resp.status_code != 200:
        # 401 here means the server was built before this token scheme - it
        # will refresh on next redeploy.
        return {"primitives": [], "tokens": [], "started_ts": 0, "now_ts": 0, "available": False}
    data = resp.json()
    data["available"] = True
    return data


# ---- Create ----

class RemoteHeaderIn(BaseModel):
    # An outbound header sent to the upstream MCP server. `value` is the secret
    # credential (e.g. "ApiKey <base64>"); it's stored encrypted as an env var
    # and never echoed back.
    header: str
    value: str


class PlacementConstraintIn(BaseModel):
    key: str
    value: str


def _validate_placement(constraints: list[PlacementConstraintIn]) -> list[dict]:
    """Resolve submitted node-label selectors against the labels that actually
    exist on the swarm, rejecting anything free-form. Returns the de-duplicated
    [{"key","value"}] to persist on the spec; [] when nothing was submitted."""
    if not constraints:
        return []
    available = {(lbl["key"], lbl["value"]) for lbl in get_docker().list_node_labels()}
    if not available:
        raise HTTPException(
            status_code=422,
            detail="Node-label placement requires a Docker Swarm with labeled nodes.",
        )
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in constraints:
        key = (c.key or "").strip()
        value = (c.value or "").strip()
        if not key or not value:
            continue
        if (key, value) not in available:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown node label {key}={value}; choose from existing node labels.",
            )
        if (key, value) not in seen:
            seen.add((key, value))
            out.append({"key": key, "value": value})
    return out


class CreateServerIn(BaseModel):
    name: str
    description: str | None = ""
    template: str | None = None
    config: dict[str, Any] = {}
    replicas: int | None = None
    mode: Literal["structured", "code", "remote"] = MODE_STRUCTURED
    source: str | None = None
    # Remote-proxy fields (mode == "remote").
    remote_url: str | None = None
    remote_headers: list[RemoteHeaderIn] = []
    # Swarm node-label placement selectors (all ANDed). Validated against the
    # labels that exist on the cluster; empty = schedule anywhere.
    placement_constraints: list[PlacementConstraintIn] = []


def _apply_remote_headers(spec: ServerSpec, headers: list[RemoteHeaderIn]) -> None:
    """Stash each outbound header's secret value as an encrypted env var and
    record the header->env mapping on the spec. The generated proxy reads the
    value from os.environ at runtime; the plaintext never touches the spec, the
    image, or any API response."""
    mapping: list[dict] = []
    for h in headers:
        header = (h.header or "").strip()
        value = h.value or ""
        if not header or not value.strip():
            continue
        env_name = "RH_REMOTE_" + normalize_env_name(header)
        # Replace any prior row for this env name (re-create / edit).
        spec.env_vars = [ev for ev in spec.env_vars if ev.name != env_name]
        spec.env_vars.append(EnvVar(name=env_name, value=_encrypt_env(value), secret=True))
        mapping.append({"header": header, "env": env_name})
    spec.remote_headers = mapping


@router.post("", status_code=201)
def store(
    payload: CreateServerIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_settings()
    if not _SERVER_NAME_RE.match(payload.name):
        raise HTTPException(status_code=422, detail="Invalid server name")
    if payload.replicas is not None and not (1 <= payload.replicas <= cfg.mcp_max_server_replicas):
        raise HTTPException(
            status_code=422,
            detail=f"replicas must be between 1 and {cfg.mcp_max_server_replicas}",
        )
    if payload.mode == MODE_CODE:
        if not (payload.source and payload.source.strip()):
            raise HTTPException(status_code=422, detail='source is required when mode is "code"')
        if payload.template:
            raise HTTPException(
                status_code=422,
                detail="Cannot specify both a template and code-mode source",
            )
    if payload.mode == MODE_REMOTE:
        if not (payload.remote_url and payload.remote_url.strip()):
            raise HTTPException(status_code=422, detail='remote_url is required when mode is "remote"')
        if not payload.remote_url.strip().startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="remote_url must be an http(s) URL")
        if payload.template:
            raise HTTPException(
                status_code=422, detail="Cannot specify both a template and a remote URL"
            )

    placement_constraints = _validate_placement(payload.placement_constraints)

    service = get_server_service()
    docker = get_docker()
    name = payload.name

    if docker.get_server(name):
        raise HTTPException(status_code=409, detail=f"Server '{name}' already exists")

    _cleanup_orphan_registration(db, name, user)

    if db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
        raise HTTPException(
            status_code=409, detail=f"Server name '{name}' is already registered"
        )

    db.add(ServerOwner(server_name=name, owner_id=user.id))
    db.flush()

    try:
        spec = ServerSpec(
            name=name,
            description=payload.description or "",
            replicas=payload.replicas,
            mode=payload.mode,
            source=(payload.source if payload.mode == MODE_CODE else None),
            placement_constraints=placement_constraints,
        )

        if payload.mode == MODE_REMOTE:
            spec.remote_url = payload.remote_url.strip()
            spec.deny_unlisted = True  # remote defaults to locked-until-granted
            _apply_remote_headers(spec, payload.remote_headers)
            service.store.save(spec)
            # Discover the upstream toolset before the first deploy so the nav
            # populates immediately. Best-effort: a transient upstream/credential
            # problem shouldn't block creation - the operator can Rediscover.
            try:
                spec.primitives = service.discover_primitives(db, spec)
            except Exception as e:  # noqa: BLE001
                logger.warning("Initial discovery failed for remote '%s': %s", name, e)
            result = service.build_and_deploy(db, spec)
        elif payload.template:
            tmpl = service.templates.get_template(payload.template)
            if tmpl is None:
                raise HTTPException(
                    status_code=404, detail=f"Template '{payload.template}' not found"
                )
            from app.services import build_context as buildctx
            build_context = service.templates.render(payload.template, name, payload.config)
            try:
                service.store.save(spec)
                # Persist the rendered files so future redeploys (which rebuild
                # from DB, not this temp dir) have the template's source.
                service.store.set_build_files(name, buildctx.snapshot_dir(build_context))
                result = docker.build_and_start(
                    server_name=name,
                    build_context=build_context,
                    template_name=payload.template,
                    env_vars=service.effective_env(db, spec),
                    replicas=service.effective_replicas(spec),
                    registry_prefix=service.registry_prefix(db),
                    registry_auth=service.registry_auth(db),
                    placement_constraints=spec.placement_constraints,
                )
            finally:
                shutil.rmtree(build_context, ignore_errors=True)
        else:
            result = service.build_and_deploy(db, spec)

        audit_record(db, user, "server.create", "server", name, {
            "template": payload.template, "mode": payload.mode,
        })
        return _to_response(db, result, spec)
    except HTTPException:
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to create server '%s': %s", name, e)
        _log_deploy_failure(user, "server.create", name, e)
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        service.store.delete(name)
        try:
            docker.remove_server(name, service.registry_prefix(db))
        except Exception:  # noqa: BLE001
            pass
        status_code = 409 if isinstance(e, RegistryRequiredError) else 500
        raise HTTPException(status_code=status_code, detail=str(e)) from e


def _cleanup_orphan_registration(db: Session, name: str, user: User) -> None:
    if get_docker().get_server(name):
        return
    orphan = db.query(ServerOwner).filter(ServerOwner.server_name == name).first()
    if orphan is None:
        return
    if not user.is_superadmin() and str(orphan.owner_id) != str(user.id):
        raise HTTPException(
            status_code=403,
            detail=f"Server name '{name}' is already registered to another user",
        )
    logger.warning("Removing orphaned server_owners row for '%s'", name)
    db.delete(orphan)
    db.flush()
    get_server_service().store.delete(name)


# ---- Lifecycle ----

@router.post("/{name}/start")
def start(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access(db, user, name)
    docker = get_docker()
    if not docker.get_server(name):
        raise HTTPException(
            status_code=400,
            detail="Server has no Docker service to start. Deploy configuration from the server details page first.",
        )
    service = get_server_service()
    spec = service.store.load(name)
    replicas = service.effective_replicas(spec)
    result = docker.start_server(name, replicas)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    if spec is None:
        spec = _ensure_spec(db, name)
    audit_record(db, user, "server.start", "server", name)
    return _to_response(db, result, spec)


@router.post("/{name}/stop")
def stop(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access(db, user, name)
    docker = get_docker()
    if not docker.get_server(name):
        raise HTTPException(
            status_code=400,
            detail="Server is not deployed to Docker; nothing to stop.",
        )
    result = docker.stop_server(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = get_server_service().store.load(name)
    audit_record(db, user, "server.stop", "server", name)
    return _to_response(db, result, spec)


@router.post("/{name}/redeploy")
def redeploy(
    name: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    try:
        result = get_server_service().redeploy(db, spec)
        audit_record(db, user, "server.redeploy", "server", name)
        return _to_response(db, result, spec)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to redeploy '%s': %s", name, e)
        _log_deploy_failure(user, "server.redeploy", name, e)
        status_code = 409 if isinstance(e, RegistryRequiredError) else 500
        raise HTTPException(status_code=status_code, detail=str(e)) from e


@router.post("/{name}/rediscover")
def rediscover(
    name: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Re-introspect a proxied server (code-first or remote) and reconcile its
    primitives: new tools added, removed tools archived, assigned scopes kept.
    Flags a redeploy so the regenerated proxy picks up the new scope config.
    Remote servers can be rediscovered any time (the API reaches the upstream
    directly); code-first servers must be deployed and running first."""
    _assert_access(db, user, name)
    service = get_server_service()
    spec = _ensure_spec(db, name)
    if not spec.is_proxied():
        raise HTTPException(
            status_code=409,
            detail="Rediscovery only applies to code-first or remote servers.",
        )
    try:
        spec.primitives = service.discover_primitives(db, spec)
    except Exception as e:  # noqa: BLE001
        _log_deploy_failure(user, "server.rediscover", name, e)
        raise HTTPException(status_code=502, detail=f"Discovery failed: {e}") from e
    service.save_spec(db, spec)
    audit_record(db, user, "server.rediscover", "server", name, {
        "primitive_count": len(spec.primitives),
    })
    return _to_response(db, _docker_snapshot(name), spec)


# ---- Export / Import ----
# (see app.services.bundle for the archive format and BUNDLE_VERSION)


# ---- Deploy from a Git URL ----

import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def _looks_like_git_url(url: str) -> bool:
    if url.startswith(("git@", "ssh://")):
        return True
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


def _clone_repo(git_url: str, ref: str | None, dest: Path) -> Path:
    """Clone git_url@ref into dest (which must not already exist), strip .git,
    require server.py at the root, and drop any Dockerfile the repo ships
    (Roundhouse generates its own). Returns the path to server.py. Raises
    HTTPException on any failure; the caller owns cleanup of `dest`."""
    git_args = ["git", "clone", "--depth", "1"]
    if ref:
        git_args.extend(["--branch", ref])
    git_args.extend([git_url, str(dest)])
    try:
        result = subprocess.run(git_args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git clone timed out") from None
    if result.returncode != 0:
        raise HTTPException(
            status_code=502, detail=f"git clone failed: {result.stderr.strip()[:500]}"
        )
    git_dir = dest / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)
    server_py = dest / "server.py"
    if not server_py.is_file():
        raise HTTPException(
            status_code=422, detail="Repo does not contain server.py at its root."
        )
    repo_dockerfile = dest / "Dockerfile"
    if repo_dockerfile.is_file():
        repo_dockerfile.unlink()
    return server_py


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    """existing + any incoming items not already present, order preserved."""
    out = list(existing)
    have = set(existing)
    for item in incoming:
        if item not in have:
            have.add(item)
            out.append(item)
    return out


class GitDeployIn(BaseModel):
    name: str
    description: str | None = ""
    git_url: str
    # Optional branch/tag/commit ref. Falls back to the remote's default branch.
    ref: str | None = None
    replicas: int | None = None
    # Swarm node-label placement selectors, validated against existing labels.
    placement_constraints: list[PlacementConstraintIn] = []


@router.post("/from-git", status_code=201)
def deploy_from_git(
    payload: GitDeployIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Clone a git repo and register it as a code-mode server WITHOUT building.

    The repo must contain `server.py` at its root. Dependencies and required
    env vars are declared in a `roundhouse.json` manifest (see git_manifest);
    Roundhouse owns the generated Dockerfile, so any Dockerfile in the repo is
    ignored. The server lands in `not_deployed` state: the operator fills in
    the seeded env vars on the editor, then explicitly deploys."""
    cfg = get_settings()
    name = payload.name
    if not _SERVER_NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid server name")
    if not _looks_like_git_url(payload.git_url):
        raise HTTPException(status_code=422, detail="git_url doesn't look like a git URL")
    if payload.replicas is not None and not (1 <= payload.replicas <= cfg.mcp_max_server_replicas):
        raise HTTPException(
            status_code=422,
            detail=f"replicas must be between 1 and {cfg.mcp_max_server_replicas}",
        )
    placement_constraints = _validate_placement(payload.placement_constraints)

    docker = get_docker()
    service = get_server_service()
    if docker.get_server(name):
        raise HTTPException(status_code=409, detail=f"Server '{name}' already exists")
    _cleanup_orphan_registration(db, name, user)
    if db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
        raise HTTPException(status_code=409, detail=f"Server name '{name}' is already registered")

    # Clone into a throwaway dir; the repo files are snapshotted into the
    # server's build_files (Postgres) and re-materialized at each build.
    from app.services import build_context as buildctx
    tmp = Path(tempfile.mkdtemp(prefix="rh-git-import-"))
    repo = tmp / "repo"
    try:
        try:
            server_py = _clone_repo(payload.git_url, payload.ref, repo)
        except HTTPException:
            raise

        manifest = parse_manifest(repo)

        db.add(ServerOwner(server_name=name, owner_id=user.id))
        db.flush()

        try:
            # Materialize a code-mode spec seeded from the manifest. Env values
            # are empty - the operator fills them before the first deploy.
            spec = ServerSpec(
                name=name,
                description=payload.description or "",
                replicas=payload.replicas,
                mode=MODE_CODE,
                source=server_py.read_text(encoding="utf-8"),
                pip_packages=manifest.pip_packages,
                apt_packages=manifest.apt_packages,
                env_vars=manifest.env_vars,
                git_url=payload.git_url,
                git_ref=payload.ref,
                placement_constraints=placement_constraints,
            )
            service.store.save(spec)
            service.store.set_build_files(name, buildctx.snapshot_dir(repo))
            audit_record(db, user, "server.import_from_git", "server", name, {
                "git_url": payload.git_url, "ref": payload.ref,
            })
            return _to_response(db, _docker_snapshot(name), spec)
        except HTTPException:
            db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
            service.store.delete(name)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("Git import failed for '%s': %s", name, e)
            _log_deploy_failure(user, "server.import_from_git", name, e)
            db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
            service.store.delete(name)
            raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/{name}/update-from-git")
def update_from_git(
    name: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Re-clone the server's git source and merge it into the spec, WITHOUT
    deploying. `server.py` is replaced wholesale; env / pip / apt are merged
    additively - entries the new manifest declares are added if missing, while
    existing entries (and their values) are left untouched. Flags the server
    as needing a redeploy so the operator can fill any new env vars, then
    deploy."""
    _assert_access(db, user, name)
    service = get_server_service()
    spec = service.store.load(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    if not spec.git_url:
        raise HTTPException(
            status_code=409, detail="This server was not imported from Git; nothing to update."
        )

    from app.services import build_context as buildctx
    tmp = Path(tempfile.mkdtemp(prefix="rh-git-update-"))
    try:
        repo = tmp / "repo"
        server_py = _clone_repo(spec.git_url, spec.git_ref, repo)
        manifest = parse_manifest(repo)

        # Replace source wholesale; merge deps/env additively.
        spec.source = server_py.read_text(encoding="utf-8")
        existing_env = {ev.name for ev in spec.env_vars}
        added_env = [ev.name for ev in manifest.env_vars if ev.name not in existing_env]
        spec.env_vars.extend(ev for ev in manifest.env_vars if ev.name not in existing_env)
        spec.pip_packages = _merge_unique(spec.pip_packages, manifest.pip_packages)
        spec.apt_packages = _merge_unique(spec.apt_packages, manifest.apt_packages)

        # Refresh the stored build files (helper modules) from the new clone,
        # then let save_spec persist the spec and flag redeploy; codegen
        # rewrites server.py + Dockerfile at the next build.
        service.save_spec(db, spec)
        service.store.set_build_files(name, buildctx.snapshot_dir(repo))
        audit_record(db, user, "server.update_from_git", "server", name, {
            "git_url": spec.git_url, "ref": spec.git_ref, "added_env": added_env,
        })
        return _to_response(db, _docker_snapshot(name), spec)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Git update failed for '%s': %s", name, e)
        _log_deploy_failure(user, "server.update_from_git", name, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.get("/{name}/export")
def export_spec(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Return a portable zip bundle of the server: manifest.json (the spec),
    assets/, and the git/template build-files tarball. No secrets - runtime
    tokens and apt/pip env values for global imports are NOT included; the
    importer regenerates tokens and reads globals from its own platform."""
    from app.services import bundle
    from app.services.assets import AssetStore

    _assert_access(db, user, name)
    service = get_server_service()
    spec = service.store.load(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    exported = spec.to_dict()
    # Strip ciphertext from secret env vars - it's keyed to this instance's
    # APP_KEY and useless elsewhere. The flag + name survive so the importer
    # has a slot to re-enter the value into.
    exported["env_vars"] = [
        ({**ev, "value": ""} if ev.get("secret") else ev) for ev in exported.get("env_vars", [])
    ]
    astore = AssetStore(name)
    assets = [
        (a["name"], content)
        for a in astore.list()
        if (content := astore.read_bytes(a["name"])) is not None
    ]
    build_files = service.store.get_build_files(name)
    manifest = {
        "version": bundle.BUNDLE_VERSION,
        "exported_at": _now_iso(),
        "spec": exported,
        # Metadata only, for humans inspecting the zip; the entries are canonical.
        "assets": [{"name": n, "size": len(c)} for n, c in assets],
        "has_build_files": bool(build_files),
    }
    content = bundle.build_bundle(manifest, assets, build_files)
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.rhserver.zip"'},
    )


class ImportIn(BaseModel):
    spec: dict
    # If set, override the spec's own `name` field. Useful when the source
    # server already exists locally and you want to clone it under a new name.
    name_override: str | None = None


def _do_import(
    db: Session,
    user: User,
    raw_spec: dict,
    name_override: str | None,
    assets: list[tuple[str, bytes]] | None = None,
    build_files: bytes | None = None,
):
    """Shared import flow: register ownership, persist spec (+ assets and
    build files when importing a bundle), then build and deploy. Rolls the
    registration and stored state back on any failure."""
    from app.services.assets import AssetError, AssetStore

    raw_spec = dict(raw_spec)
    if name_override:
        raw_spec["name"] = name_override
    name = str(raw_spec.get("name") or "")
    if not _SERVER_NAME_RE.match(name):
        raise HTTPException(status_code=422, detail=f"Invalid or missing server name: {name!r}")

    docker = get_docker()
    if docker.get_server(name):
        raise HTTPException(status_code=409, detail=f"Server '{name}' already exists")
    _cleanup_orphan_registration(db, name, user)
    if db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
        raise HTTPException(status_code=409, detail=f"Server name '{name}' is already registered")

    db.add(ServerOwner(server_name=name, owner_id=user.id))
    db.flush()

    spec = ServerSpec.from_dict(raw_spec)
    service = get_server_service()

    def _rollback() -> None:
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        service.store.delete(name)  # also removes assets + build files

    try:
        # Assets and build files must be in the DB before build_and_deploy
        # materializes the build context from it. save() first so the server
        # row exists for set_build_files.
        if assets or build_files:
            service.store.save(spec)
            astore = AssetStore(name)
            for filename, content in assets or []:
                astore.write(filename, content)  # re-enforces caps + charset
            if build_files:
                service.store.set_build_files(name, build_files)
        result = service.build_and_deploy(db, spec)
        audit_record(db, user, "server.import", "server", name, {
            "assets": len(assets or []),
            "build_files": bool(build_files),
        })
        return _to_response(db, result, spec)
    except AssetError as e:
        _rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except HTTPException:
        _rollback()
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Import failed for '%s': %s", name, e)
        _log_deploy_failure(user, "server.import", name, e)
        _rollback()
        try:
            docker.remove_server(name, service.registry_prefix(db))
        except Exception:  # noqa: BLE001
            pass
        status_code = 409 if isinstance(e, RegistryRequiredError) else 500
        raise HTTPException(status_code=status_code, detail=str(e)) from e


@router.post("/import", status_code=201)
def import_spec(
    payload: ImportIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Legacy JSON import: a bare spec (or v1 export envelope's `spec`).
    Bundles exported as zips go through /import-archive instead."""
    return _do_import(db, user, payload.spec, payload.name_override)


@router.post("/import-archive", status_code=201)
async def import_archive(
    file: UploadFile = FastApiFile(...),
    name_override: str | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Import a zip bundle produced by GET /{name}/export: spec + assets +
    build files. Bounded read so an oversize upload can't exhaust memory
    before the size check."""
    from app.services import bundle

    data = await file.read(bundle.MAX_BUNDLE_BYTES + 1)
    if len(data) > bundle.MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Bundle exceeds the {bundle.MAX_BUNDLE_BYTES}-byte limit",
        )
    try:
        parsed = bundle.parse_bundle(data)
    except bundle.BundleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _do_import(
        db, user, parsed.manifest["spec"], name_override,
        assets=parsed.assets, build_files=parsed.build_files,
    )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def destroy(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access(db, user, name)
    service = get_server_service()
    docker = get_docker()
    removed = False
    try:
        removed = docker.remove_server(name, service.registry_prefix(db))
    except Exception as e:  # noqa: BLE001
        logger.warning("Error removing docker server '%s': %s", name, e)
    if not removed:
        if not db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
            raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
        logger.warning("Clearing orphaned registration for '%s' (no Docker service to remove)", name)
    service.store.delete(name)
    db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
    audit_record(db, user, "server.delete", "server", name)


# ---- Spec mutations ----

def _save_and_respond(db: Session, spec: ServerSpec) -> dict:
    try:
        get_server_service().save_spec(db, spec)
        snap = _docker_snapshot(spec.name)
        return _to_response(db, snap, spec)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Save failed for '%s': %s", spec.name, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class ReplicasIn(BaseModel):
    replicas: int = Field(ge=1)


@router.put("/{name}/replicas")
def update_replicas(
    name: str,
    payload: ReplicasIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_settings()
    if payload.replicas > cfg.mcp_max_server_replicas:
        raise HTTPException(
            status_code=422,
            detail=f"replicas must be ≤ {cfg.mcp_max_server_replicas}",
        )
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.replicas = payload.replicas
    service = get_server_service()
    service.store.save(spec)

    docker = get_docker()
    if docker.supports_scaling():
        running = docker.get_server(name)
        if running and running.get("status") == "running":
            docker.scale_server(name, spec.replicas)

    snap = docker.get_server(name) or _missing_snapshot(name)
    return _to_response(db, snap, spec)


class ResourceLimitsIn(BaseModel):
    # 0 / null clears the limit. cpu is whole CPUs (0.5 = half).
    cpu_limit: float | None = None
    memory_limit_mb: int | None = None


@router.put("/{name}/resources")
def update_resources(
    name: str,
    payload: ResourceLimitsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.cpu_limit = payload.cpu_limit if (payload.cpu_limit and payload.cpu_limit > 0) else None
    spec.memory_limit_mb = (
        payload.memory_limit_mb if (payload.memory_limit_mb and payload.memory_limit_mb > 0) else None
    )
    # Resource changes don't take effect until next deploy - flag it so the
    # editor surfaces the redeploy banner like a spec change.
    from app.services import server_auth as _sa
    get_server_service().store.save(spec)
    _sa.mark_redeploy_required(db, name)
    snap = get_docker().get_server(name) or _missing_snapshot(name)
    return _to_response(db, snap, spec)


class PlacementIn(BaseModel):
    placement_constraints: list[PlacementConstraintIn] = []


@router.put("/{name}/placement")
def update_placement(
    name: str,
    payload: PlacementIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.placement_constraints = _validate_placement(payload.placement_constraints)
    # Placement is baked into the service spec at create time, so a change only
    # takes effect on the next deploy - flag it like other spec edits.
    from app.services import server_auth as _sa
    get_server_service().store.save(spec)
    _sa.mark_redeploy_required(db, name)
    snap = get_docker().get_server(name) or _missing_snapshot(name)
    return _to_response(db, snap, spec)


# ---- Assets ----

@router.get("/{name}/assets")
def list_assets(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.services.assets import AssetStore, MAX_FILE_BYTES, MAX_TOTAL_BYTES
    _assert_access(db, user, name)
    store = AssetStore(name)
    return {
        "assets": store.list(),
        "total_size": store.total_size(),
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
    }


@router.get("/{name}/assets/{filename}")
def download_asset(
    name: str,
    filename: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Stream an asset back to the caller as a download. Filename is run
    through the same safe-charset gate the upload uses; no-cache so
    collaborators always pull the current version."""
    from fastapi.responses import Response as RawResponse
    from app.services.assets import AssetStore, AssetError
    _assert_access(db, user, name)
    store = AssetStore(name)
    try:
        data = store.read_bytes(filename)
    except AssetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if data is None:
        raise HTTPException(status_code=404, detail=f"Asset {filename!r} not found")
    return RawResponse(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/{name}/assets", status_code=201)
async def upload_asset(
    name: str,
    file: UploadFile = FastApiFile(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Multipart upload of a single asset. Bounded read so an oversize
    upload can't exhaust memory before the size check."""
    from app.services.assets import AssetStore, AssetError, MAX_FILE_BYTES
    from app.services import server_auth as _sa
    _assert_access(db, user, name)
    # Read one byte past the cap so we can distinguish at-limit from over.
    payload = await file.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Asset exceeds {MAX_FILE_BYTES}-byte per-file cap",
        )
    store = AssetStore(name)
    try:
        record = store.write(file.filename or "", payload)
    except AssetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _sa.mark_redeploy_required(db, name)
    audit_record(db, user, "asset.upload", "server", name)
    return record


@router.delete("/{name}/assets/{filename}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    name: str,
    filename: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.services.assets import AssetStore, AssetError
    from app.services import server_auth as _sa
    _assert_access(db, user, name)
    store = AssetStore(name)
    try:
        removed = store.delete(filename)
    except AssetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail=f"Asset {filename!r} not found")
    _sa.mark_redeploy_required(db, name)
    audit_record(db, user, "asset.delete", "server", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DescriptionIn(BaseModel):
    description: str


@router.put("/{name}/description")
def update_description(
    name: str,
    payload: DescriptionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.description = payload.description
    get_server_service().store.save(spec)
    snap = get_docker().get_server(name) or _missing_snapshot(name)
    return _to_response(db, snap, spec)


class SourceIn(BaseModel):
    source: str


@router.put("/{name}/source")
def update_source(
    name: str,
    payload: SourceIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    if not spec.is_code_mode():
        raise HTTPException(
            status_code=409,
            detail="Cannot set source on a structured server - use the primitive editor instead.",
        )
    spec.source = payload.source
    return _save_and_respond(db, spec)


class PrimitiveIn(BaseModel):
    kind: Literal["tool", "resource", "resource_template", "prompt"]
    name: str
    description: str | None = ""
    code: str | None = ""
    return_type: Literal["str", "dict"] | None = None
    parameters: list[dict] | None = None
    uri: str | None = None
    uri_template: str | None = None
    mime_type: str | None = None
    scopes: list[str] | None = None
    middleware: dict | None = None


class AddPrimitiveIn(BaseModel):
    primitive: PrimitiveIn


def _primitive_to_dict(p: PrimitiveIn) -> dict:
    """Strip None-valued fields so we don't pollute the on-disk spec with
    explicit nulls."""
    raw = p.model_dump(exclude_none=True)
    # Sanity-bound scopes to plain string list.
    if "scopes" in raw and isinstance(raw["scopes"], list):
        raw["scopes"] = [s for s in raw["scopes"] if isinstance(s, str)][:128]
    return raw


@router.post("/{name}/primitives")
def add_primitive(
    name: str,
    payload: AddPrimitiveIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    _assert_scopes_exist(db, name, payload.primitive.scopes or [])
    spec = _ensure_spec(db, name)
    _assert_structured(spec, "add primitives")
    for p in spec.primitives:
        if p.get("name") == payload.primitive.name and p.get("kind") == payload.primitive.kind:
            raise HTTPException(
                status_code=409,
                detail=f"{payload.primitive.kind} '{payload.primitive.name}' already exists",
            )
    spec.primitives.append(_primitive_to_dict(payload.primitive))
    return _save_and_respond(db, spec)


@router.put("/{name}/primitives/{prim_name}")
def update_primitive(
    name: str,
    prim_name: str,
    payload: AddPrimitiveIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    _assert_scopes_exist(db, name, payload.primitive.scopes or [])
    spec = _ensure_spec(db, name)

    idx = next(
        (i for i, p in enumerate(spec.primitives) if p.get("name") == prim_name), None
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")
    if spec.is_proxied():
        # Discovered primitives are an overlay: their schema comes from the
        # upstream and is read-only here. Only operator-assigned scopes (and
        # optional middleware) are editable - never overwrite the discovered body.
        existing = spec.primitives[idx]
        existing["scopes"] = [s for s in (payload.primitive.scopes or []) if isinstance(s, str)][:128]
        if payload.primitive.middleware is not None:
            existing["middleware"] = payload.primitive.middleware
    else:
        _assert_structured(spec, "update primitives")
        spec.primitives[idx] = _primitive_to_dict(payload.primitive)
    return _save_and_respond(db, spec)


@router.delete("/{name}/primitives/{prim_name}")
def delete_primitive(
    name: str,
    prim_name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    _assert_structured(spec, "delete primitives")
    before = len(spec.primitives)
    spec.primitives = [p for p in spec.primitives if p.get("name") != prim_name]
    if len(spec.primitives) == before:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")
    return _save_and_respond(db, spec)


@router.delete("/{name}/primitives-archived")
def clear_archived_primitives(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Permanently remove all primitives a prior rediscovery marked archived
    (vanished upstream). Unlike delete_primitive this is allowed on proxied
    servers - that's where archived primitives come from - and archived entries
    are already excluded from the live toolset, so this only clears the
    management view."""
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    before = len(spec.primitives)
    spec.primitives = discovery.drop_archived(spec.primitives)
    removed = before - len(spec.primitives)
    if removed:
        audit_record(db, user, "server.clear_archived", "server", name, {
            "removed": removed,
        })
    return _save_and_respond(db, spec)


class PackagesIn(BaseModel):
    pip_packages: list[str]


@router.put("/{name}/packages")
def update_packages(
    name: str,
    payload: PackagesIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.pip_packages = list(payload.pip_packages)
    return _save_and_respond(db, spec)


class AptPackagesIn(BaseModel):
    apt_packages: list[str]


@router.put("/{name}/apt-packages")
def update_apt_packages(
    name: str,
    payload: AptPackagesIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    for p in payload.apt_packages:
        if not _APT_NAME_RE.match(p):
            raise HTTPException(status_code=422, detail=f"Invalid apt package name: {p}")
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.apt_packages = list(payload.apt_packages)
    return _save_and_respond(db, spec)


class EnvVarItem(BaseModel):
    name: str
    # Plain rows: literal value. Secret rows: the plaintext to encrypt-and-
    # store, or null/"" to preserve whatever ciphertext is already on disk.
    value: str | None = ""
    secret: bool = False


class EnvIn(BaseModel):
    env_global_imports: list[str] = []
    env_vars: list[EnvVarItem] = []


def _encrypt_env(plaintext: str) -> str:
    """Encrypt a secret env value for at-rest storage. Falls back to
    plaintext when APP_KEY isn't configured (dev mode) so we don't fail
    closed - the secret flag is still preserved so the UI keeps masking."""
    from app.config import get_settings
    from app.crypto import encrypt
    app_key = get_settings().app_key
    if not app_key:
        return plaintext
    return encrypt(plaintext, app_key)


def _merge_env_vars(items: list[EnvVarItem], existing: list[EnvVar]) -> list[EnvVar]:
    """Build the new env_vars list. For secret rows where the client omitted
    a fresh value, preserve the ciphertext already on disk so the UI never
    has to re-collect the plaintext."""
    by_name = {ev.name: ev for ev in existing}
    out: list[EnvVar] = []
    for it in items:
        name = (it.name or "").strip()
        if not name:
            continue
        prev = by_name.get(name)
        if it.secret:
            if it.value:
                out.append(EnvVar(name=name, value=_encrypt_env(it.value), secret=True))
            elif prev is not None and prev.secret:
                out.append(EnvVar(name=name, value=prev.value, secret=True))
            # Else: secret toggle flipped on with no value yet -> skip.
        elif not it.value and prev is not None and prev.secret:
            # Guardrail: the client sent a previously-secret row back with no
            # value AND without the secret flag. That happens when a masked
            # secret field round-trips blank (the API never echoes the value).
            # Treating it as a plaintext "" would silently WIPE the stored
            # secret on the next deploy - so preserve what's on disk instead.
            # A genuine delete removes the row entirely, so it never lands here.
            out.append(EnvVar(name=name, value=prev.value, secret=True))
        else:
            out.append(EnvVar(name=name, value=it.value or "", secret=False))
    return out


@router.put("/{name}/env")
def update_env(
    name: str,
    payload: EnvIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    spec.env_global_imports = normalize_env_imports(payload.env_global_imports)
    spec.env_vars = _merge_env_vars(payload.env_vars, spec.env_vars)
    return _save_and_respond(db, spec)


class ConfigIn(BaseModel):
    imports: list[str] = []
    pip_packages: list[str] = []
    apt_packages: list[str] = []
    env_global_imports: list[str] = []
    env_vars: list[EnvVarItem] = []


@router.put("/{name}/config")
def update_config(
    name: str,
    payload: ConfigIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    for p in payload.apt_packages:
        if not _APT_NAME_RE.match(p):
            raise HTTPException(status_code=422, detail=f"Invalid apt package name: {p}")
    _assert_access(db, user, name)
    spec = _ensure_spec(db, name)
    _assert_structured(spec, "update config (imports)")
    spec.imports = list(payload.imports)
    spec.pip_packages = list(payload.pip_packages)
    spec.apt_packages = list(payload.apt_packages)
    spec.env_global_imports = normalize_env_imports(payload.env_global_imports)
    spec.env_vars = _merge_env_vars(payload.env_vars, spec.env_vars)
    return _save_and_respond(db, spec)
