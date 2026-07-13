"""Self-managed TLS: validation of the uploaded pair + the Swarm secret swap."""
from __future__ import annotations

import base64
import datetime as dt
import os

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register tables on Base.metadata
from app.crypto import looks_encrypted
from app.db import Base
from app.platform_settings import SETTING_TLS_CERT, SETTING_TLS_KEY, get_setting
from app.services import tls_cert


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, autoflush=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _app_key(monkeypatch):
    from app.config import get_settings

    key = "base64:" + base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("APP_KEY", key)
    monkeypatch.setenv("MCP_TLS_SELF_MANAGED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_pair(cn: str = "mcp.example.com", days_valid: int = 30):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=days_valid))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


class FakeDocker:
    """Minimal Swarm double: records secret + service-update calls."""

    def __init__(self):
        self.secrets: dict[str, dict] = {}  # name -> {ID, Spec}
        self.service_secrets: list[dict] | None = None
        self.service_exists = True
        self.update_calls = 0
        self._n = 0

    def swarm_mode(self) -> bool:
        return True

    def get_service_by_name(self, name):
        if not self.service_exists:
            return None
        return {
            "ID": "svc1",
            "Version": {"Index": 1},
            "Spec": {
                "Name": name,
                "TaskTemplate": {"ContainerSpec": {"Secrets": self.service_secrets or []}},
            },
        }

    def find_secret(self, name):
        return self.secrets.get(name)

    def create_secret(self, name, data, labels=None):
        self._n += 1
        sid = f"sid{self._n}"
        self.secrets[name] = {"ID": sid, "Spec": {"Name": name, "Labels": labels or {}}, "_data": data}
        return sid

    def list_secrets(self, label=None):
        out = []
        for s in self.secrets.values():
            if label is None or label in (s["Spec"].get("Labels") or {}):
                out.append(s)
        return out

    def remove_secret(self, secret_id):
        for name, s in list(self.secrets.items()):
            if s["ID"] == secret_id:
                del self.secrets[name]

    def set_service_secrets(self, service_name, refs):
        self.service_name = service_name
        self.service_secrets = refs
        self.update_calls += 1


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(tls_cert, "get_docker", lambda: fake)
    return fake


# ---- Validation ----------------------------------------------------------

def test_validate_accepts_matching_pair():
    cert_pem, key_pem = _make_pair(cn="host.test")
    info = tls_cert.validate_pair(cert_pem, key_pem)
    assert info["subject_cn"] == "host.test"
    assert "host.test" in info["sans"]


def test_validate_rejects_mismatched_key():
    cert_pem, _ = _make_pair()
    _, other_key = _make_pair()
    with pytest.raises(tls_cert.TlsCertError, match="does not match"):
        tls_cert.validate_pair(cert_pem, other_key)


def test_validate_rejects_expired_cert():
    cert_pem, key_pem = _make_pair(days_valid=-1)
    with pytest.raises(tls_cert.TlsCertError, match="expired"):
        tls_cert.validate_pair(cert_pem, key_pem)


def test_validate_rejects_garbage():
    with pytest.raises(tls_cert.TlsCertError):
        tls_cert.validate_pair("not a cert", "not a key")


# ---- Apply / sync --------------------------------------------------------

def test_apply_creates_secrets_and_swaps_them_on_traefik(db, fake_docker):
    cert_pem, key_pem = _make_pair()
    tls_cert.apply_certificate(db, cert_pem, key_pem)

    # Three secrets — cert, key, and the file-provider config — content-
    # addressed and labelled for pruning.
    names = sorted(fake_docker.secrets)
    assert len(names) == 3
    assert any(n.startswith(tls_cert.CERT_TARGET + "_") for n in names)
    assert any(n.startswith(tls_cert.KEY_TARGET + "_") for n in names)
    assert any(n.startswith(tls_cert.DYNAMIC_TARGET + "_") for n in names)

    # Traefik service updated to mount them at the STABLE target paths that the
    # delivered dynamic config (mounted at DYNAMIC_FILENAME) references. The
    # dynamic config must mount with a .yml suffix — Traefik's file provider
    # infers the format from the extension and rejects extensionless files.
    refs = fake_docker.service_secrets
    assert fake_docker.service_name == "roundhouse_traefik"
    targets = sorted(r["File"]["Name"] for r in refs)
    assert targets == [
        tls_cert.CERT_TARGET,
        tls_cert.DYNAMIC_FILENAME,
        tls_cert.KEY_TARGET,
    ]
    assert tls_cert.DYNAMIC_FILENAME.endswith(".yml")
    key_ref = next(r for r in refs if r["File"]["Name"] == tls_cert.KEY_TARGET)
    assert key_ref["File"]["Mode"] == 0o400  # key is not world-readable
    # The delivered config names the fixed cert/key secret paths.
    dyn = fake_docker.secrets[
        next(n for n in names if n.startswith(tls_cert.DYNAMIC_TARGET + "_"))
    ]
    assert f"/run/secrets/{tls_cert.CERT_TARGET}".encode() in dyn["_data"]


def test_apply_persists_cert_plain_and_key_encrypted(db, fake_docker):
    cert_pem, key_pem = _make_pair()
    tls_cert.apply_certificate(db, cert_pem, key_pem)

    stored_cert = get_setting(db, SETTING_TLS_CERT)
    stored_key = get_setting(db, SETTING_TLS_KEY)
    assert "BEGIN CERTIFICATE" in stored_cert  # cert stored plain
    assert looks_encrypted(stored_key)  # key encrypted at rest
    assert "PRIVATE KEY" not in stored_key


def test_apply_delivers_pem_with_trailing_newline(db, fake_docker):
    # Uploads that lost the final newline (or gained padding) in copy-paste
    # must still reach Traefik newline-terminated, or Go's PEM decoder rejects
    # them and Traefik falls back to its self-signed cert.
    cert_pem, key_pem = _make_pair()
    tls_cert.apply_certificate(db, "\n  " + cert_pem.strip(), key_pem.strip())

    cert_name = next(
        n for n in fake_docker.secrets if n.startswith(tls_cert.CERT_TARGET + "_")
    )
    key_name = next(
        n for n in fake_docker.secrets if n.startswith(tls_cert.KEY_TARGET + "_")
    )
    assert fake_docker.secrets[cert_name]["_data"].endswith(
        b"-----END CERTIFICATE-----\n"
    )
    assert fake_docker.secrets[key_name]["_data"].endswith(b"PRIVATE KEY-----\n")
    # The persisted source of truth is normalised the same way.
    assert get_setting(db, SETTING_TLS_CERT).endswith("-----END CERTIFICATE-----\n")


def test_apply_hash_is_stable_across_cosmetic_whitespace(db, fake_docker):
    # Same pair, differing only in surrounding whitespace, must map to the
    # same content-addressed secrets — no churn on re-upload.
    cert_pem, key_pem = _make_pair()
    tls_cert.apply_certificate(db, cert_pem, key_pem)
    first = set(fake_docker.secrets)

    tls_cert.apply_certificate(db, cert_pem.strip(), "  " + key_pem.strip() + "\n\n")
    assert set(fake_docker.secrets) == first


def test_reupload_prunes_previous_secrets(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair(cn="one.test"))
    first = set(fake_docker.secrets)
    dyn = {n for n in first if n.startswith(tls_cert.DYNAMIC_TARGET + "_")}

    tls_cert.apply_certificate(db, *_make_pair(cn="two.test"))
    # Old cert/key gone; the static dynamic-config secret is reused, not churned.
    assert (first & set(fake_docker.secrets)) == dyn
    assert len(fake_docker.secrets) == 3


def test_clear_detaches_and_removes_secrets(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair())
    assert fake_docker.secrets

    tls_cert.clear_certificate(db)
    assert fake_docker.service_secrets == []  # detached from Traefik
    assert fake_docker.secrets == {}  # deleted
    assert get_setting(db, SETTING_TLS_CERT) is None


def test_status_reports_configured_after_apply(db, fake_docker):
    assert tls_cert.status(db) == {"supported": True, "configured": False}
    tls_cert.apply_certificate(db, *_make_pair(cn="status.test"))
    st = tls_cert.status(db)
    assert st["configured"] is True
    assert st["subject_cn"] == "status.test"


# ---- Reconcile (drift healing) ---------------------------------------------

def test_reconcile_unconfigured_when_no_cert_stored(db, fake_docker):
    assert tls_cert.reconcile(db) == "unconfigured"
    assert fake_docker.update_calls == 0


def test_reconcile_in_sync_after_apply(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair())
    calls = fake_docker.update_calls
    assert tls_cert.reconcile(db) == "in_sync"
    assert fake_docker.update_calls == calls  # no needless rolling update


def test_reconcile_reattaches_after_stack_redeploy_wipes_refs(db, fake_docker):
    # A `docker stack deploy` re-asserts the YAML spec: the secret OBJECTS
    # survive but the service's references to them are dropped, and Traefik
    # reverts to its self-signed default cert. Reconcile must re-attach.
    tls_cert.apply_certificate(db, *_make_pair())
    expected = {r["SecretName"] for r in fake_docker.service_secrets}
    fake_docker.service_secrets = []  # the redeploy reset

    assert tls_cert.reconcile(db) == "reapplied"
    assert {r["SecretName"] for r in fake_docker.service_secrets} == expected
    targets = sorted(r["File"]["Name"] for r in fake_docker.service_secrets)
    assert targets == [
        tls_cert.CERT_TARGET,
        tls_cert.DYNAMIC_FILENAME,
        tls_cert.KEY_TARGET,
    ]


def test_reconcile_recreates_deleted_secret_objects(db, fake_docker):
    # Even if the secrets themselves were cleaned up (not just detached),
    # everything is rebuilt from the Postgres source of truth.
    tls_cert.apply_certificate(db, *_make_pair())
    fake_docker.secrets = {}
    fake_docker.service_secrets = []

    assert tls_cert.reconcile(db) == "reapplied"
    assert len(fake_docker.secrets) == 3
    assert len(fake_docker.service_secrets) == 3


def test_reconcile_ignores_unrelated_mounted_secrets(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair())
    calls = fake_docker.update_calls
    fake_docker.service_secrets = fake_docker.service_secrets + [
        {"SecretName": "operator_extra", "File": {"Name": "extra"}}
    ]
    # Ours are all present -> in sync; the extra mount is not our business.
    assert tls_cert.reconcile(db) == "in_sync"
    assert fake_docker.update_calls == calls


def test_reconcile_unconfigured_when_tls_mode_off(db, fake_docker, monkeypatch):
    from app.config import get_settings

    tls_cert.apply_certificate(db, *_make_pair())
    fake_docker.service_secrets = []
    monkeypatch.setenv("MCP_TLS_SELF_MANAGED", "false")
    get_settings.cache_clear()
    assert tls_cert.reconcile(db) == "unconfigured"


def test_reconcile_raises_when_traefik_service_missing(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair())
    fake_docker.service_exists = False
    with pytest.raises(tls_cert.TlsCertError, match="not found"):
        tls_cert.reconcile(db)
