"""Per-server asset storage.

Assets live at `{server_dir}/assets/{filename}` on the platform's persistent
volume and are baked into the spawned container's image at /app/assets/ via
codegen's `write_build_context`. Mutation (upload/delete) flags the server
as redeploy-required - the running container doesn't see changes until the
image is rebuilt.

Filenames are restricted to a safe alphanumeric + ._- charset; no path
separators ever cross this boundary, so traversal is structurally
impossible."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

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


class AssetStore:
    """Filesystem-backed asset CRUD for a single server."""

    def __init__(self, server_dir: Path):
        self.assets_dir = Path(server_dir) / "assets"

    def list(self) -> list[dict]:
        if not self.assets_dir.is_dir():
            return []
        out: list[dict] = []
        for p in sorted(self.assets_dir.iterdir()):
            if p.is_file():
                stat = p.stat()
                out.append({"name": p.name, "size": stat.st_size, "modified_ts": stat.st_mtime})
        return out

    def total_size(self) -> int:
        if not self.assets_dir.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.assets_dir.iterdir() if p.is_file())

    def write(self, filename: str, data: bytes) -> dict:
        name = _validate_filename(filename)
        if len(data) > MAX_FILE_BYTES:
            raise AssetError(f"File {name!r} exceeds per-file cap of {MAX_FILE_BYTES} bytes")
        # Compute projected total excluding the file we're about to overwrite.
        target = self.assets_dir / name
        existing_total = self.total_size()
        if target.is_file():
            existing_total -= target.stat().st_size
        if existing_total + len(data) > MAX_TOTAL_BYTES:
            raise AssetError(
                f"Upload would push total assets past the {MAX_TOTAL_BYTES}-byte server cap"
            )
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        # Write through a temp file so a partial write never leaves a broken
        # asset visible to the next build.
        tmp = self.assets_dir / (name + ".upload.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        stat = target.stat()
        return {"name": name, "size": stat.st_size, "modified_ts": stat.st_mtime}

    def delete(self, filename: str) -> bool:
        name = _validate_filename(filename)
        target = self.assets_dir / name
        if not target.is_file():
            return False
        target.unlink()
        return True

    def copy_into_build_context(self, dest_dir: Path) -> None:
        """Mirror assets/ into the docker build context. Always creates the
        destination directory (even if empty) so the Dockerfile's
        `COPY assets/` instruction stays valid."""
        out = Path(dest_dir) / "assets"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        if self.assets_dir.is_dir():
            for p in self.assets_dir.iterdir():
                if p.is_file():
                    shutil.copy2(p, out / p.name)
