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
        return self._rpc(self._server_url(server_name), method, params, what=f"server {server_name!r}")

    def call_url(
        self,
        url: str,
        method: str,
        params: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """JSON-RPC against an arbitrary MCP endpoint with extra headers - used
        to introspect EXTERNAL (remote-proxy) servers during discovery. Unlike
        our own servers, an upstream may answer streamable-http with an SSE
        body rather than json_response=True JSON, so _decode handles both."""
        return self._rpc(url, method, params, extra_headers=headers, what=url)

    def _rpc(
        self,
        url: str,
        method: str,
        params: dict | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
        what: str = "",
    ) -> dict:
        envelope = {
            "jsonrpc": "2.0",
            "id": secrets.randbelow(2**62),
            "method": method,
            "params": params or {},
        }
        try:
            resp = self._client.post(url, json=envelope, headers=extra_headers or None)
        except httpx.ConnectError as e:
            raise McpError(f"Cannot reach MCP {what} ({e})") from e
        except httpx.HTTPError as e:
            raise McpError(f"MCP request failed: {e}") from e

        if resp.status_code >= 400:
            raise McpError(f"MCP {what} returned HTTP {resp.status_code}: {resp.text[:300]}")
        decoded = self._decode(resp)
        if not isinstance(decoded, dict):
            raise McpError("MCP server returned non-dict response")
        if "error" in decoded:
            err = decoded["error"] or {}
            raise McpError(f"MCP error {err.get('code', -1)}: {err.get('message', 'Unknown')}")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise McpError("MCP response missing result object")
        return result

    @staticmethod
    def _decode(resp: httpx.Response) -> dict:
        """Parse a JSON-RPC reply that may arrive as plain JSON (our servers,
        json_response=True) or as a text/event-stream SSE frame (some upstreams).
        For SSE, the JSON-RPC message rides the last `data:` line."""
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            payload = None
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
            if payload is None:
                raise McpError("SSE response carried no data frame")
            import json as _json
            try:
                return _json.loads(payload)
            except ValueError as e:
                raise McpError(f"SSE data frame was not JSON: {payload[:200]}") from e
        try:
            return resp.json()
        except ValueError as e:
            raise McpError(f"MCP server returned non-JSON response: {resp.text[:200]}") from e

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

    # ---- External (remote-proxy) introspection ----

    def initialize_url(self, url: str, headers: dict[str, str] | None = None) -> dict:
        """Best-effort MCP initialize handshake against an external endpoint.
        Stateless upstreams (Elastic Agent Builder) answer tools/list without it,
        but stricter servers require it first; harmless either way."""
        return self.call_url(
            url,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "roundhouse-discovery", "version": "1"},
            },
            headers=headers,
        )

    def list_tools_url(self, url: str, headers: dict[str, str] | None = None) -> list[dict]:
        return self.call_url(url, "tools/list", headers=headers).get("tools", [])

    def list_resources_url(self, url: str, headers: dict[str, str] | None = None) -> list[dict]:
        result = self.call_url(url, "resources/list", headers=headers)
        out = list(result.get("resources", []))
        for t in result.get("resourceTemplates", []) or []:
            out.append({**t, "isTemplate": True})
        return out

    def list_prompts_url(self, url: str, headers: dict[str, str] | None = None) -> list[dict]:
        return self.call_url(url, "prompts/list", headers=headers).get("prompts", [])

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
