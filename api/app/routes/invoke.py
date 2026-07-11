from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import User
from app.services import permissions, server_auth
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


def _auth_headers(db: Session, server: str, token_name: str | None = None) -> dict[str, str] | None:
    """Bearer header for the internal call. Once a server has tokens, its
    generated StaticTokenVerifier gates the whole MCP endpoint, so the tester
    authenticates with a real (decrypted) token — exercising the same auth +
    scope path as external clients. No tokens -> no header (auth is off).
    The plaintext never leaves the backend; callers only name the token."""
    plain = server_auth.token_plaintext(db, server, token_name)
    if token_name is not None and plain is None:
        raise HTTPException(status_code=404, detail=f"No token named '{token_name}' on this server")
    return {"Authorization": f"Bearer {plain}"} if plain else None


def _wrap(fn):
    try:
        return fn()
    except McpError as e:
        msg = str(e)
        if "HTTP 401" in msg:
            msg += (
                " — the server rejected the token. Newly minted, rotated, or revoked"
                " tokens take effect on the next redeploy."
            )
        raise HTTPException(status_code=502, detail=msg) from e


@router.get("/{name}/tools")
def list_tools(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name)
    return {"tools": _wrap(lambda: get_mcp_client().list_tools(name, headers=headers))}


@router.get("/{name}/resources")
def list_resources(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name)
    return {"resources": _wrap(lambda: get_mcp_client().list_resources(name, headers=headers))}


@router.get("/{name}/prompts")
def list_prompts(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name)
    return {"prompts": _wrap(lambda: get_mcp_client().list_prompts(name, headers=headers))}


class ToolInvokeIn(BaseModel):
    tool: str = Field(min_length=1)
    arguments: dict = {}
    # Which server token to run as (None -> the oldest token, if any). The
    # test dialog surfaces this so scoped tokens can be verified end-to-end.
    token_name: str | None = None


@router.post("/{name}/tools/invoke")
def invoke_tool(
    name: str,
    payload: ToolInvokeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name, payload.token_name)
    return _wrap(lambda: get_mcp_client().call_tool(name, payload.tool, payload.arguments, headers=headers))


class ResourceReadIn(BaseModel):
    uri: str = Field(min_length=1)
    token_name: str | None = None


@router.post("/{name}/resources/read")
def read_resource(
    name: str,
    payload: ResourceReadIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name, payload.token_name)
    return _wrap(lambda: get_mcp_client().read_resource(name, payload.uri, headers=headers))


class PromptGetIn(BaseModel):
    prompt: str = Field(min_length=1)
    arguments: dict = {}
    token_name: str | None = None


@router.post("/{name}/prompts/get")
def get_prompt(
    name: str,
    payload: PromptGetIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access_and_deployed(db, user, name)
    headers = _auth_headers(db, name, payload.token_name)
    return _wrap(lambda: get_mcp_client().get_prompt(name, payload.prompt, payload.arguments, headers=headers))
