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


def _self_signed_ca_pem() -> str:
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Roundhouse Test CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def test_verify_for_ca_none_when_unset():
    from app.services.mcp_client import verify_for_ca

    assert verify_for_ca(None) is None
    assert verify_for_ca("") is None
    assert verify_for_ca("   ") is None


def test_verify_for_ca_builds_context_trusting_custom_ca():
    import ssl

    from app.services.mcp_client import verify_for_ca

    ctx = verify_for_ca(_self_signed_ca_pem())
    assert isinstance(ctx, ssl.SSLContext)
    # The custom CA is now in the context's trust store alongside system roots.
    subjects = {
        tuple(sorted(attr for rdn in c.get("subject", ()) for attr in rdn))
        for c in ctx.get_ca_certs()
    }
    assert any("Roundhouse Test CA" in str(s) for s in subjects)


def test_verify_for_ca_rejects_unparseable_pem():
    import ssl

    import pytest

    from app.services.mcp_client import verify_for_ca

    with pytest.raises(ssl.SSLError):
        verify_for_ca("this is not a certificate")
