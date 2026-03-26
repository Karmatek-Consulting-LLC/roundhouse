from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.codegen import write_build_context
from app.config import MCP_BASE_URL, SERVERS_DATA_DIR
from app.docker_manager import DockerManager
from app.models import (
    AddPrimitiveRequest,
    CreateServerRequest,
    Primitive,
    ServerResponse,
    ServerSpec,
    TemplateResponse,
    UpdatePipPackagesRequest,
)
from app.server_store import ServerStore
from app.template_engine import TemplateEngine

logger = logging.getLogger(__name__)
router = APIRouter()

docker_mgr = DockerManager()
template_engine = TemplateEngine()
store = ServerStore()


def _to_response(server: dict, spec: ServerSpec | None = None) -> ServerResponse:
    return ServerResponse(
        name=server["name"],
        template=server["template"],
        status=server["status"],
        url=f"{MCP_BASE_URL}/mcp/{server['name']}/mcp",
        description=spec.description if spec else "",
        primitives=spec.primitives if spec else [],
        pip_packages=spec.pip_packages if spec else [],
        created_at=server.get("created_at"),
    )


def _build_and_deploy(spec: ServerSpec) -> dict:
    """Generate code, build image, and start container."""
    build_ctx = write_build_context(spec, SERVERS_DATA_DIR / spec.name)
    store.save(spec)
    return docker_mgr.build_and_start(spec.name, build_ctx, "custom")


def _redeploy(spec: ServerSpec) -> dict:
    """Stop, remove, rebuild, and restart a server with updated spec."""
    docker_mgr.remove_server(spec.name)
    return _build_and_deploy(spec)


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
def list_servers():
    servers = docker_mgr.list_servers()
    results = []
    for s in servers:
        spec = store.load(s["name"])
        results.append(_to_response(s, spec))
    return results


@router.get("/servers/{name}", response_model=ServerResponse)
def get_server(name: str):
    server = docker_mgr.get_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec)


@router.post("/servers", response_model=ServerResponse, status_code=201)
def create_server(req: CreateServerRequest):
    if docker_mgr.get_server(req.name):
        raise HTTPException(status_code=409, detail=f"Server '{req.name}' already exists")

    try:
        if req.template:
            # Legacy template-based flow
            if not template_engine.get_template(req.template):
                raise HTTPException(
                    status_code=404, detail=f"Template '{req.template}' not found"
                )
            build_context = template_engine.render(req.template, req.name, req.config)
            spec = ServerSpec(name=req.name, description=req.description)
            store.save(spec)
            server = docker_mgr.build_and_start(req.name, build_context, req.template)
        else:
            # New primitive-based flow: create empty server
            spec = ServerSpec(name=req.name, description=req.description)
            server = _build_and_deploy(spec)

        return _to_response(server, spec)
    except HTTPException:
        raise
    except Exception as e:
        store.delete(req.name)
        docker_mgr.remove_server(req.name)
        logger.exception("Failed to create server '%s'", req.name)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{name}/start", response_model=ServerResponse)
def start_server(name: str):
    server = docker_mgr.start_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec)


@router.post("/servers/{name}/stop", response_model=ServerResponse)
def stop_server(name: str):
    server = docker_mgr.stop_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    spec = store.load(name)
    return _to_response(server, spec)


@router.delete("/servers/{name}", status_code=204)
def delete_server(name: str):
    if not docker_mgr.remove_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    store.delete(name)


# --- Primitives ---


@router.post("/servers/{name}/primitives", response_model=ServerResponse, status_code=201)
def add_primitive(name: str, req: AddPrimitiveRequest):
    spec = store.load(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    # Check for duplicate primitive name
    for p in spec.primitives:
        if p.name == req.primitive.name and p.kind == req.primitive.kind:
            raise HTTPException(
                status_code=409,
                detail=f"{req.primitive.kind} '{req.primitive.name}' already exists",
            )

    spec.primitives.append(req.primitive)

    try:
        server = _redeploy(spec)
        return _to_response(server, spec)
    except Exception as e:
        logger.exception("Failed to add primitive to '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/servers/{name}/primitives/{prim_name}", response_model=ServerResponse)
def update_primitive(name: str, prim_name: str, req: AddPrimitiveRequest):
    spec = store.load(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    idx = next(
        (i for i, p in enumerate(spec.primitives) if p.name == prim_name),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    spec.primitives[idx] = req.primitive

    try:
        server = _redeploy(spec)
        return _to_response(server, spec)
    except Exception as e:
        logger.exception("Failed to update primitive on '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/servers/{name}/primitives/{prim_name}", response_model=ServerResponse)
def delete_primitive(name: str, prim_name: str):
    spec = store.load(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    original_len = len(spec.primitives)
    spec.primitives = [p for p in spec.primitives if p.name != prim_name]

    if len(spec.primitives) == original_len:
        raise HTTPException(status_code=404, detail=f"Primitive '{prim_name}' not found")

    try:
        server = _redeploy(spec)
        return _to_response(server, spec)
    except Exception as e:
        logger.exception("Failed to delete primitive from '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/servers/{name}/packages", response_model=ServerResponse)
def update_pip_packages(name: str, req: UpdatePipPackagesRequest):
    spec = store.load(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    spec.pip_packages = req.pip_packages

    try:
        server = _redeploy(spec)
        return _to_response(server, spec)
    except Exception as e:
        logger.exception("Failed to update packages for '%s'", name)
        raise HTTPException(status_code=500, detail=str(e))
