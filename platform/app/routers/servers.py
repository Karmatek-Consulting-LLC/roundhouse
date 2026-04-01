from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.codegen import write_build_context
from app.config import (
    DEFAULT_MCP_SERVER_REPLICAS,
    MAX_MCP_SERVER_REPLICAS,
    SERVERS_DATA_DIR,
)
from app.database import get_db
from app.db_models import ServerOwner, User
from app.docker_manager import DockerManager
from app.routers.settings import get_base_url, get_docker_registry_auth, get_docker_registry_prefix
from app.models import (
    AddPrimitiveRequest,
    CreateServerRequest,
    PlacementTask,
    ServerResponse,
    ServerSpec,
    TemplateResponse,
    UpdateConfigRequest,
    UpdateEnvVarsRequest,
    UpdatePipPackagesRequest,
    UpdateReplicasRequest,
)
from app.permissions import can_access_server, get_accessible_server_names
from app.server_store import ServerStore
from app.template_engine import TemplateEngine

logger = logging.getLogger(__name__)
router = APIRouter()

docker_mgr = DockerManager()
template_engine = TemplateEngine()
store = ServerStore()


def effective_replicas(spec: ServerSpec | None) -> int:
    if spec is None:
        return DEFAULT_MCP_SERVER_REPLICAS
    if spec.replicas is not None:
        return spec.replicas
    return DEFAULT_MCP_SERVER_REPLICAS


def _missing_docker_snapshot(name: str) -> dict:
    """Synthetic server dict when registered in DB but no Docker service/container."""
    return {
        "name": name,
        "template": "custom",
        "status": "not_deployed",
        "created_at": "",
        "replicas_running": 0,
        "placement": [],
    }


def _unknown_docker_snapshot(name: str) -> dict:
    """Docker API failed while resolving this server."""
    return {
        "name": name,
        "template": "custom",
        "status": "unknown",
        "created_at": "",
        "replicas_running": 0,
        "placement": [],
    }


def _docker_snapshot_for_server(name: str) -> dict:
    try:
        d = docker_mgr.get_server(name)
    except Exception as e:
        logger.warning("Docker get_server failed for %s: %s", name, e)
        return _unknown_docker_snapshot(name)
    if d is None:
        return _missing_docker_snapshot(name)
    return d


def _registered_server_names_for_user(user: User, db: Session) -> list[str]:
    """All server names the user may see (DB source of truth)."""
    if user.role == "superadmin":
        rows = (
            db.query(ServerOwner.server_name)
            .order_by(ServerOwner.server_name)
            .all()
        )
        return [r[0] for r in rows]
    return sorted(get_accessible_server_names(user, db))


def _to_response(
    server: dict, spec: ServerSpec | None = None, db: Session | None = None
) -> ServerResponse:
    owner_id = None
    owner_email = None
    if db:
        so = db.query(ServerOwner).filter(
            ServerOwner.server_name == server["name"]
        ).first()
        if so:
            owner_id = str(so.owner_id)
            if so.owner:
                owner_email = so.owner.email

    raw_placement = server.get("placement") or []
    placement = [PlacementTask(**p) for p in raw_placement]

    return ServerResponse(
        name=server["name"],
        template=server["template"],
        status=server["status"],
        url=f"{get_base_url(db) if db else ''}/s/{server['name']}/mcp",
        description=spec.description if spec else "",
        imports=spec.imports if spec else [],
        primitives=spec.primitives if spec else [],
        pip_packages=spec.pip_packages if spec else [],
        env_vars=spec.env_vars if spec else [],
        owner_id=owner_id,
        owner_email=owner_email,
        created_at=server.get("created_at"),
        replicas_desired=effective_replicas(spec),
        replicas_running=int(server.get("replicas_running", 0)),
        docker_swarm_mode=docker_mgr.swarm_mode,
        placement=placement,
    )


def _env_dict(spec: ServerSpec) -> dict[str, str]:
    return {ev.name: ev.value for ev in spec.env_vars}


def _registry_prefix(db: Session) -> str | None:
    return get_docker_registry_prefix(db)


def _build_and_deploy(spec: ServerSpec, db: Session) -> dict:
    build_ctx = write_build_context(spec, SERVERS_DATA_DIR / spec.name)
    store.save(spec)
    return docker_mgr.build_and_start(
        spec.name,
        build_ctx,
        "custom",
        env_vars=_env_dict(spec),
        replicas=effective_replicas(spec),
        registry_prefix=_registry_prefix(db),
        registry_auth=get_docker_registry_auth(db),
    )


