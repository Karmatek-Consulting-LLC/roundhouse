"""Portable server bundles — the zip behind server export/import.

A server is no longer just its spec: it can carry uploaded assets
(`server_assets` rows) and the non-regenerable build-files tarball for
git-cloned / template servers (`servers.build_files`). A flat JSON export
loses both, so exports are a zip bundle:

    manifest.json               # {version, exported_at, spec, assets: [...]}
    assets/<filename>           # raw asset bytes, one entry per asset
    build/build_files.tar.gz    # the stored snapshot tarball, verbatim

The manifest's `spec` is the same secret-stripped document the v1 flat-JSON
export carried, so a bundle's manifest imports through the legacy JSON path
too. Asset filenames re-run the AssetStore charset gate on parse — no path
separators can cross this boundary, so zip-slip is structurally impossible —
and size caps are enforced against declared *uncompressed* sizes before any
entry is read, so a decompression bomb is rejected without inflating it.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

from app.services.assets import MAX_FILE_BYTES, MAX_TOTAL_BYTES, _validate_filename

# Export format version. 1 was the flat spec-only JSON; 2 is the zip bundle.
BUNDLE_VERSION = 2

MANIFEST_NAME = "manifest.json"
ASSETS_PREFIX = "assets/"
BUILD_FILES_NAME = "build/build_files.tar.gz"

MAX_MANIFEST_BYTES = 5 * 1024 * 1024
# Defensive ceiling for the build-files tarball (a gzipped repo/template
# snapshot). Nothing this side enforces a cap at snapshot time, so this only
# guards imports against absurd archives.
MAX_BUILD_FILES_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 1024
# Upper bound for the whole uploaded bundle, checked before parsing.
MAX_BUNDLE_BYTES = MAX_MANIFEST_BYTES + MAX_TOTAL_BYTES + MAX_BUILD_FILES_BYTES


class BundleError(ValueError):
    """Any malformed / oversized / unsupported bundle. Routes map this to 422."""


@dataclass
class ParsedBundle:
    manifest: dict
    assets: list[tuple[str, bytes]]
    build_files: bytes | None


def build_bundle(
    manifest: dict,
    assets: list[tuple[str, bytes]],
    build_files: bytes | None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for name, data in assets:
            zf.writestr(ASSETS_PREFIX + name, data)
        if build_files:
            # Already gzip — deflating again just burns CPU.
            zf.writestr(BUILD_FILES_NAME, build_files, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def parse_bundle(data: bytes) -> ParsedBundle:
    """Validate and unpack an uploaded bundle. Unknown entries are ignored so
    a newer minor layout can still import what this version understands; an
    unknown *major* version is rejected via the manifest check."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"Bundle exceeds the {MAX_BUNDLE_BYTES}-byte limit")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BundleError(f"Not a valid zip bundle: {e}") from e

    with zf:
        try:
            return _parse_entries(zf)
        except zipfile.BadZipFile as e:  # corrupt entry surfacing mid-read
            raise BundleError(f"Corrupt bundle entry: {e}") from e


def _parse_entries(zf: zipfile.ZipFile) -> ParsedBundle:
    entries = [i for i in zf.infolist() if not i.is_dir()]
    if len(entries) > MAX_ENTRIES:
        raise BundleError(f"Bundle has more than {MAX_ENTRIES} entries")

    manifest = _read_manifest(zf, entries)
    version = manifest.get("version")
    if not isinstance(version, int) or version < 2:
        raise BundleError(f"Unsupported bundle version: {version!r}")
    if version > BUNDLE_VERSION:
        raise BundleError(
            f"Bundle version {version} is newer than this Roundhouse supports "
            f"(max {BUNDLE_VERSION}) — upgrade the destination platform"
        )
    if not isinstance(manifest.get("spec"), dict):
        raise BundleError("Bundle manifest has no spec object")

    assets = _read_assets(zf, entries)
    build_files = _read_build_files(zf, entries)
    return ParsedBundle(manifest=manifest, assets=assets, build_files=build_files)


def _read_manifest(zf: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> dict:
    info = next((i for i in entries if i.filename == MANIFEST_NAME), None)
    if info is None:
        raise BundleError(f"Bundle has no {MANIFEST_NAME}")
    if info.file_size > MAX_MANIFEST_BYTES:
        raise BundleError("Bundle manifest is too large")
    try:
        manifest = json.loads(zf.read(info))
    except (ValueError, UnicodeDecodeError) as e:
        raise BundleError(f"Bundle manifest is not valid JSON: {e}") from e
    if not isinstance(manifest, dict):
        raise BundleError("Bundle manifest must be a JSON object")
    return manifest


def _read_assets(zf: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> list[tuple[str, bytes]]:
    assets: list[tuple[str, bytes]] = []
    total = 0
    for info in entries:
        if not info.filename.startswith(ASSETS_PREFIX):
            continue
        name = info.filename[len(ASSETS_PREFIX):]
        try:
            name = _validate_filename(name)
        except ValueError as e:
            raise BundleError(f"Invalid asset entry {info.filename!r}: {e}") from e
        if info.file_size > MAX_FILE_BYTES:
            raise BundleError(f"Asset {name!r} exceeds the {MAX_FILE_BYTES}-byte per-file cap")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise BundleError(f"Bundle assets exceed the {MAX_TOTAL_BYTES}-byte server cap")
        content = zf.read(info)
        if len(content) > MAX_FILE_BYTES:  # header lied; trust the real bytes
            raise BundleError(f"Asset {name!r} exceeds the {MAX_FILE_BYTES}-byte per-file cap")
        assets.append((name, content))
    return assets


def _read_build_files(zf: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> bytes | None:
    info = next((i for i in entries if i.filename == BUILD_FILES_NAME), None)
    if info is None:
        return None
    if info.file_size > MAX_BUILD_FILES_BYTES:
        raise BundleError(f"Build files exceed the {MAX_BUILD_FILES_BYTES}-byte cap")
    data = zf.read(info)
    if len(data) > MAX_BUILD_FILES_BYTES:
        raise BundleError(f"Build files exceed the {MAX_BUILD_FILES_BYTES}-byte cap")
    return data or None
