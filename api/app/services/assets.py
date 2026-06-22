"""Per-server asset storage (Postgres-backed).

Assets are rows in `server_assets` and are baked into the spawned container's
image at /app/assets/ via the build context that codegen + the deploy flow
materialize. Mutation (upload/delete) flags the server as redeploy-required -
the running container doesn't see changes until the image is rebuilt.

Filenames are restricted to a safe alphanumeric + ._- charset; no path
separators ever cross this boundary, so traversal is structurally impossible."""
from __future__ import annotations

import re
import time
from pathlib import Path

from app.db import db_session
from app.models import ServerAsset

# Hard caps. Generous enough for lookup tables, small reference JSON, and
# token vocab files; small enough to keep image builds fast and avoid
# accidental large-binary uploads.
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB / file
MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB / server

# Disallow path separators, leading dots, and anything outside the basic set.
# Matches conservative POSIX portable-filename rules with `-` and `.` allowed
# but never at position 0 (so we don't accidentally accept `.env` etc).
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


class AssetError(ValueError):
    """Raised for any input-validation failure. Routes map this to HTTP 422."""


def _validate_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name:
        raise AssetError("Filename is required")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise AssetError(f"Invalid filename: {filename!r}")
    if not _SAFE_FILENAME_RE.match(name):
        raise AssetError(
            f"Invalid filename: {filename!r} (use letters, digits, dot, underscore, hyphen; max 128 chars)"
        )
    return name


def _modified_ts(row: ServerAsset) -> float:
    dt = row.updated_at or row.created_at
    try:
        return dt.timestamp() if dt is not None else time.time()
    except (AttributeError, ValueError):
        return time.time()


class AssetStore:
    """Postgres-backed asset CRUD for a single server. Each method opens its
    own short-lived session, mirroring ServerStore."""

    def __init__(self, server_name: str):
        self.server = server_name

    def list(self) -> list[dict]:
        with db_session() as s:
            rows = (
                s.query(ServerAsset)
                .filter(ServerAsset.server_name == self.server)
                .order_by(ServerAsset.filename)
                .all()
            )
            return [
                {"name": r.filename, "size": r.size_bytes, "modified_ts": _modified_ts(r)}
                for r in rows
            ]

    def total_size(self) -> int:
        with db_session() as s:
            rows = (
                s.query(ServerAsset.size_bytes)
                .filter(ServerAsset.server_name == self.server)
                .all()
            )
            return sum(r[0] for r in rows)

    def write(self, filename: str, data: bytes) -> dict:
        name = _validate_filename(filename)
        if len(data) > MAX_FILE_BYTES:
            raise AssetError(f"File {name!r} exceeds per-file cap of {MAX_FILE_BYTES} bytes")
        with db_session() as s:
            rows = (
                s.query(ServerAsset)
                .filter(ServerAsset.server_name == self.server)
                .all()
            )
            existing_total = sum(r.size_bytes for r in rows)
            current = next((r for r in rows if r.filename == name), None)
            if current is not None:
                existing_total -= current.size_bytes
            if existing_total + len(data) > MAX_TOTAL_BYTES:
                raise AssetError(
                    f"Upload would push total assets past the {MAX_TOTAL_BYTES}-byte server cap"
                )
            if current is None:
                current = ServerAsset(
                    server_name=self.server,
                    filename=name,
                    content=data,
                    size_bytes=len(data),
                )
                s.add(current)
            else:
                current.content = data
                current.size_bytes = len(data)
            s.flush()
            s.refresh(current)
            return {"name": name, "size": current.size_bytes, "modified_ts": _modified_ts(current)}

    def delete(self, filename: str) -> bool:
        name = _validate_filename(filename)
        with db_session() as s:
            deleted = (
                s.query(ServerAsset)
                .filter(ServerAsset.server_name == self.server, ServerAsset.filename == name)
                .delete()
            )
            return deleted > 0

    def read_bytes(self, filename: str) -> bytes | None:
        """Return an asset's raw bytes, or None if absent. Filename runs through
        the same safe-charset gate as write/delete."""
        name = _validate_filename(filename)
        with db_session() as s:
            row = (
                s.query(ServerAsset)
                .filter(ServerAsset.server_name == self.server, ServerAsset.filename == name)
                .one_or_none()
            )
            return bytes(row.content) if row is not None else None

    def copy_into_build_context(self, dest_dir: Path) -> None:
        """Materialize this server's assets into `{dest_dir}/assets/`. Always
        creates the directory (even if empty) so the Dockerfile's
        `COPY assets/` instruction stays valid."""
        out = Path(dest_dir) / "assets"
        out.mkdir(parents=True, exist_ok=True)
        with db_session() as s:
            rows = (
                s.query(ServerAsset)
                .filter(ServerAsset.server_name == self.server)
                .all()
            )
            for r in rows:
                (out / r.filename).write_bytes(bytes(r.content))