def _redeploy(spec: ServerSpec, db: Session) -> dict:
    docker_mgr.remove_server(spec.name, registry_prefix=_registry_prefix(db))
    return _build_and_deploy(spec, db)


def _ensure_spec(name: str, db: Session) -> ServerSpec:
    spec = store.load(name)
    if spec is not None:
        return spec
    if docker_mgr.get_server(name):
        spec = ServerSpec(name=name)
        store.save(spec)
        return spec
    if db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
        spec = ServerSpec(name=name)
        store.save(spec)
        return spec
    raise HTTPException(status_code=404, detail=f"Server '{name}' not found")


def _check_access(user: User, server_name: str, db: Session) -> None:
    if not can_access_server(user, server_name, db):
        raise HTTPException(status_code=403, detail="Access denied")


# --- Templates ---


@router.get("/templates", response_model=list[TemplateResponse])
def list_templates():
    return template_engine.list_templates()


@router.get("/templates/{name}", response_model=TemplateResponse)
def get_template(name: str):
    tmpl = template_engine.get_template(name)
    if not tmpl:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    return tmpl


# --- Servers ---


@router.get("/servers", response_model=list[ServerResponse])
def list_servers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    names = _registered_server_names_for_user(user, db)
    results: list[ServerResponse] = []
    for name in names:
        snap = _docker_snapshot_for_server(name)
        spec = store.load(name)
        results.append(_to_response(snap, spec, db))
    return results


@router.get("/servers/limits")
def server_replica_limits(user: User = Depends(get_current_user)):
    """Docker/Swarm replica defaults for the UI (any authenticated user)."""
    return {
        "default_mcp_server_replicas": DEFAULT_MCP_SERVER_REPLICAS,
        "max_mcp_server_replicas": MAX_MCP_SERVER_REPLICAS,
        "docker_swarm_mode": docker_mgr.swarm_mode,
    }


@router.get("/servers/{name}", response_model=ServerResponse)
def get_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    snap = _docker_snapshot_for_server(name)
    spec = store.load(name)
    return _to_response(snap, spec, db)


