"""Secret env vars must survive every redeploy.

Regression coverage for the bug where secret env vars silently vanished from a
container on redeploy: effective_env decrypted them and, on any DecryptError,
dropped the row with no log. Plaintext rows survived, so "only certain ones"
disappeared. The fix distinguishes a real ciphertext from a plaintext value
stored under the no-APP_KEY fallback, recovers the latter, and logs (never
silently drops) a genuine decrypt failure.
"""
from __future__ import annotations

import base64
import logging

from app.crypto import encrypt, looks_encrypted
from app.services import server_service as ss
from app.services.server_service import ServerService, _resolve_secret_env
from app.services.spec import EnvVar, ServerSpec

# Two distinct, valid 32-byte keys.
KEY = "base64:" + base64.b64encode(b"k" * 32).decode("ascii")
OTHER_KEY = "base64:" + base64.b64encode(b"z" * 32).decode("ascii")


def test_looks_encrypted_true_for_envelope():
    assert looks_encrypted(encrypt("hunter2", KEY)) is True


def test_looks_encrypted_false_for_plaintext():
    assert looks_encrypted("hunter2") is False
    assert looks_encrypted("ApiKey abc.def-ghi") is False
    assert looks_encrypted("") is False


def test_resolve_secret_roundtrip():
    blob = encrypt("s3cr3t", KEY)
    assert _resolve_secret_env("X", blob, KEY) == "s3cr3t"


def test_resolve_plaintext_fallback_passthrough():
    # Saved while APP_KEY was unset: stored plaintext, flagged secret. Must not
    # be dropped just because it isn't a ciphertext envelope.
    assert _resolve_secret_env("X", "literal-value", KEY) == "literal-value"


def test_resolve_wrong_key_drops_and_warns(caplog):
    blob = encrypt("s3cr3t", KEY)
    with caplog.at_level(logging.WARNING):
        assert _resolve_secret_env("MYSECRET", blob, OTHER_KEY) is None
    assert "MYSECRET" in caplog.text  # loss is diagnosable, not silent


def test_resolve_envelope_without_key_warns(caplog):
    blob = encrypt("s3cr3t", KEY)
    with caplog.at_level(logging.WARNING):
        assert _resolve_secret_env("MYSECRET", blob, "") is None
    assert "MYSECRET" in caplog.text


def test_effective_env_preserves_all_secret_kinds(monkeypatch):
    monkeypatch.setattr(ss.global_env, "globals_as_dict", lambda db: {})
    monkeypatch.setenv("APP_KEY", KEY)
    from app.config import get_settings

    get_settings.cache_clear()

    spec = ServerSpec(name="x")
    spec.env_vars = [
        EnvVar(name="PLAIN", value="p", secret=False),
        EnvVar(name="ENC", value=encrypt("e", KEY), secret=True),
        EnvVar(name="FALLBACK", value="raw-secret", secret=True),  # plaintext-as-secret
        EnvVar(name="UNSET", value="", secret=True),
    ]
    svc = ServerService(docker=None, store=None, templates=None)
    env = svc.effective_env(db=None, spec=spec)

    assert env == {"PLAIN": "p", "ENC": "e", "FALLBACK": "raw-secret", "UNSET": ""}


def test_effective_env_drops_only_undecryptable(monkeypatch):
    # A secret encrypted under a now-stale key is dropped, but a good plaintext
    # row beside it survives - proving "only certain ones" loss is contained and
    # not a wholesale wipe.
    monkeypatch.setattr(ss.global_env, "globals_as_dict", lambda db: {})
    monkeypatch.setenv("APP_KEY", OTHER_KEY)
    from app.config import get_settings

    get_settings.cache_clear()

    spec = ServerSpec(name="x")
    spec.env_vars = [
        EnvVar(name="GOOD", value="keep", secret=False),
        EnvVar(name="STALE", value=encrypt("lost", KEY), secret=True),
    ]
    svc = ServerService(docker=None, store=None, templates=None)
    env = svc.effective_env(db=None, spec=spec)

    assert env == {"GOOD": "keep"}
