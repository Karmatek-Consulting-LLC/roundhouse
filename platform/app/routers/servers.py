from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.codegen import write_build_context
from app.config import MCP_BASE_URL, SERVERS_DATA_DIR
from app.database import get_db
from app.db_models import ServerOwner, User
from app.docker_manager import DockerManager
from app.models import (
    AddPrimitiveRequest,
    CreateServerRequest,
    ServerResponse,
    ServerSpec,
    TemplateResponse,
    UpdateConfigRequest,
    UpdateEnvVarsRequest,
    UpdatePipPackagesRequest,
)
from app.permissions import can_access_server, get_accessible_server_names
from app.server_store import ServerStore
from app.template_engine import TemplateEngine

logger = logging.getLogger(__name__)
router = APIRouter()

docker_mgr = DockerManager()
template_engine = TemplateEngine()
store = ServerStore()


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

    return ServerResponse(
        name=server["name"],
        template=server["template"],
        status=server["status"],
        url=f"{MCP_BASE_URL}/s/{server['name']}/mcp",
        description=spec.description if spec else "",
        primitives=spec.primitives if spec else [],
        pip_packages=spec.pip_packages if spec else [],
        env_vars=spec.env_vars if spec else [],
        owner_id=owner_id,
        owner_email=owner_email,
        created_at=server.get("created_at"),
    )


def _env_dict(spec: ServerSpec) -> dict[str, str]:
    return {ev.name: ev.value for ev in spec.env_vars}


def _build_and_deploy(spec: ServerSpec) -> dict:
    build_ctx = write_build_context(spec, SERVERS_DATA_DIR / spec.name)
    store.save(spec)
    return docker_mgr.build_and_start(
        spec.name, build_ctx, "custom", env_vars=_env_dict(spec)
    )


def _redeploy(spec: ServerSpec) -> dict:
    docker_mgr.remove_server(spec.name)
    return _build_and_deploy(spec)


def _ensure_spec(name: str) -> ServerSpec:
    spec = store.load(name)
    if spec is None:
        if not docker_mgr.get_server(name):
            raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
        spec = ServerSpec(name=name)
        store.save(spec)
    return spec


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
    servers = docker_mgr.list_servers()
    accessible = get_accessible_server_names(user, db)

    results = []
    for s in servers:
        if accessible is not None and s["name"] not in accessible:
            continue
        spec = store.load(s["name"])
        results.append(_to_response(s, spec, db))
    return results


@router.get("/servers/{name}", response_model=ServerResponse)
def get_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = docker_mgr.get_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    _check_access(user, name, db)
    spec = store.load(name)
    return _to_response(server, spec, db)


@router.post("/servers", response_model=ServerResponse, status_code=201)
def create_server(
    req: CreateServerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if docker_mgr.get_server(req.name):
        raise HTTPException(status_code=409, detail=f"Server '{req.name}' already exists")

    try:
        if req.template:
            if not template_engine.get_template(req.template):
                raise HTTPException(
                    status_code=404, detail=f"Template '{req.template}' not found"
                )
            build_context = template_engine.render(req.template, req.name, req.config)
            spec = ServerSpec(name=req.name, description=req.description)
            store.save(spec)
            server = docker_mgr.build_and_start(req.name, build_context, req.template)
        else:
            spec = ServerSpec(name=req.name, description=req.description)
            server = _build_and_deploy(spec)

        # Record ownership
        db.add(ServerOwner(server_name=req.name, owner_id=user.id))
        db.commit()

        return _to_response(server, spec, db)
    except HTTPException:
        raise
    except Exception as e:
        store.delete(req.name)
        docker_mgr.remove_server(req.name)
        logger.exception("Failed to create server '%s'", req.name)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{name}/start", response_model=ServerResponse)
def start_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    server = docker_mgr.start_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec, db)


@router.post("/servers/{name}/stop", response_model=ServerResponse)
def stop_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    server = docker_mgr.stop_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec, db)


@router.delete("/servers/{name}", status_code=204)
def delete_server(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    if not docker_mgr.remove_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    store.delete(name)
    db.query(ServerOwner).filter(ServerOwner.server_name == name).delete()
    db.commit()


# --- Primitives ---


@router.post("/servers/{name}/primitives", response_model=ServerResponse, status_code=201)
def add_primitive(
    name: str,
    req: AddPrimitiveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_access(user, name, db)
    spec = _ensure_spec(name)

    for p in spec.primitives:
        if p.name == req.primitive.name and p.kind == req.primitive.kind:
            raise HTTPException(
                status_code=409,
                detail=f"{req.primitive.kind} '{req.primitive.name}' already exists",
            )

    spec.primitives.append(req.primitive)

    try:
        server = _redeploy(spec)
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
    spec = _ensure_spec(name)

    idx = next(
        (i for i, p in enumerate(spec.primitives) if p.name == prim_name),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    spec.primitives[idx] = req.primitive

    try:
        server = _redeploy(spec)
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
    spec = _ensure_spec(name)

    original_len = len(spec.primitives)
    spec.primitives = [p for p in spec.primitives if p.name != prim_name]

    if len(spec.primitives) == original_len:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    try:
        server = _redeploy(spec)
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
    spec = _ensure_spec(name)
    spec.pip_packages = req.pip_packages

    try:
        server = _redeploy(spec)
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
    spec = _ensure_spec(name)
    spec.env_vars = req.env_vars

    try:
        server = _redeploy(spec)
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
    spec = _ensure_spec(name)
    spec.pip_packages = req.pip_packages
    spec.env_vars = req.env_vars

    try:
        server = _redeploy(spec)
        return _to_response(server, spec, db)
    except Exception as e:
        logger.exception("Failed to update config for '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))
