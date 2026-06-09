"""MCP JSON-RPC client wire-format guarantees."""
from app.services.mcp_client import McpClient

# JavaScript Number.MAX_SAFE_INTEGER - upstreams like Kibana's Agent Builder
# MCP validate that JSON-RPC integer ids do not exceed this and 400 otherwise.
JS_MAX_SAFE_INTEGER = 2**53 - 1


class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def json(self):
        return {"jsonrpc": "2.0", "result": {}}


def test_rpc_id_stays_within_js_safe_integer(monkeypatch):
    client = McpClient()
    captured: dict = {}

    def fake_post(url, json, headers=None):  # noqa: A002 - mirrors httpx signature
        captured["envelope"] = json
        return _FakeResp()

    monkeypatch.setattr(client._client, "post", fake_post)

    # Sample many ids so a too-large bound would be caught probabilistically.
    for _ in range(200):
        client.call_url("https://upstream.example/mcp", "tools/list")
        rpc_id = captured["envelope"]["id"]
        assert 0 <= rpc_id <= JS_MAX_SAFE_INTEGER