@router.get("/servers/{name}/logs")
def get_server_logs(
    name: str,
    tail: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    if not docker_mgr.get_server(name):
        raise HTTPException(
            status_code=404,
            detail=f"No Docker service for '{name}'; logs are unavailable until deployed.",
        )
    try:
        text = docker_mgr.get_server_logs(name, tail=tail)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to read logs for server '%s'", name)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


def _can_clear_orphan_ownership(user: User, server_name: str, db: Session) -> bool:
    """Only the recorded owner or superadmin may remove a stale server_owners row."""
    if user.role == "superadmin":
        return True
    row = db.query(ServerOwner).filter(ServerOwner.server_name == server_name).first()
    return row is not None and row.owner_id == user.id


def _cleanup_orphan_server_registration(server_name: str, user: User, db: Session) -> None:
    """If Docker has no server but Postgres still has ownership (failed deploy), remove the row."""
    if docker_mgr.get_server(server_name):
        return
    orphan = db.query(ServerOwner).filter(ServerOwner.server_name == server_name).first()
    if not orphan:
        return
    if not _can_clear_orphan_ownership(user, server_name, db):
        raise HTTPException(
            status_code=403,
            detail=f"Server name '{server_name}' is already registered to another user",
        )
    logger.warning(
        "Removing orphaned server_owners row for '%s' (no matching Docker service)",
        server_name,
    )
    db.delete(orphan)
    db.commit()
    store.delete(server_name)


@router.post("/servers", response_model=ServerResponse, status_code=201)
def create_server(
    req: CreateServerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if docker_mgr.get_server(req.name):
        raise HTTPException(status_code=409, detail=f"Server '{req.name}' already exists")

    _cleanup_orphan_server_registration(req.name, user, db)

    try:
        if req.template:
            if not template_engine.get_template(req.template):
                raise HTTPException(
                    status_code=404, detail=f"Template '{req.template}' not found"
                )
            build_context = template_engine.render(req.template, req.name, req.config)
            spec = ServerSpec(
                name=req.name, description=req.description, replicas=req.replicas
            )
            store.save(spec)
            server = docker_mgr.build_and_start(
                req.name,
                build_context,
                req.template,
                replicas=effective_replicas(spec),
                registry_prefix=_registry_prefix(db),
                registry_auth=get_docker_registry_auth(db),
            )
        else:
            spec = ServerSpec(
                name=req.name, description=req.description, replicas=req.replicas
            )
            server = _build_and_deploy(spec, db)

        # Record ownership
        db.add(ServerOwner(server_name=req.name, owner_id=user.id))
        db.commit()

        return _to_response(server, spec, db)
    except HTTPException:
        raise
    except Exception as e:
        store.delete(req.name)
        docker_mgr.remove_server(req.name, registry_prefix=_registry_prefix(db))
        logger.exception("Failed to create server '%s'", req.name)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{name}/start", response_model=ServerResponse)
def start_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    if docker_mgr.get_server(name) is None:
        raise HTTPException(
            status_code=400,
            detail="Server has no Docker service to start. Deploy configuration from the server details page first.",
        )
    spec = store.load(name)
    n = effective_replicas(spec) if spec else DEFAULT_MCP_SERVER_REPLICAS
    server = docker_mgr.start_server(name, replicas=n)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    if spec is None:
        spec = _ensure_spec(name, db)
    return _to_response(server, spec, db)


@router.post("/servers/{name}/stop", response_model=ServerResponse)
def stop_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    if docker_mgr.get_server(name) is None:
        raise HTTPException(
            status_code=400,
            detail="Server is not deployed to Docker; nothing to stop.",
        )
    server = docker_mgr.stop_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec, db)


@router.post("/servers/{name}/redeploy", response_model=ServerResponse)
def redeploy_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate server.py from the saved spec, rebuild the image, and replace the service."""
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to redeploy '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/servers/{name}", status_code=204)
def delete_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    removed = docker_mgr.remove_server(name, registry_prefix=_registry_prefix(db))
    if not removed:
        if not db.query(ServerOwner).filter(ServerOwner.server_name == name).first():
            raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
        logger.warning(
            "Clearing orphaned registration for '%s' (no Docker service to remove)",
            name,
        )
    store.delete(name)
    db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
    db.commit()


# --- Description ---


class UpdateDescriptionRequest(BaseModel):
    description: str


@router.put("/servers/{name}/replicas", response_model=ServerResponse)
def update_replicas(
    name: str,
    req: UpdateReplicasRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    spec.replicas = req.replicas
    store.save(spec)
    if docker_mgr.swarm_mode:
        running = docker_mgr.get_server(name)
        if running and running.get("status") == "running":
            docker_mgr.scale_server(name, req.replicas)
    server = docker_mgr.get_server(name)
    if not server:
        return _to_response(_missing_docker_snapshot(name), spec, db)
    return _to_response(server, spec, db)


@router.put("/servers/{name}/description", response_model=ServerResponse)
def update_description(
    name: str,
    req: UpdateDescriptionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    spec.description = req.description
    store.save(spec)
    server = docker_mgr.get_server(name)
    if not server:
        return _to_response(_missing_docker_snapshot(name), spec, db)
    return _to_response(server, spec, db)


# --- Primitives ---


@router.post("/servers/{name}/primitives", response_model=ServerResponse, status_code=201)
def add_primitive(
    name: str,
    req: AddPrimitiveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)

    for p in spec.primitives:
        if p.name == req.primitive.name and p.kind == req.primitive.kind:
            raise HTTPException(
                status_code=409,
                detail=f"{req.primitive.kind} '{req.primitive.name}' already exists",
            )

    spec.primitives.append(req.primitive)

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to add primitive to '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/servers/{name}/primitives/{prim_name}", response_model=ServerResponse)
def update_primitive(
    name: str,
    prim_name: str,
    req: AddPrimitiveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)

    idx = next(
        (i for i, p in enumerate(spec.primitives) if p.name == prim_name),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    spec.primitives[idx] = req.primitive

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to update primitive on '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/servers/{name}/primitives/{prim_name}", response_model=ServerResponse)
def delete_primitive(
    name: str,
    prim_name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)

    original_len = len(spec.primitives)
    spec.primitives = [p for p in spec.primitives if p.name != prim_name]

    if len(spec.primitives) == original_len:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to delete primitive from '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/servers/{name}/packages", response_model=ServerResponse)
def update_pip_packages(
    name: str,
    req: UpdatePipPackagesRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    spec.pip_packages = req.pip_packages

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to update packages for '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


# --- Environment Variables ---


@router.put("/servers/{name}/env", response_model=ServerResponse)
def update_env_vars(
    name: str,
    req: UpdateEnvVarsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    spec.env_vars = req.env_vars

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to update env vars for '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


# --- Config (packages + env vars in one deploy) ---


@router.put("/servers/{name}/config", response_model=ServerResponse)
def update_config(
    name: str,
    req: UpdateConfigRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name, db)
    spec.imports = req.imports
    spec.pip_packages = req.pip_packages
    spec.env_vars = req.env_vars

    try:
        server = _redeploy(spec, db)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to update config for '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))
