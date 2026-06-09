"""Phase 3: remote-server route helpers (no DB/Docker needed)."""
from app.routes.servers import (
    EnvVarItem,
    RemoteHeaderIn,
    _apply_remote_headers,
    _merge_env_vars,
)
from app.services.spec import EnvVar, ServerSpec


def test_merge_preserves_secret_when_blank_flag_lost():
    # The wipe scenario: a stored secret comes back from the client blank and
    # without the secret flag (a masked field round-trips empty). It must NOT
    # be clobbered to an empty plaintext value.
    existing = [EnvVar(name="LM_BEARER_TOKEN", value="ciphertext-abc", secret=True)]
    incoming = [EnvVarItem(name="LM_BEARER_TOKEN", value="", secret=False)]
    out = _merge_env_vars(incoming, existing)
    assert out == [EnvVar(name="LM_BEARER_TOKEN", value="ciphertext-abc", secret=True)]


def test_merge_preserves_secret_when_flag_kept_but_blank():
    existing = [EnvVar(name="API_KEY", value="ct", secret=True)]
    incoming = [EnvVarItem(name="API_KEY", value="", secret=True)]
    out = _merge_env_vars(incoming, existing)
    assert out == [EnvVar(name="API_KEY", value="ct", secret=True)]


def test_merge_deletes_when_row_absent():
    # A genuine delete drops the row from the payload entirely - it must NOT be
    # resurrected by the guardrail.
    existing = [EnvVar(name="API_KEY", value="ct", secret=True)]
    out = _merge_env_vars([], existing)
    assert out == []


def test_merge_plaintext_blank_still_clears():
    # Blanking a NON-secret value is a legitimate clear and must still work.
    existing = [EnvVar(name="PLAIN", value="old", secret=False)]
    incoming = [EnvVarItem(name="PLAIN", value="", secret=False)]
    out = _merge_env_vars(incoming, existing)
    assert out == [EnvVar(name="PLAIN", value="", secret=False)]


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
