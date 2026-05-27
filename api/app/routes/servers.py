from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.config import get_settings
from app.db import get_db
from app.deps import current_user
from app.models import ServerOwner, ServerScope, User
from app.services import global_env, permissions
from app.services.docker import DockerError, DockerNotFoundError, get_docker
from app.services.server_service import get_server_service
from app.services.spec import (
    MODE_CODE,
    MODE_STRUCTURED,
    EnvVar,
    ServerSpec,
    normalize_env_imports,
)

router = APIRouter(prefix="/api/servers", tags=["servers"])
logger = logging.getLogger(__name__)


# ---- Helpers ----

_SERVER_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
_APT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+._:=~-]*$")


def _assert_access(db: Session, user: User, name: str) -> None:
    if not permissions.can_access(db, user, name):
        raise HTTPException(status_code=403, detail="Access denied")


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


def _to_response(db: Session, snap: dict, spec: ServerSpec | None) -> dict:
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
        "health": snap.get("health"),
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
        "docker_swarm_mode": get_docker().swarm_mode(),
        "placement": snap.get("placement") or [],
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
    out: list[dict] = []
    for name in _registered_names_for_user(db, user):
        snap = _docker_snapshot(name)
        spec = service.store.load(name)
        out.append(_to_response(db, snap, spec))
    return out


@router.get("/limits")
def limits(_: User = Depends(current_user)):
    cfg = get_settings()
    return {
        "default_mcp_server_replicas": cfg.mcp_default_server_replicas,
        "max_mcp_server_replicas": cfg.mcp_max_server_replicas,
        "docker_swarm_mode": get_docker().swarm_mode(),
    }


@router.get("/{name}")
def show(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access(db, user, name)
    snap = _docker_snapshot(name)
    spec = get_server_service().store.load(name)
    return _to_response(db, snap, spec)


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
    url = f"http://mcp-{name}:8000/metrics"
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

class CreateServerIn(BaseModel):
    name: str
    description: str | None = ""
    template: str | None = None
    config: dict[str, Any] = {}
    replicas: int | None = None
    mode: Literal["structured", "code"] = MODE_STRUCTURED
    source: str | None = None


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
        )

        if payload.template:
            tmpl = service.templates.get_template(payload.template)
            if tmpl is None:
                raise HTTPException(
                    status_code=404, detail=f"Template '{payload.template}' not found"
                )
            build_context = service.templates.render(payload.template, name, payload.config)
            service.store.save(spec)
            result = docker.build_and_start(
                server_name=name,
                build_context=build_context,
                template_name=payload.template,
                env_vars=service.effective_env(db, spec),
                replicas=service.effective_replicas(spec),
                registry_prefix=service.registry_prefix(db),
                registry_auth=service.registry_auth(db),
            )
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
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        service.store.delete(name)
        try:
            docker.remove_server(name, service.registry_prefix(db))
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---- Export / Import ----

EXPORT_VERSION = 1


# ---- Deploy from a Git URL ----

import shutil
import subprocess
from urllib.parse import urlparse


def _looks_like_git_url(url: str) -> bool:
    if url.startswith(("git@", "ssh://")):
        return True
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


class GitDeployIn(BaseModel):
    name: str
    description: str | None = ""
    git_url: str
    # Optional branch/tag/commit ref. Falls back to the remote's default branch.
    ref: str | None = None
    replicas: int | None = None


