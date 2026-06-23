"""Postgres persistence for ServerSpecs.

Replaces the old one-JSON-file-per-server layout on the `server-data` volume so
platform-api carries no node-local state and can scale to multiple replicas.
Each method opens its own short-lived session (the spec store was never
transactional with the relational owner/scope/token writes — that separation is
preserved), so the existing call sites keep their `store.save(spec)` /
`store.load(name)` shape unchanged."""
from __future__ import annotations

from app.db import db_session
from app.models import Server, ServerAsset
from app.services.spec import ServerSpec


class ServerStore:
    def save(self, spec: ServerSpec) -> None:
        data = spec.to_dict()
        with db_session() as s:
            row = s.get(Server, spec.name)
            if row is None:
                s.add(Server(name=spec.name, spec=data, mode=spec.mode))
            else:
                row.spec = data
                row.mode = spec.mode

    def load(self, name: str) -> ServerSpec | None:
        with db_session() as s:
            row = s.get(Server, name)
            if row is None:
                return None
            data = row.spec
        if not isinstance(data, dict):
            return None
        return ServerSpec.from_dict(data)

    def delete(self, name: str) -> None:
        with db_session() as s:
            row = s.get(Server, name)
            if row is not None:
                s.delete(row)
            s.query(ServerAsset).filter(ServerAsset.server_name == name).delete()

    def list_all(self) -> list[ServerSpec]:
        with db_session() as s:
            rows = s.query(Server).order_by(Server.name).all()
            specs = [r.spec for r in rows]
        out: list[ServerSpec] = []
        for data in specs:
            if isinstance(data, dict):
                out.append(ServerSpec.from_dict(data))
        return out

    # ---- Build-context files (git clone / template render snapshots) ----

    def get_build_files(self, name: str) -> bytes | None:
        """The gzip tarball of non-regenerable build files for this server, or
        None (structured/code/remote servers have none)."""
        with db_session() as s:
            row = s.get(Server, name)
            return bytes(row.build_files) if row and row.build_files else None

    def set_build_files(self, name: str, data: bytes | None) -> None:
        """Attach (or clear) the build-files tarball for a server. The server
        row must already exist (save() it first)."""
        with db_session() as s:
            row = s.get(Server, name)
            if row is not None:
                row.build_files = data
