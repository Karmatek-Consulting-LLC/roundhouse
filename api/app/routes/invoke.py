from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import User
from app.services import permissions
from app.services.docker import get_docker
from app.services.mcp_client import McpError, get_mcp_client

router = APIRouter(prefix="/api/servers", tags=["invoke"])


def _assert_access_and_deployed(db: Session, user: User, name: str) -> None:
    if not permissions.can_access(db, user, name):
        raise HTTPException(status_code=403, detail="Access denied")
    if not get_docker().get_server(name):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{name}' is not deployed - nothing to invoke against.",
        )


def _wrap(fn):
    try:
        return fn()
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/{name}/tools")
def list_tools(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    return {"tools": _wrap(lambda: get_mcp_client().list_tools(name))}


@router.get("/{name}/resources")
def list_resources(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    return {"resources": _wrap(lambda: get_mcp_client().list_resources(name))}


@router.get("/{name}/prompts")
def list_prompts(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    return {"prompts": _wrap(lambda: get_mcp_client().list_prompts(name))}


class ToolInvokeIn(BaseModel):
    tool: str = Field(min_length=1)
    arguments: dict = {}


@router.post("/{name}/tools/invoke")
def invoke_tool(
    name: str,
    payload: ToolInvokeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    return _wrap(lambda: get_mcp_client().call_tool(name, payload.tool, payload.arguments))


class ResourceReadIn(BaseModel):
    uri: str = Field(min_length=1)


@router.post("/{name}/resources/read")
def read_resource(
    name: str,
    payload: ResourceReadIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    return _wrap(lambda: get_mcp_client().read_resource(name, payload.uri))


class PromptGetIn(BaseModel):
    prompt: str = Field(min_length=1)
    arguments: dict = {}


@router.post("/{name}/prompts/get")
def get_prompt(
    name: str,
    payload: PromptGetIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    return _wrap(lambda: get_mcp_client().get_prompt(name, payload.prompt, payload.arguments))