@router.post("/from-git", status_code=201)
def deploy_from_git(
    payload: GitDeployIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Clone a git repo and deploy it as a code-mode server. The repo must
    contain `server.py` at its root (and may include a `Dockerfile`; if
    absent, codegen synthesizes one from any pip_packages declared in the
    spec - but for git-deploy the simplest path is: bring your own
    Dockerfile)."""
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

    docker = get_docker()
    service = get_server_service()
    if docker.get_server(name):
        raise HTTPException(status_code=409, detail=f"Server '{name}' already exists")
    _cleanup_orphan_registration(db, name, user)
    if db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
        raise HTTPException(status_code=409, detail=f"Server name '{name}' is already registered")

    # Clone into the server's dedicated context dir so codegen / store layout matches.
    server_dir = service.store.server_dir(name)
    if server_dir.exists():
        shutil.rmtree(server_dir)

    git_args = ["git", "clone", "--depth", "1"]
    if payload.ref:
        git_args.extend(["--branch", payload.ref])
    git_args.extend([payload.git_url, str(server_dir)])

    try:
        result = subprocess.run(git_args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git clone timed out") from None
    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"git clone failed: {result.stderr.strip()[:500]}",
        )

    # Drop the .git directory before tar-ing the context - keeps the image
    # smaller and avoids embedding the repo history in the deployable.
    git_dir = server_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)

    server_py = server_dir / "server.py"
    if not server_py.is_file():
        shutil.rmtree(server_dir, ignore_errors=True)
        raise HTTPException(
            status_code=422,
            detail="Repo does not contain server.py at its root.",
        )
    has_dockerfile = (server_dir / "Dockerfile").is_file()

    db.add(ServerOwner(server_name=name, owner_id=user.id))
    db.flush()

    # Materialize a code-mode spec so the editor knows what this is. We
    # don't blow away the cloned files - just save the spec alongside.
    spec = ServerSpec(
        name=name,
        description=payload.description or "",
        replicas=payload.replicas,
        mode=MODE_CODE,
        source=server_py.read_text(encoding="utf-8"),
    )
    service.store.save(spec)

    try:
        if not has_dockerfile:
            # No Dockerfile in the repo - let codegen drop a default one in.
            from app.services.codegen import generate_dockerfile
            (server_dir / "Dockerfile").write_text(
                generate_dockerfile(spec, service.custom_ca_cert(db)),
                encoding="utf-8",
            )
        result = docker.build_and_start(
            server_name=name,
            build_context=server_dir,
            template_name="git",
            env_vars=service.effective_env(db, spec),
            replicas=service.effective_replicas(spec),
            registry_prefix=service.registry_prefix(db),
            registry_auth=service.registry_auth(db),
        )
        audit_record(db, user, "server.deploy_from_git", "server", name, {
            "git_url": payload.git_url, "ref": payload.ref,
        })
        return _to_response(db, result, spec)
    except HTTPException:
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Git deploy failed for '%s': %s", name, e)
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        service.store.delete(name)
        try:
            docker.remove_server(name, service.registry_prefix(db))
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{name}/export")
def export_spec(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Return a portable JSON dump of the server's spec. No secrets - runtime
    tokens and apt/pip env values for global imports are NOT included; the
    importer regenerates tokens and reads globals from its own platform."""
    _assert_access(db, user, name)
    spec = get_server_service().store.load(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    exported = spec.to_dict()
    # Strip ciphertext from secret env vars - it's keyed to this instance's
    # APP_KEY and useless elsewhere. The flag + name survive so the importer
    # has a slot to re-enter the value into.
    exported["env_vars"] = [
        ({**ev, "value": ""} if ev.get("secret") else ev) for ev in exported.get("env_vars", [])
    ]
    return {
        "version": EXPORT_VERSION,
        "exported_at": _now_iso(),
        "spec": exported,
    }


class ImportIn(BaseModel):
    spec: dict
    # If set, override the spec's own `name` field. Useful when the source
    # server already exists locally and you want to clone it under a new name.
    name_override: str | None = None


@router.post("/import", status_code=201)
def import_spec(
    payload: ImportIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    raw_spec = dict(payload.spec)
    if payload.name_override:
        raw_spec["name"] = payload.name_override
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
    try:
        result = service.build_and_deploy(db, spec)
        audit_record(db, user, "server.import", "server", name)
        return _to_response(db, result, spec)
    except HTTPException:
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Import failed for '%s': %s", name, e)
        db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
        service.store.delete(name)
        try:
            docker.remove_server(name, service.registry_prefix(db))
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=str(e)) from e


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
    if docker.swarm_mode():
        running = docker.get_server(name)
        if running and running.get("status") == "running":
            docker.scale_server(name, spec.replicas)

    snap = docker.get_server(name) or _missing_snapshot(name)
    return _to_response(db, snap, spec)


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
    """Strip None-valued fields the way the Laravel validator did so we don't
    pollute the on-disk spec with explicit nulls."""
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
    _assert_structured(spec, "update primitives")

    idx = next(
        (i for i, p in enumerate(spec.primitives) if p.get("name") == prim_name), None
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")
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
    from app.laravel_crypto import encrypt
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
        if it.secret:
            if it.value:
                out.append(EnvVar(name=name, value=_encrypt_env(it.value), secret=True))
            else:
                prev = by_name.get(name)
                if prev is not None and prev.secret:
                    out.append(EnvVar(name=name, value=prev.value, secret=True))
                # Else: secret toggle flipped on with no value yet -> skip,
                # matches Laravel behavior (no row written).
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
