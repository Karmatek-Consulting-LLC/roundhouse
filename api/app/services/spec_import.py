"""One-time import of filesystem server state into Postgres.

Migrates the old per-node `server-data` volume layout
(`<name>/server.json`, `<name>/assets/*`, plus cloned-repo / rendered-template
build files) into the `servers` + `server_assets` tables. Idempotent: a server
already present in `servers` is skipped, so it's safe to run on every boot and
safe to re-run by hand:

    python -m app.services.spec_import            # import from the configured volume
    python -m app.services.spec_import /some/dir  # import from an explicit path

Runs automatically at startup (see app.main lifespan). Once the volume is gone
(Phase 2 complete) it simply finds nothing and no-ops.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import servers_dir
from app.db import db_session
from app.models import Server, ServerAsset
from app.services.build_context import snapshot_dir
from app.services.spec import ServerSpec

logger = logging.getLogger(__name__)

# Files codegen regenerates each build or that are stored elsewhere; their
# presence alone does NOT make a server "has build files". Anything else in the
# dir (helper modules, data files, rendered template extras) does.
_MANAGED = {"server.json", "assets", "server.py", "Dockerfile", "proxy.py", "custom-ca.crt"}


def _has_extra_files(d: Path) -> bool:
    return any(child.name not in _MANAGED for child in d.iterdir())


def import_filesystem_specs(base: Path | str | None = None) -> dict:
    base = Path(base) if base else servers_dir()
    summary = {"imported": 0, "skipped": 0, "assets": 0, "build_files": 0, "errors": 0}
    if not base.is_dir():
        return summary

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        spec_path = entry / "server.json"
        if not spec_path.is_file():
            continue
        try:
            with db_session() as s:
                if s.get(Server, name) is not None:
                    summary["skipped"] += 1
                    continue

            data = json.loads(spec_path.read_text(encoding="utf-8"))
            spec = ServerSpec.from_dict(data)
            with db_session() as s:
                s.add(Server(name=spec.name, spec=spec.to_dict(), mode=spec.mode))

            assets_dir = entry / "assets"
            if assets_dir.is_dir():
                with db_session() as s:
                    for af in sorted(assets_dir.iterdir()):
                        if af.is_file():
                            content = af.read_bytes()
                            s.add(
                                ServerAsset(
                                    server_name=spec.name,
                                    filename=af.name,
                                    content=content,
                                    size_bytes=len(content),
                                )
                            )
                            summary["assets"] += 1

            if _has_extra_files(entry):
                blob = snapshot_dir(entry)
                with db_session() as s:
                    row = s.get(Server, spec.name)
                    if row is not None:
                        row.build_files = blob
                summary["build_files"] += 1

            summary["imported"] += 1
            logger.info("Imported server %r from volume into Postgres", name)
        except Exception as e:  # noqa: BLE001 - one bad dir must not abort the rest
            summary["errors"] += 1
            logger.error("Failed to import server %r from volume: %s", name, e)

    if summary["imported"] or summary["errors"]:
        logger.info(
            "Spec import complete: imported=%(imported)d skipped=%(skipped)d "
            "assets=%(assets)d build_files=%(build_files)d errors=%(errors)d",
            summary,
        )
    return summary


if __name__ == "__main__":  # pragma: no cover - manual CLI
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = import_filesystem_specs(arg)
    print(result)
