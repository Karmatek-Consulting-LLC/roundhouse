"""AES-256-CBC + HMAC-SHA256 envelope used to encrypt server_tokens at rest.

Wire format — a base64-encoded JSON object:

    {
        "iv":    base64(16 bytes),
        "value": base64(ciphertext),
        "mac":   hex(hmac_sha256(iv_b64 || value_b64, key)),
        "tag":   ""        # CBC has no tag
    }

The key is the 32 raw bytes carried inside a `base64:<...>`-prefixed APP_KEY.
The format is fixed: existing rows must remain decryptable, so do not change it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class DecryptError(RuntimeError):
    pass


def _key_bytes(app_key: str) -> bytes:
    if not app_key:
        raise DecryptError("APP_KEY is not set")
    if app_key.startswith("base64:"):
        return base64.b64decode(app_key[len("base64:") :])
    return app_key.encode("utf-8")


def key_fingerprint(app_key: str) -> str:
    """A stable, non-reversible id for an APP_KEY.

    Used by backup/restore to verify a backup is being restored under the same
    key that encrypted its server_tokens — a different key would leave every
    stored token silently undecryptable. Returns a short hex digest; raises
    DecryptError when no key is configured."""
    return hashlib.sha256(_key_bytes(app_key)).hexdigest()[:16]


def encrypt(plaintext: str, app_key: str) -> str:
    key = _key_bytes(app_key)
    iv = os.urandom(16)

    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ciphertext = enc.update(padded) + enc.finalize()

    iv_b64 = base64.b64encode(iv).decode("ascii")
    value_b64 = base64.b64encode(ciphertext).decode("ascii")
    mac = hmac.new(
        key, (iv_b64 + value_b64).encode("ascii"), hashlib.sha256
    ).hexdigest()
    envelope = {"iv": iv_b64, "value": value_b64, "mac": mac, "tag": ""}
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


def looks_encrypted(token: str) -> bool:
    """True if `token` has the shape of an `encrypt()` envelope, regardless of key.

    Lets callers tell a genuine ciphertext apart from a plaintext value that was
    stored under the no-APP_KEY dev fallback (see `_encrypt_env`). A plaintext
    secret will not base64-decode into the `{iv, value, mac}` JSON envelope, so
    this returns False and the caller can use the value as-is rather than trying
    (and failing) to decrypt it."""
    try:
        env = json.loads(base64.b64decode(token))
    except (ValueError, TypeError):
        return False
    return isinstance(env, dict) and {"iv", "value", "mac"}.issubset(env)


def decrypt(token: str, app_key: str) -> str:
    key = _key_bytes(app_key)
    try:
        raw = base64.b64decode(token)
        env = json.loads(raw)
        iv = base64.b64decode(env["iv"])
        ciphertext = base64.b64decode(env["value"])
        expected = hmac.new(
            key, (env["iv"] + env["value"]).encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, env["mac"]):
            raise DecryptError("MAC mismatch")
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        dec = cipher.decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except (ValueError, KeyError, TypeError) as e:
        raise DecryptError(f"Decryption failed: {e}") from e
