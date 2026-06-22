"""Postgres-backed ServerStore / AssetStore + filesystem importer.

Runs against an isolated in-file SQLite DB by repointing app.db.SessionLocal,
so it needs no Postgres. Covers spec round-trip, asset CRUD + caps, build-file
snapshot/extract, and the one-time volume->DB importer (incl. idempotency)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(monkeypatch, tmp_path):
    import app.db as appdb
    import app.models  # noqa: F401 - register tables on Base.metadata

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    TestSession = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(appdb, "SessionLocal", TestSession)
    appdb.Base.metadata.create_all(engine)
    yield
    engine.dispose()


def test_spec_round_trip(db):
    from app.services.spec import ServerSpec
    from app.services.store import ServerStore

    store = ServerStore()
    store.save(ServerSpec(name="demo", description="hi", mode="structured"))
    loaded = store.load("demo")
    assert loaded is not None and loaded.description == "hi"
    assert [s.name for s in store.list_all()] == ["demo"]
    store.delete("demo")
    assert store.load("demo") is None


def test_asset_crud_caps_and_validation(db):
    from app.services.assets import AssetError, AssetStore

    a = AssetStore("demo")
    rec = a.write("data.json", b'{"k":1}')
    assert rec == {"name": "data.json", "size": 7, "modified_ts": rec["modified_ts"]}
    assert a.read_bytes("data.json") == b'{"k":1}'
    assert a.total_size() == 7
    assert [x["name"] for x in a.list()] == ["data.json"]

    with pytest.raises(AssetError):
        a.write("../evil", b"x")

    a.write("data.json", b"abcd")  # overwrite, not duplicate
    assert a.read_bytes("data.json") == b"abcd"
    assert a.total_size() == 4
    assert len(a.list()) == 1

    assert a.delete("data.json") is True
    assert a.delete("data.json") is False
    assert a.read_bytes("data.json") is None


def test_delete_cascades_assets(db):
    from app.services.assets import AssetStore
    from app.services.spec import ServerSpec
    from app.services.store import ServerStore

    ServerStore().save(ServerSpec(name="demo", mode="structured"))
    AssetStore("demo").write("a.txt", b"x")
    ServerStore().delete("demo")
    assert AssetStore("demo").list() == []


def test_build_files_snapshot_excludes_spec_and_assets(db, tmp_path):
    from app.services.build_context import extract_into, snapshot_dir
    from app.services.spec import ServerSpec
    from app.services.store import ServerStore

    src = tmp_path / "src"
    src.mkdir()
    (src / "server.json").write_text("{}")
    (src / "helper.py").write_text("X = 1")
    (src / "assets").mkdir()
    (src / "assets" / "skip.txt").write_text("no")

    store = ServerStore()
    store.save(ServerSpec(name="g", mode="code"))
    store.set_build_files("g", snapshot_dir(src))

    out = tmp_path / "out"
    extract_into(store.get_build_files("g"), out)
    assert sorted(p.name for p in out.iterdir()) == ["helper.py"]
    assert (out / "helper.py").read_text() == "X = 1"


def test_importer_moves_volume_into_db_idempotently(db, tmp_path):
    from app.services.assets import AssetStore
    from app.services.build_context import extract_into
    from app.services.spec_import import import_filesystem_specs
    from app.services.store import ServerStore

    vol = tmp_path / "vol"
    (vol / "struct").mkdir(parents=True)
    (vol / "struct" / "server.json").write_text('{"name":"struct","mode":"structured"}')

    g = vol / "gitsrv"
    (g / "assets").mkdir(parents=True)
    g.joinpath("server.json").write_text('{"name":"gitsrv","mode":"code","git_url":"https://x/y"}')
    g.joinpath("server.py").write_text("print(1)")
    g.joinpath("helper.py").write_text("H=1")
    (g / "assets" / "a.txt").write_text("hello")

    summary = import_filesystem_specs(vol)
    assert summary["imported"] == 2
    assert summary["assets"] == 1
    assert summary["build_files"] == 1  # only gitsrv has extra (non-generated) files

    store = ServerStore()
    assert store.load("struct").mode == "structured"
    assert store.load("gitsrv").git_url == "https://x/y"
    assert AssetStore("gitsrv").read_bytes("a.txt") == b"hello"
    assert store.get_build_files("struct") is None

    bf = tmp_path / "bf"
    extract_into(store.get_build_files("gitsrv"), bf)
    assert (bf / "helper.py").read_text() == "H=1"

    again = import_filesystem_specs(vol)
    assert again["imported"] == 0 and again["skipped"] == 2
