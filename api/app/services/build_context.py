"""Ephemeral build-context materialization.

Server state now lives in Postgres, not on a persistent per-node dir, so the
docker build context is assembled into a throwaway temp directory each build:

    git/template "build files" tarball (from servers.build_files)
        -> extracted into the temp dir
    codegen.write_build_context(spec, tmp)         # server.py / Dockerfile / proxy / ca
    AssetStore(name).copy_into_build_context(tmp)  # assets/ from server_assets

The temp dir is removed afterward. `snapshot_dir` produces the tarball stored in
`servers.build_files` at git-import / template-render time.
"""
from __future__ import annotations

import io
import tarfile
import tempfile
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path
from shutil import rmtree

# Generated files codegen always rewrites from the spec — never snapshot them,
# so the stored tarball stays the pure source (cloned repo / rendered template).
_SNAPSHOT_EXCLUDE = {".git", "server.json"}


def snapshot_dir(src: Path | str) -> bytes:
    """gzip-tar the contents of `src` (entries relative to src), skipping the
    spec file, .git, and the assets/ dir (assets live in their own table)."""
    src = Path(src)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for child in sorted(src.iterdir()):
            if child.name in _SNAPSHOT_EXCLUDE or child.name == "assets":
                continue
            tar.add(child, arcname=child.name)
    return buf.getvalue()


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def extract_into(data: bytes, dest: Path | str) -> None:
    """Extract a snapshot tarball into `dest`, refusing any member that would
    escape the destination (defense-in-depth; we author these tarballs)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                continue
            if not _is_within(dest, dest / member.name):
                continue
            # filter="data" (py3.12+) strips unsafe metadata + rejects escapes;
            # belt-and-suspenders alongside the manual guards above.
            tar.extract(member, dest, filter="data")


@contextmanager
def materialize(spec, store, custom_ca: str | None) -> Generator[Path, None, None]:
    """Build a complete docker build context in a temp dir from DB state and
    yield its path; always cleaned up. Mirrors the old persistent server_dir
    layout: prior build files first, then codegen overwrites server.py /
    Dockerfile, then assets/ from Postgres."""
    from app.services import codegen
    from app.services.assets import AssetStore

    tmp = Path(tempfile.mkdtemp(prefix="rh-build-"))
    try:
        blob = store.get_build_files(spec.name)
        if blob:
            extract_into(blob, tmp)
        codegen.write_build_context(spec, tmp, custom_ca)
        AssetStore(spec.name).copy_into_build_context(tmp)
        yield tmp
    finally:
        rmtree(tmp, ignore_errors=True)
