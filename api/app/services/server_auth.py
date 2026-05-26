"""Per-server scopes + tokens for the runtime auth surface exposed by
generated FastMCP servers (StaticTokenVerifier + require_scopes).

Referential integrity is enforced here, not by DB constraints: scope rows
own a name, and that name appears as a string inside ServerToken.scopes and
inside primitive specs on disk. Delete/rename cascades fan out from this
module - nowhere else should mutate those references."""
from __future__ import annotations

import base64
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.laravel_crypto import DecryptError, decrypt, encrypt
from app.models import ServerOwner, ServerScope, ServerToken
from app.services.spec import ServerSpec
from app.services.store import ServerStore


def _decrypt_or_passthrough(stored: str) -> str:
    """Decrypt a Laravel-encrypted token. If decryption fails (rows written
    before the port may carry a different envelope, or new rows we encrypted
    ourselves), fall back to the raw column value."""
    app_key = get_settings().app_key
    try:
        return decrypt(stored, app_key)
    except DecryptError:
        return stored


def _encrypt_for_storage(plaintext: str) -> str:
    app_key = get_settings().app_key
    if not app_key:
        # No key configured (dev only) - store plaintext rather than failing.
        return plaintext
    return encrypt(plaintext, app_key)


def _generate_token_string() -> str:
    return "mcps_" + base64.urlsafe_b64encode(secrets.token_bytes(36)).rstrip(b"=").decode("ascii")


def mark_redeploy_required(db: Session, server: str) -> None:
    row = db.query(ServerOwner).filter(ServerOwner.server_name == server).first()
    if row is not None:
        row.redeploy_required_at = datetime.now(timezone.utc)


def clear_redeploy_required(db: Session, server: str) -> None:
    row = db.query(ServerOwner).filter(ServerOwner.server_name == server).first()
    if row is not None:
        row.redeploy_required_at = None


def create_scope(db: Session, server: str, name: str, description: str | None) -> ServerScope:
    scope = ServerScope(server_name=server, name=name, description=description)
    db.add(scope)
    db.flush()
    mark_redeploy_required(db, server)
    return scope


def delete_scope(db: Session, store: ServerStore, server: str, name: str) -> None:
    _mutate_primitive_scopes(store, server, lambda scopes: [s for s in scopes if s != name])
    for tok in db.query(ServerToken).filter(ServerToken.server_name == server).all():
        current = list(tok.scopes or [])
        nxt = [s for s in current if s != name]
        if nxt != current:
            tok.scopes = nxt
    db.query(ServerScope).filter(
        ServerScope.server_name == server, ServerScope.name == name
    ).delete()
    mark_redeploy_required(db, server)


def rename_scope(db: Session, store: ServerStore, server: str, old: str, new: str) -> None:
    _mutate_primitive_scopes(
        store, server, lambda scopes: [new if s == old else s for s in scopes]
    )
    for tok in db.query(ServerToken).filter(ServerToken.server_name == server).all():
        current = list(tok.scopes or [])
        nxt = [new if s == old else s for s in current]
        if nxt != current:
            tok.scopes = nxt
    db.query(ServerScope).filter(
        ServerScope.server_name == server, ServerScope.name == old
    ).update({ServerScope.name: new})
    mark_redeploy_required(db, server)


def mint_token(db: Session, server: str, name: str, scopes: list[str]) -> tuple[ServerToken, str]:
    plain = _generate_token_string()
    row = ServerToken(
        server_name=server,
        name=name,
        token=_encrypt_for_storage(plain),
        display_prefix=plain[:12],
        scopes=list({s for s in scopes if isinstance(s, str) and s}),
    )
    db.add(row)
    db.flush()
    mark_redeploy_required(db, server)
    return row, plain


def revoke_token(db: Session, server: str, token_id: int) -> bool:
    n = (
        db.query(ServerToken)
        .filter(ServerToken.server_name == server, ServerToken.id == token_id)
        .delete()
    )
    if n:
        mark_redeploy_required(db, server)
    return bool(n)


def tokens_for_codegen(db: Session, server: str) -> list[dict]:
    rows = (
        db.query(ServerToken)
        .filter(ServerToken.server_name == server)
        .order_by(ServerToken.id)
        .all()
    )
    return [
        {
            "name": t.name,
            "token": _decrypt_or_passthrough(t.token),
            "scopes": list(t.scopes or []),
        }
        for t in rows
    ]


def _mutate_primitive_scopes(store: ServerStore, server: str, mutator) -> None:
    spec = store.load(server)
    if spec is None:
        return
    changed = False
    for i, p in enumerate(spec.primitives):
        current = list(p.get("scopes") or [])
        if not current:
            continue
        nxt = list(mutator(current))
        if nxt != current:
            spec.primitives[i]["scopes"] = nxt
            changed = True
    if changed:
        store.save(spec)
