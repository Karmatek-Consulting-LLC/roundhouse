"""Self-managed TLS: operator-uploaded HTTPS cert/key, terminated by the
embedded Traefik itself so no upstream reverse proxy is needed.

Flow (see the conversation in docker-stack.tls.override.yml):

  1. Operator uploads a PEM cert chain + private key in Platform Settings.
  2. We validate the pair (parses, key matches cert, not expired) and store it
     as the source of truth in platform_settings — cert plain, key encrypted at
     rest with the app.crypto AES envelope (keyed off APP_KEY).
  3. We sync it to the Swarm: create content-addressed secrets and swap them
     onto the Traefik service at STABLE mount paths, then prune the old ones.
     We deliver Traefik's file-provider config as a third (static) secret too,
     so nothing in the stack references a repo-relative file — the whole stack
     deploys from the compose files alone, with no checkout on the host. The
     dynamic config points at the fixed cert/key paths, so it never changes;
     only the secret objects churn.

Swarm secrets are immutable and cluster-distributed via Raft, so this needs no
shared volume and Traefik stays freely schedulable. The cost is that a cert
change is a rolling task update (zero-downtime with >=2 replicas), not an
in-place hot reload.

Only meaningful in Swarm mode with MCP_TLS_SELF_MANAGED=true.
"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import DecryptError, decrypt, encrypt, looks_encrypted
from app.platform_settings import (
    SETTING_TLS_CERT,
    SETTING_TLS_KEY,
    forget_setting,
    get_setting,
    put_setting,
)
from app.services.docker import get_docker
from app.services.docker_http import DockerError

logger = logging.getLogger(__name__)

# Label on the Docker secrets we manage, so we can find and prune stale ones
# without touching secrets created for other purposes.
TLS_SECRET_LABEL = "roundhouse.tls"
# Stable filenames the secrets mount to under /run/secrets/ on the Traefik
# container. The dynamic config below references exactly these paths, and
# Traefik's file provider is pointed at DYNAMIC_FILENAME.
CERT_TARGET = "roundhouse_tls_cert"
KEY_TARGET = "roundhouse_tls_key"
DYNAMIC_TARGET = "roundhouse_tls_dynamic"
# Traefik's file provider infers the config format from the file extension and
# rejects extensionless files ("unsupported file extension"), so the dynamic
# config must MOUNT with a .yml suffix. Only the in-container filename needs
# it; the secret objects keep the plain DYNAMIC_TARGET name prefix. The cert
# and key stay extensionless — certFile/keyFile are parsed as PEM regardless.
DYNAMIC_FILENAME = f"{DYNAMIC_TARGET}.yml"

# Traefik file-provider config declaring the default certificate. Static — it
# only names the two fixed secret paths — so we ship it as a secret rather than
# a repo file, and the operator needs no checkout on the host. Traefik reads it
# via `--providers.file.filename=/run/secrets/roundhouse_tls_dynamic.yml`.
_DYNAMIC_YAML = (
    "tls:\n"
    "  stores:\n"
    "    default:\n"
    "      defaultCertificate:\n"
    f"        certFile: /run/secrets/{CERT_TARGET}\n"
    f"        keyFile: /run/secrets/{KEY_TARGET}\n"
)


class TlsCertError(ValueError):
    """The uploaded cert/key pair is invalid or can't be applied."""


def _normalize_pem(pem: str) -> str:
    """Stripped + exactly one trailing newline.

    Stripping alone drops the newline after the final END line, and Go's
    encoding/pem (what Traefik parses the mounted secrets with) won't decode a
    block whose END line isn't newline-terminated — Traefik then silently falls
    back to its self-signed cert. Normalising (rather than not stripping) still
    absorbs copy-paste padding and keeps the content-addressed secret names
    stable across cosmetic whitespace differences."""
    return pem.strip() + "\n"


# ---- Validation + parsing -------------------------------------------------

def _load_cert(cert_pem: str):
    from cryptography import x509

    try:
        return x509.load_pem_x509_certificate(cert_pem.strip().encode())
    except Exception as e:  # noqa: BLE001 - surface a clean 422, not a stacktrace
        raise TlsCertError(f"Certificate is not valid PEM: {e}") from e


def _cert_public_der(cert) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return cert.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


def _describe(cert) -> dict:
    """UI-facing summary of a parsed certificate."""
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    def _cn(name) -> str:
        attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        return attrs[0].value if attrs else ""

    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        sans = ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass

    # cryptography >=42 exposes tz-aware *_utc; fall back for older versions.
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
    return {
        "subject_cn": _cn(cert.subject),
        "issuer_cn": _cn(cert.issuer),
        "sans": sans,
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
    }


def validate_pair(cert_pem: str, key_pem: str) -> dict:
    """Validate the cert chain + private key. Returns a UI summary of the leaf
    certificate. Raises TlsCertError on any problem."""
    import datetime as _dt

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    cert = _load_cert(cert_pem)
    try:
        key = load_pem_private_key(key_pem.strip().encode(), password=None)
    except TypeError as e:
        # Raised when the key is encrypted (needs a password we don't take).
        raise TlsCertError(
            "Private key appears to be encrypted; upload an unencrypted key."
        ) from e
    except Exception as e:  # noqa: BLE001
        raise TlsCertError(f"Private key is not valid PEM: {e}") from e

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key_pub_der = key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    if key_pub_der != _cert_public_der(cert):
        raise TlsCertError("Private key does not match the certificate.")

    info = _describe(cert)
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    now = _dt.datetime.now(_dt.timezone.utc)
    # Normalise a possibly-naive not_after for comparison.
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=_dt.timezone.utc)
    if not_after < now:
        raise TlsCertError(
            f"Certificate expired on {info['not_after']}; upload a current one."
        )
    return info


