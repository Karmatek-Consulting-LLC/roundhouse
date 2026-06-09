"""Deterministic diagnostic for secret env vars.

Classifies why a secret env var might vanish from a container on redeploy,
WITHOUT ever printing plaintext. Run inside the platform-api container:

    docker exec <container> python -m app.diagnose_secrets          # all servers
    docker exec <container> python -m app.diagnose_secrets <name>   # one server

For each secret-typed env var it reports one of:
    OK        valid ciphertext that decrypts under the current APP_KEY
    PLAINTEXT stored unencrypted (saved while APP_KEY was unset); effective_env
              now injects these as-is, so they are NOT lost
    STALE     valid ciphertext that FAILS to decrypt under the current APP_KEY
              (the key differs from when it was saved) -> re-enter the value once
    EMPTY     secret declared but no value stored yet

It also prints a non-reversible fingerprint of APP_KEY. Run it before and after a
redeploy: if the fingerprint changes, the key is not stable (the real bug);
if it stays the same, STALE rows were encrypted under a previous key.
"""
from __future__ import annotations

import hashlib
import sys

from app.config import get_settings, servers_dir
from app.crypto import DecryptError, decrypt, looks_encrypted
from app.services.store import ServerStore


def fingerprint(app_key: str) -> str:
    if not app_key:
        return "(APP_KEY NOT SET)"
    return "sha256:" + hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12]


def classify(value: str, app_key: str) -> str:
    if not value:
        return "EMPTY"
    if not looks_encrypted(value):
        return "PLAINTEXT"
    if not app_key:
        return "STALE (no APP_KEY to decrypt with)"
    try:
        decrypt(value, app_key)
        return "OK"
    except DecryptError:
        return "STALE (decrypt failed under current APP_KEY)"


def main(argv: list[str]) -> int:
    app_key = get_settings().app_key
    print(f"APP_KEY fingerprint: {fingerprint(app_key)}")

    store = ServerStore(servers_dir())
    specs = [store.load(argv[0])] if argv else store.list_all()
    specs = [s for s in specs if s is not None]
    if not specs:
        print("No matching server specs found.")
        return 1

    total_secret = 0
    total_stale = 0
    for spec in specs:
        secrets = [ev for ev in spec.env_vars if ev.secret]
        if not secrets:
            continue
        print(f"\n{spec.name} ({spec.mode}):")
        for ev in secrets:
            status = classify(ev.value, app_key)
            total_secret += 1
            if status.startswith("STALE"):
                total_stale += 1
            print(f"  {ev.name:32} {status}")

    print(
        f"\n{total_secret} secret env var(s) inspected; "
        f"{total_stale} cannot be decrypted under the current APP_KEY."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
