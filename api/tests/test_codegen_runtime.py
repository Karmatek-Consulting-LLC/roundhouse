"""Runtime behavior of the GENERATED scope-enforcement code.

These exec the generated server/proxy sources (so they need fastmcp installed -
skipped otherwise) and drive _mw_scope_allows with a faked access token to prove
the allow/deny/default-deny decisions are correct in the emitted code itself.
Run with:  uv run --extra dev --with fastmcp==3.3.1 pytest tests/test_codegen_runtime.py
"""
import pytest

pytest.importorskip("fastmcp")

from app.services import codegen  # noqa: E402
from app.services.spec import ServerSpec  # noqa: E402


def _exec(src: str) -> dict:
    ns: dict = {"__name__": "rh_runtime_test"}  # != __main__, so mcp.run never fires
    exec(compile(src, "<gen>", "exec"), ns)
    return ns


class _Tok:
    def __init__(self, scopes):
        self.scopes = scopes
        self.client_id = "client-a"


def test_remote_deny_unlisted_enforcement():
    spec = ServerSpec(
        name="elastic",
        mode="remote",
        remote_url="https://x/mcp",
        remote_headers=[{"header": "Authorization", "env": "RH_REMOTE_AUTHORIZATION"}],
        deny_unlisted=True,
        primitives=[
            {"kind": "tool", "name": "locked", "scopes": ["es:read"], "discovered": True},
            {"kind": "tool", "name": "ungranted", "scopes": [], "discovered": True},
        ],
        tokens=[{"name": "client-a", "token": "t", "scopes": ["es:read"]}],
    )
    ns = _exec(codegen.generate_proxy_py(spec, format_output=False))
    allows = ns["_mw_scope_allows"]

    ns["_get_access_token"] = lambda: _Tok(["es:read"])
    assert allows("tool", "locked") is True          # has required scope

    ns["_get_access_token"] = lambda: _Tok([])
    assert allows("tool", "locked") is False         # missing required scope
    assert allows("tool", "ungranted") is False      # no scope + deny_unlisted
    assert allows("tool", "never_seen") is False      # unknown + deny_unlisted


def test_structured_default_allow_enforcement():
    spec = ServerSpec(
        name="s",
        primitives=[
            {"kind": "tool", "name": "open_tool", "code": "return 1"},
            {"kind": "tool", "name": "scoped", "scopes": ["x"], "code": "return 1"},
        ],
        tokens=[{"name": "client-a", "token": "t", "scopes": []}],
    )
    ns = _exec(codegen.generate_server_py(spec, format_output=False))
    allows = ns["_mw_scope_allows"]

    ns["_get_access_token"] = lambda: _Tok([])
    assert allows("tool", "open_tool") is True        # unscoped + default-allow
    assert allows("tool", "scoped") is False          # missing scope x

    ns["_get_access_token"] = lambda: _Tok(["x"])
    assert allows("tool", "scoped") is True


def test_auth_disabled_is_open():
    spec = ServerSpec(
        name="s",
        primitives=[{"kind": "tool", "name": "t", "scopes": ["x"], "code": "return 1"}],
        # no tokens -> auth disabled
    )
    ns = _exec(codegen.generate_server_py(spec, format_output=False))
    allows = ns["_mw_scope_allows"]
    ns["_get_access_token"] = lambda: None
    assert allows("tool", "t") is True                # enforcement is a no-op


def test_ingest_capture_baked_and_bounded():
    """The generated server defines the metadata-push machinery, bakes its own
    name + ingest URL, and the enqueue is non-blocking / drop-oldest."""
    import asyncio

    spec = ServerSpec(
        name="obs-demo",
        primitives=[{"kind": "tool", "name": "t", "code": "return 1"}],
    )
    ns = _exec(codegen.generate_server_py(spec, format_output=False))

    # Baked constants
    assert ns["_INGEST_SERVER_NAME"] == "obs-demo"
    assert "platform-api" in ns["_INGEST_URL"]
    # Machinery exists
    assert callable(ns["_ingest_enqueue"])
    assert isinstance(ns["_INGEST_QUEUE"], asyncio.Queue)

    # Swap in a tiny queue to exercise drop-oldest without pushing 10k items.
    ns["_INGEST_QUEUE"] = asyncio.Queue(maxsize=2)
    ns["_INGEST_ENABLED"] = True
    enqueue = ns["_ingest_enqueue"]
    # No running loop here: _ingest_ensure_started swallows the RuntimeError.
    enqueue({"n": 1})
    enqueue({"n": 2})
    enqueue({"n": 3})  # overflows -> drops the oldest ({"n": 1})

    q = ns["_INGEST_QUEUE"]
    assert q.qsize() == 2
    drained = [q.get_nowait()["n"], q.get_nowait()["n"]]
    assert drained == [2, 3]


def test_ingest_capture_emitted_for_proxy_servers():
    """Proxied (code-first / remote) servers also get the capture machinery."""
    spec = ServerSpec(
        name="remote-demo",
        mode="remote",
        remote_url="https://x/mcp",
        primitives=[{"kind": "tool", "name": "t", "discovered": True}],
    )
    src = codegen.generate_proxy_py(spec, format_output=False)
    assert "_ingest_enqueue" in src
    assert "_INGEST_QUEUE" in src