# ---- Persistence (source of truth) ----------------------------------------

def _encrypt_key(key_pem: str) -> str:
    app_key = get_settings().app_key
    if not app_key:
        # No APP_KEY -> store as-is (mirrors sso_config's plaintext fallback).
        return key_pem
    return encrypt(key_pem, app_key)


def _decrypt_key(stored: str) -> str:
    if not stored:
        return ""
    if not looks_encrypted(stored):
        return stored
    try:
        return decrypt(stored, get_settings().app_key)
    except DecryptError:
        return ""


# ---- Swarm sync -----------------------------------------------------------

def _ensure_secret(docker, name: str, data: bytes, labels: dict[str, str]) -> str:
    """Idempotent create: content-addressed names mean a matching name already
    holds identical bytes, so we reuse it rather than fail on the 409 conflict."""
    existing = docker.find_secret(name)
    if existing:
        return existing.get("ID", "")
    return docker.create_secret(name, data, labels)


def _prune_old_secrets(docker, keep: set[str]) -> None:
    for s in docker.list_secrets(TLS_SECRET_LABEL):
        name = (s.get("Spec") or {}).get("Name")
        sid = s.get("ID")
        if name and name not in keep and sid:
            docker.remove_secret(sid)  # no-op if still referenced; pruned later


def _sync_to_swarm(cert_pem: str, key_pem: str) -> None:
    docker = get_docker()
    if not getattr(docker, "swarm_mode", None) or not docker.swarm_mode():
        raise TlsCertError(
            "Self-managed TLS requires Docker Swarm mode (the cert is delivered "
            "to Traefik as a Swarm secret)."
        )
    h = hashlib.sha256(f"{cert_pem}\x00{key_pem}".encode()).hexdigest()[:12]
    cert_name = f"{CERT_TARGET}_{h}"
    key_name = f"{KEY_TARGET}_{h}"
    # The dynamic config is static, so its content hash is fixed: created once
    # and reused across every rotation.
    dyn_h = hashlib.sha256(_DYNAMIC_YAML.encode()).hexdigest()[:12]
    dyn_name = f"{DYNAMIC_TARGET}_{dyn_h}"
    cert_id = _ensure_secret(
        docker, cert_name, cert_pem.encode(), {TLS_SECRET_LABEL: "cert"}
    )
    key_id = _ensure_secret(
        docker, key_name, key_pem.encode(), {TLS_SECRET_LABEL: "key"}
    )
    dyn_id = _ensure_secret(
        docker, dyn_name, _DYNAMIC_YAML.encode(), {TLS_SECRET_LABEL: "dynamic"}
    )
    refs = [
        {
            "SecretID": cert_id,
            "SecretName": cert_name,
            "File": {"Name": CERT_TARGET, "UID": "0", "GID": "0", "Mode": 0o444},
        },
        {
            "SecretID": key_id,
            "SecretName": key_name,
            "File": {"Name": KEY_TARGET, "UID": "0", "GID": "0", "Mode": 0o400},
        },
        {
            "SecretID": dyn_id,
            "SecretName": dyn_name,
            "File": {"Name": DYNAMIC_FILENAME, "UID": "0", "GID": "0", "Mode": 0o444},
        },
    ]
    docker.set_service_secrets(get_settings().mcp_traefik_service, refs)
    _prune_old_secrets(docker, keep={cert_name, key_name, dyn_name})
    logger.info("Applied self-managed TLS cert to %s", get_settings().mcp_traefik_service)


# ---- Public API -----------------------------------------------------------

def apply_certificate(db: Session, cert_pem: str, key_pem: str) -> dict:
    """Validate, persist, and push a cert/key pair to the ingress Traefik.

    Persist-then-sync inside one request: get_db rolls back on exception, so a
    failed Swarm sync also discards the stored cert — they can't diverge."""
    cert_pem = _normalize_pem(cert_pem)
    key_pem = _normalize_pem(key_pem)
    info = validate_pair(cert_pem, key_pem)
    put_setting(db, SETTING_TLS_CERT, cert_pem)
    put_setting(db, SETTING_TLS_KEY, _encrypt_key(key_pem))
    db.flush()
    _sync_to_swarm(cert_pem, key_pem)
    return info


def clear_certificate(db: Session) -> None:
    """Remove the uploaded cert: detach the secrets from Traefik (it falls back
    to its auto self-signed cert), delete them, and forget the stored pair."""
    forget_setting(db, SETTING_TLS_CERT)
    forget_setting(db, SETTING_TLS_KEY)
    db.flush()
    docker = get_docker()
    if getattr(docker, "swarm_mode", None) and docker.swarm_mode():
        try:
            docker.set_service_secrets(get_settings().mcp_traefik_service, [])
            _prune_old_secrets(docker, keep=set())
        except DockerError as e:
            logger.warning("Could not detach TLS secrets from Traefik: %s", e)


def status(db: Session) -> dict:
    """UI status block. `supported` gates whether the section is shown at all."""
    supported = get_settings().mcp_tls_self_managed
    cert_pem = (get_setting(db, SETTING_TLS_CERT, "") or "").strip()
    if not cert_pem:
        return {"supported": supported, "configured": False}
    out = {"supported": supported, "configured": True}
    try:
        out.update(_describe(_load_cert(cert_pem)))
    except TlsCertError:
        pass
    return out
