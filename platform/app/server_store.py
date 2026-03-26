"""JSON-file persistence for server specs."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import SERVERS_DATA_DIR
from app.models import ServerSpec


class ServerStore:
    def __init__(self) -> None:
        self.base_dir = SERVERS_DATA_DIR

    def _spec_path(self, name: str) -> Path:
        return self.base_dir / name / "server.json"

    def save(self, spec: ServerSpec) -> None:
        path = self._spec_path(spec.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec.model_dump_json(indent=2))

    def load(self, name: str) -> ServerSpec | None:
        path = self._spec_path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ServerSpec(**data)

    def delete(self, name: str) -> None:
        import shutil
        server_dir = self.base_dir / name
        if server_dir.exists():
            shutil.rmtree(server_dir)

    def list_all(self) -> list[ServerSpec]:
        results = []
        if not self.base_dir.exists():
            return results
        for server_dir in sorted(self.base_dir.iterdir()):
            spec_path = server_dir / "server.json"
            if spec_path.exists():
                data = json.loads(spec_path.read_text())
                results.append(ServerSpec(**data))
        return results
