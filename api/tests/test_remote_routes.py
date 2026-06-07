"""Phase 3: remote-server route helpers (no DB/Docker needed)."""
from app.routes.servers import RemoteHeaderIn, _apply_remote_headers
from app.services.spec import ServerSpec


def test_apply_remote_headers_creates_secret_env_and_mapping():
    spec = ServerSpec(name="e", mode="remote", remote_url="https://x/mcp")
    _apply_remote_headers(spec, [RemoteHeaderIn(header="Authorization", value="ApiKey abc")])

    assert spec.remote_headers == [{"header": "Authorization", "env": "RH_REMOTE_AUTHORIZATION"}]
    rows = [e for e in spec.env_vars if e.name == "RH_REMOTE_AUTHORIZATION"]
    assert len(rows) == 1
    assert rows[0].secret is True
    assert rows[0].value  # encrypted (or plaintext fallback) - never empty


def test_apply_remote_headers_replaces_and_skips_blank():
    spec = ServerSpec(name="e", mode="remote")
    _apply_remote_headers(spec, [RemoteHeaderIn(header="Authorization", value="v1")])
    _apply_remote_headers(spec, [
        RemoteHeaderIn(header="Authorization", value="v2"),  # replaces v1's row
        RemoteHeaderIn(header="", value="x"),                # skipped: no header
        RemoteHeaderIn(header="X-Extra", value="   "),       # skipped: blank value
    ])

    rh_rows = [e.name for e in spec.env_vars if e.name.startswith("RH_REMOTE_")]
    assert rh_rows == ["RH_REMOTE_AUTHORIZATION"]  # replaced, not duplicated
    assert spec.remote_headers == [{"header": "Authorization", "env": "RH_REMOTE_AUTHORIZATION"}]
