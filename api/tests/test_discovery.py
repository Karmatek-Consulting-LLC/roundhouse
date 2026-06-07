"""Phase 1 unit coverage: remote spec fields + discovery conversion/reconcile.

Pure functions only - no DB, no network, no Docker."""
from app.services import discovery
from app.services.spec import (
    MODE_REMOTE,
    MODE_STRUCTURED,
    ServerSpec,
)


# ---- Spec round-trip + mode helpers ----

def test_remote_spec_roundtrip():
    spec = ServerSpec(
        name="elastic",
        mode=MODE_REMOTE,
        remote_url="https://kibana.example/api/agent_builder/mcp",
        remote_headers=[{"header": "Authorization", "env": "RH_REMOTE_AUTHORIZATION"}],
        deny_unlisted=True,
    )
    out = ServerSpec.from_dict(spec.to_dict())
    assert out.mode == MODE_REMOTE
    assert out.is_remote_mode() and out.is_proxied()
    assert out.remote_url == spec.remote_url
    assert out.remote_headers == spec.remote_headers
    assert out.deny_unlisted is True


def test_unknown_mode_falls_back_to_structured():
    spec = ServerSpec.from_dict({"name": "x", "mode": "bogus"})
    assert spec.mode == MODE_STRUCTURED
    assert not spec.is_proxied()


def test_remote_headers_sanitized():
    spec = ServerSpec.from_dict({
        "name": "x",
        "mode": MODE_REMOTE,
        "remote_headers": [
            {"header": "Authorization", "env": "rh_remote_auth!"},  # disallowed chars stripped
            {"header": "", "env": "X"},                              # dropped (no header)
            "garbage",                                               # dropped (not dict)
        ],
    })
    # normalize_env_name uppercases and strips any non [A-Z0-9_] char (spaces/!).
    assert spec.remote_headers == [{"header": "Authorization", "env": "RH_REMOTE_AUTH"}]


def test_code_mode_is_proxied_remote_helpers():
    code = ServerSpec(name="c", mode="code")
    assert code.is_proxied() and not code.is_remote_mode()
    structured = ServerSpec(name="s")
    assert not structured.is_proxied()


# ---- Discovery conversion (mirrors a slice of Elastic's tools/list) ----

ELASTIC_TOOLS = [
    {
        "name": "platform_core_search",
        "description": "Search an index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "query": {"type": "string"},
                "size": {"type": "integer"},
            },
            "required": ["index"],
        },
    },
    {"name": "platform_core_list_indices", "inputSchema": {"type": "object", "properties": {}}},
]


def test_to_overlay_converts_tools():
    overlay = discovery.to_overlay(ELASTIC_TOOLS, [], [])
    assert {p["name"] for p in overlay} == {"platform_core_search", "platform_core_list_indices"}
    search = next(p for p in overlay if p["name"] == "platform_core_search")
    assert search["kind"] == "tool"
    assert search["discovered"] is True
    assert search["scopes"] == []
    params = {p["name"]: p for p in search["parameters"]}
    assert params["index"] == {"name": "index", "type": "str", "required": True}
    assert params["size"] == {"name": "size", "type": "int", "required": False}


def test_to_overlay_resources_and_prompts():
    resources = [
        {"name": "doc", "uri": "es://doc", "description": "A doc"},
        {"name": "idx", "uriTemplate": "es://{index}", "isTemplate": True},
    ]
    prompts = [{"name": "summarize", "arguments": [{"name": "text", "required": True}]}]
    overlay = discovery.to_overlay([], resources, prompts)
    by_kind = {p["kind"]: p for p in overlay}
    assert by_kind["resource"]["uri"] == "es://doc"
    assert by_kind["resource_template"]["uri_template"] == "es://{index}"
    assert by_kind["prompt"]["parameters"] == [{"name": "text", "type": "str", "required": True}]


# ---- Reconcile ----

def test_reconcile_preserves_scopes_adds_new_archives_removed():
    existing = [
        {"kind": "tool", "name": "platform_core_search", "scopes": ["es:read"], "discovered": True},
        {"kind": "tool", "name": "gone_tool", "scopes": ["es:admin"], "discovered": True},
    ]
    discovered = discovery.to_overlay(ELASTIC_TOOLS, [], [])
    out = discovery.reconcile(existing, discovered)
    by_name = {p["name"]: p for p in out}

    # scope preserved across rediscovery, params refreshed from upstream
    assert by_name["platform_core_search"]["scopes"] == ["es:read"]
    assert any(p["name"] == "index" for p in by_name["platform_core_search"]["parameters"])
    # brand-new tool added with empty scopes
    assert by_name["platform_core_list_indices"]["scopes"] == []
    # vanished tool archived, not dropped
    assert by_name["gone_tool"].get("archived") is True


def test_reconcile_leaves_authored_primitives_untouched():
    authored = [{"kind": "tool", "name": "hand_built", "code": "return 1", "scopes": ["x"]}]
    discovered = discovery.to_overlay(ELASTIC_TOOLS, [], [])
    out = discovery.reconcile(authored, discovered)
    hand = next(p for p in out if p["name"] == "hand_built")
    assert hand == authored[0]  # verbatim, not archived
    assert {p["name"] for p in out if p.get("discovered")} == {
        "platform_core_search", "platform_core_list_indices"
    }
