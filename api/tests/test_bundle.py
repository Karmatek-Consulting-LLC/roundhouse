"""Server export/import bundle format (app.services.bundle).

Pure in-memory zip round-trips — no DB, no HTTP. Covers the manifest contract,
version gating, asset filename/size enforcement, build-files passthrough, and
malformed-archive rejection."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.services import bundle


def _manifest(spec: dict | None = None, version: int = bundle.BUNDLE_VERSION) -> dict:
    return {
        "version": version,
        "exported_at": "2026-07-10T00:00:00+00:00",
        "spec": {"name": "demo", "mode": "structured"} if spec is None else spec,
    }


def test_round_trip_spec_assets_and_build_files():
    assets = [("data.json", b'{"k":1}'), ("vocab.txt", b"a\nb\n")]
    build_files = b"\x1f\x8b-not-really-gzip-but-opaque-here"
    data = bundle.build_bundle(_manifest(), assets, build_files)

    parsed = bundle.parse_bundle(data)
    assert parsed.manifest["spec"]["name"] == "demo"
    assert sorted(parsed.assets) == sorted(assets)
    assert parsed.build_files == build_files


def test_round_trip_without_assets_or_build_files():
    parsed = bundle.parse_bundle(bundle.build_bundle(_manifest(), [], None))
    assert parsed.assets == []
    assert parsed.build_files is None


def test_unknown_entries_are_ignored():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(bundle.MANIFEST_NAME, json.dumps(_manifest()))
        zf.writestr("extras/future-thing.bin", b"???")
    parsed = bundle.parse_bundle(buf.getvalue())
    assert parsed.assets == [] and parsed.build_files is None


def test_not_a_zip_rejected():
    with pytest.raises(bundle.BundleError, match="valid zip"):
        bundle.parse_bundle(b'{"version": 1, "spec": {}}')  # a v1 flat JSON


def test_missing_manifest_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("assets/a.txt", b"x")
    with pytest.raises(bundle.BundleError, match="no manifest.json"):
        bundle.parse_bundle(buf.getvalue())


def test_missing_spec_rejected():
    m = _manifest()
    del m["spec"]
    with pytest.raises(bundle.BundleError, match="no spec"):
        bundle.parse_bundle(bundle.build_bundle(m, [], None))


@pytest.mark.parametrize("version", [1, "2", None])
def test_old_or_malformed_version_rejected(version):
    with pytest.raises(bundle.BundleError, match="Unsupported bundle version"):
        bundle.parse_bundle(bundle.build_bundle(_manifest(version=version), [], None))


def test_newer_version_rejected_with_upgrade_hint():
    data = bundle.build_bundle(_manifest(version=bundle.BUNDLE_VERSION + 1), [], None)
    with pytest.raises(bundle.BundleError, match="upgrade the destination"):
        bundle.parse_bundle(data)


@pytest.mark.parametrize("entry", ["assets/../evil", "assets/a/b.txt", "assets/.env"])
def test_traversal_and_unsafe_asset_names_rejected(entry):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(bundle.MANIFEST_NAME, json.dumps(_manifest()))
        zf.writestr(entry, b"x")
    with pytest.raises(bundle.BundleError, match="Invalid asset entry"):
        bundle.parse_bundle(buf.getvalue())


def test_per_file_cap_enforced_from_declared_size(monkeypatch):
    monkeypatch.setattr(bundle, "MAX_FILE_BYTES", 4)
    data = bundle.build_bundle(_manifest(), [("big.bin", b"12345")], None)
    with pytest.raises(bundle.BundleError, match="per-file cap"):
        bundle.parse_bundle(data)


def test_total_asset_cap_enforced(monkeypatch):
    monkeypatch.setattr(bundle, "MAX_TOTAL_BYTES", 6)
    data = bundle.build_bundle(_manifest(), [("a.bin", b"1234"), ("b.bin", b"5678")], None)
    with pytest.raises(bundle.BundleError, match="server cap"):
        bundle.parse_bundle(data)


def test_build_files_cap_enforced(monkeypatch):
    monkeypatch.setattr(bundle, "MAX_BUILD_FILES_BYTES", 4)
    data = bundle.build_bundle(_manifest(), [], b"12345")
    with pytest.raises(bundle.BundleError, match="Build files exceed"):
        bundle.parse_bundle(data)


def test_bundle_size_ceiling(monkeypatch):
    data = bundle.build_bundle(_manifest(), [], None)
    monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", len(data) - 1)
    with pytest.raises(bundle.BundleError, match="byte limit"):
        bundle.parse_bundle(data)


def test_corrupt_entry_rejected():
    """A bit-flipped entry (CRC mismatch) must surface as BundleError, not a
    raw BadZipFile escaping to the route."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(bundle.MANIFEST_NAME, json.dumps(_manifest()))
        zf.writestr("assets/blob.bin", b"AAAAAAAA")
    raw = buf.getvalue().replace(b"AAAAAAAA", b"BBBBBBBB")
    with pytest.raises(bundle.BundleError, match="Corrupt bundle entry"):
        bundle.parse_bundle(raw)
