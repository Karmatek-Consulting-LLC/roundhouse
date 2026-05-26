"""JSON-RPC client for the FastMCP servers we spawn.

Our generated servers run stateless_http=True + json_response=True - every
request stands alone, no handshake, no SSE. We POST and read JSON back. URL
resolution prefers Docker DNS (mcp-{name}:8000/mcp) so we don't depend on
Traefik for internal traffic."""
from __future__ import annotations

import secrets

import httpx

from app.services.docker import CONTAINER_PREFIX


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self):
        self._client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=3.0),
            headers={
                # FastMCP's streamable-http transport rejects requests that don't
                # advertise both content types in Accept.
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

    def call(self, server_name: str, method: str, params: dict | None = None) -> dict:
        url = self._server_url(server_name)
        envelope = {
            "jsonrpc": "2.0",
            "id": secrets.randbelow(2**62),
            "method": method,
            "params": params or {},
        }
        try:
            resp = self._client.post(url, json=envelope)
        except httpx.ConnectError as e:
            raise McpError(f"Cannot reach MCP server '{server_name}' ({e})") from e
        except httpx.HTTPError as e:
            raise McpError(f"MCP request failed: {e}") from e

        if resp.status_code >= 400:
            raise McpError(f"MCP server returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            decoded = resp.json()
        except ValueError as e:
            raise McpError(f"MCP server returned non-JSON response: {resp.text[:200]}") from e
        if not isinstance(decoded, dict):
            raise McpError("MCP server returned non-dict response")
        if "error" in decoded:
            err = decoded["error"] or {}
            raise McpError(f"MCP error {err.get('code', -1)}: {err.get('message', 'Unknown')}")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise McpError("MCP response missing result object")
        return result

    def list_tools(self, server_name: str) -> list[dict]:
        return self.call(server_name, "tools/list").get("tools", [])

    def list_resources(self, server_name: str) -> list[dict]:
        result = self.call(server_name, "resources/list")
        out = list(result.get("resources", []))
        for t in result.get("resourceTemplates", []) or []:
            out.append({**t, "isTemplate": True})
        return out

    def list_prompts(self, server_name: str) -> list[dict]:
        return self.call(server_name, "prompts/list").get("prompts", [])

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        return self.call(server_name, "tools/call", {"name": tool_name, "arguments": arguments or {}})

    def read_resource(self, server_name: str, uri: str) -> dict:
        return self.call(server_name, "resources/read", {"uri": uri})

    def get_prompt(self, server_name: str, prompt_name: str, arguments: dict) -> dict:
        return self.call(server_name, "prompts/get", {"name": prompt_name, "arguments": arguments or {}})

    @staticmethod
    def _server_url(server_name: str) -> str:
        host = CONTAINER_PREFIX + server_name
        return f"http://{host}:8000/mcp"


_singleton: McpClient | None = None


def get_mcp_client() -> McpClient:
    global _singleton
    if _singleton is None:
        _singleton = McpClient()
    return _singleton
