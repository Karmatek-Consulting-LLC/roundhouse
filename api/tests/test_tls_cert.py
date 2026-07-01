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
        self._n = 0

    def swarm_mode(self) -> bool:
        return True

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

    # Two secrets, content-addressed names, labelled for pruning.
    names = sorted(fake_docker.secrets)
    assert len(names) == 2
    assert any(n.startswith(tls_cert.CERT_TARGET + "_") for n in names)
    assert any(n.startswith(tls_cert.KEY_TARGET + "_") for n in names)

    # Traefik service updated to mount them at the STABLE target paths that
    # traefik/dynamic-tls.yml references.
    refs = fake_docker.service_secrets
    assert fake_docker.service_name == "roundhouse_traefik"
    targets = sorted(r["File"]["Name"] for r in refs)
    assert targets == [tls_cert.CERT_TARGET, tls_cert.KEY_TARGET]
    key_ref = next(r for r in refs if r["File"]["Name"] == tls_cert.KEY_TARGET)
    assert key_ref["File"]["Mode"] == 0o400  # key is not world-readable


def test_apply_persists_cert_plain_and_key_encrypted(db, fake_docker):
    cert_pem, key_pem = _make_pair()
    tls_cert.apply_certificate(db, cert_pem, key_pem)

    stored_cert = get_setting(db, SETTING_TLS_CERT)
    stored_key = get_setting(db, SETTING_TLS_KEY)
    assert "BEGIN CERTIFICATE" in stored_cert  # cert stored plain
    assert looks_encrypted(stored_key)  # key encrypted at rest
    assert "PRIVATE KEY" not in stored_key


def test_reupload_prunes_previous_secrets(db, fake_docker):
    tls_cert.apply_certificate(db, *_make_pair(cn="one.test"))
    first = set(fake_docker.secrets)

    tls_cert.apply_certificate(db, *_make_pair(cn="two.test"))
    # Old pair gone, only the new content-addressed pair remains.
    assert not (first & set(fake_docker.secrets))
    assert len(fake_docker.secrets) == 2


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
