"""Discover a proxied MCP server's primitives and reconcile them into its spec
as a scopes-only overlay.

Proxied servers (code-first + remote) front an MCP server the platform did not
author, so we cannot enumerate their tools from the spec the way structured
servers are built. Instead we introspect the live server - the internal
container for code-first, the upstream URL for remote - and persist the result
as `discovered` primitives. These render in the nav like authored ones and
carry per-tool `scopes`, but they never drive codegen (the proxy executes the
real tool); they exist so an operator can SEE and SCOPE each primitive.
"""
from __future__ import annotations

import ssl
from typing import Any, Callable

from app.services.mcp_client import McpClient, McpError
from app.services.spec import ServerSpec

# JSON-Schema primitive type -> the platform's parameter type vocabulary
# (mirrors the frontend's liveToolToPrimitive so both render identically).
_JSON_TO_PY = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "array": "list",
}


def _params_from_schema(schema: Any) -> list[dict]:
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    req = schema.get("required")
    required = set(req) if isinstance(req, list) else set()
    out: list[dict] = []
    for pname, pschema in props.items():
        if not isinstance(pname, str) or not pname:
            continue
        ptype = "str"
        if isinstance(pschema, dict):
            ptype = _JSON_TO_PY.get(pschema.get("type"), "str")
        out.append({"name": pname, "type": ptype, "required": pname in required})
    return out


def _tool_to_primitive(t: dict) -> dict | None:
    name = t.get("name")
    if not isinstance(name, str) or not name:
        return None
    return {
        "kind": "tool",
        "name": name,
        "description": str(t.get("description") or ""),
        "parameters": _params_from_schema(t.get("inputSchema")),
        "scopes": [],
        "discovered": True,
    }


def _resource_to_primitive(r: dict) -> dict | None:
    name = r.get("name")
    if not isinstance(name, str) or not name:
        return None
    if r.get("isTemplate"):
        return {
            "kind": "resource_template",
            "name": name,
            "description": str(r.get("description") or ""),
            "uri_template": str(r.get("uriTemplate") or r.get("uri_template") or ""),
            "scopes": [],
            "discovered": True,
        }
    return {
        "kind": "resource",
        "name": name,
        "description": str(r.get("description") or ""),
        "uri": str(r.get("uri") or ""),
        "scopes": [],
        "discovered": True,
    }


def _prompt_to_primitive(p: dict) -> dict | None:
    name = p.get("name")
    if not isinstance(name, str) or not name:
        return None
    params: list[dict] = []
    for a in p.get("arguments") or []:
        if isinstance(a, dict) and isinstance(a.get("name"), str):
            params.append({"name": a["name"], "type": "str", "required": bool(a.get("required"))})
    return {
        "kind": "prompt",
        "name": name,
        "description": str(p.get("description") or ""),
        "parameters": params,
        "scopes": [],
        "discovered": True,
    }


def to_overlay(tools: list, resources: list, prompts: list) -> list[dict]:
    """Convert raw MCP list_* dicts into platform overlay primitives."""
    out: list[dict] = []
    for t in tools or []:
        p = _tool_to_primitive(t) if isinstance(t, dict) else None
        if p:
            out.append(p)
    for r in resources or []:
        p = _resource_to_primitive(r) if isinstance(r, dict) else None
        if p:
            out.append(p)
    for pr in prompts or []:
        p = _prompt_to_primitive(pr) if isinstance(pr, dict) else None
        if p:
            out.append(p)
    return out


def _key(p: dict) -> tuple:
    return (p.get("kind"), p.get("name"))


def reconcile(existing: list[dict], discovered: list[dict]) -> list[dict]:
    """Fold a fresh discovery into a spec's primitives:

    - authored (non-`discovered`) primitives pass through untouched,
    - operator-assigned `scopes`/`middleware` survive across rediscovery,
    - newly-discovered primitives are added (empty scopes),
    - previously-discovered primitives missing upstream are marked
      `archived: True` rather than deleted, so a removed tool stays visible
      until an operator clears it.
    """
    existing_by_key = {_key(p): p for p in existing}
    discovered_keys = {_key(d) for d in discovered}
    out: list[dict] = []

    # Authored primitives (structured servers) are never touched by discovery.
    for p in existing:
        if not p.get("discovered"):
            out.append(p)

    for d in discovered:
        prev = existing_by_key.get(_key(d))
        if prev is not None and prev.get("discovered"):
            merged = dict(d)  # refreshes description/params, drops stale archive
            if isinstance(prev.get("scopes"), list):
                merged["scopes"] = prev["scopes"]
            if isinstance(prev.get("middleware"), dict):
                merged["middleware"] = prev["middleware"]
            out.append(merged)
        else:
            out.append(d)

    for p in existing:
        if p.get("discovered") and _key(p) not in discovered_keys:
            out.append({**p, "archived": True})

    return out


def _safe(fn: Callable[[], list]) -> list:
    """Resources/prompts are optional MCP capabilities - a server that doesn't
    advertise them answers with a method-not-found error. Treat that as empty
    rather than failing the whole discovery."""
    try:
        return fn()
    except McpError:
        return []


def discover(
    client: McpClient,
    spec: ServerSpec,
    *,
    remote_headers: dict[str, str] | None = None,
    verify: "ssl.SSLContext | None" = None,
) -> list[dict]:
    """Introspect the live proxied server and return reconciled overlay
    primitives (caller persists them). `tools/list` failures propagate (wrong
    URL, bad credential, server not up); resources/prompts degrade to empty.
    `verify` is the TLS context for the remote upstream (custom CA support).
    """
    if spec.is_remote_mode():
        url = (spec.remote_url or "").strip()
        if not url:
            raise McpError("remote_url is not set for this server")
        # Best-effort handshake; stateless upstreams ignore it.
        try:
            client.initialize_url(url, remote_headers, verify=verify)
        except McpError:
            pass
        tools = client.list_tools_url(url, remote_headers, verify=verify)
        resources = _safe(lambda: client.list_resources_url(url, remote_headers, verify=verify))
        prompts = _safe(lambda: client.list_prompts_url(url, remote_headers, verify=verify))
    else:
        # Code-first: introspect the internal container (must be deployed/up).
        tools = client.list_tools(spec.name)
        resources = _safe(lambda: client.list_resources(spec.name))
        prompts = _safe(lambda: client.list_prompts(spec.name))

    return reconcile(spec.primitives, to_overlay(tools, resources, prompts))
