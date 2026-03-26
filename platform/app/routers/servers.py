from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.config import MCP_BASE_URL
from app.docker_manager import DockerManager
from app.models import CreateServerRequest, ServerResponse, TemplateResponse
from app.template_engine import TemplateEngine

logger = logging.getLogger(__name__)
router = APIRouter()

docker_mgr = DockerManager()
template_engine = TemplateEngine()


def _to_response(server: dict) -> ServerResponse:
    return ServerResponse(
        name=server["name"],
        template=server["template"],
        status=server["status"],
        url=f"{MCP_BASE_URL}/mcp/{server['name']}/mcp",
        created_at=server.get("created_at"),
    )


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
    return [_to_response(s) for s in servers]


@router.get("/servers/{name}", response_model=ServerResponse)
def get_server(name: str):
    server = docker_mgr.get_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return _to_response(server)


@router.post("/servers", response_model=ServerResponse, status_code=201)
def create_server(req: CreateServerRequest):
    # Check if server already exists
    if docker_mgr.get_server(req.name):
        raise HTTPException(status_code=409, detail=f"Server '{req.name}' already exists")

    # Check template exists
    if not template_engine.get_template(req.template):
        raise HTTPException(
            status_code=404, detail=f"Template '{req.template}' not found"
        )

    try:
        build_context = template_engine.render(req.template, req.name, req.config)
        server = docker_mgr.build_and_start(req.name, build_context, req.template)
        return _to_response(server)
    except Exception as e:
        # Clean up on failure
        template_engine.cleanup(req.name)
        docker_mgr.remove_server(req.name)
        logger.exception("Failed to create server '%s'", req.name)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{name}/start", response_model=ServerResponse)
def start_server(name: str):
    server = docker_mgr.start_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return _to_response(server)


@router.post("/servers/{name}/stop", response_model=ServerResponse)
def stop_server(name: str):
    server = docker_mgr.stop_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return _to_response(server)


@router.delete("/servers/{name}", status_code=204)
def delete_server(name: str):
    if not docker_mgr.remove_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    template_engine.cleanup(name)
