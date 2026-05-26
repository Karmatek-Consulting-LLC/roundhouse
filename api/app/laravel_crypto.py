"""Laravel `encrypted` cast compatibility.

Laravel's Encrypter wraps AES-256-CBC + HMAC-SHA256 in a base64(JSON) envelope:

    {
        "iv":    base64(16 bytes),
        "value": base64(ciphertext),
        "mac":   hex(hmac_sha256(iv || ciphertext, key)),
        "tag":   ""        # CBC has no tag
    }

The outer envelope is then base64-encoded. The key is the 32 raw bytes inside
the `base64:...` APP_KEY. Existing server_tokens rows minted by the Laravel app
must remain decryptable after the port, so we replicate the wire format
exactly.
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
