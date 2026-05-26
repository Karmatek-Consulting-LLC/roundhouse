"""Filesystem persistence for ServerSpecs - one JSON file per server."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.services.spec import ServerSpec


class ServerStore:
    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)

    def server_dir(self, name: str) -> Path:
        return self.base_dir / name

    def _spec_path(self, name: str) -> Path:
        return self.server_dir(name) / "server.json"

    def save(self, spec: ServerSpec) -> None:
        d = self.server_dir(spec.name)
        d.mkdir(parents=True, exist_ok=True)
        text = json.dumps(spec.to_dict(), indent=4, ensure_ascii=False)
        self._spec_path(spec.name).write_text(text, encoding="utf-8")

    def load(self, name: str) -> ServerSpec | None:
        p = self._spec_path(name)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return ServerSpec.from_dict(data)

    def delete(self, name: str) -> None:
        d = self.server_dir(name)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    def list_all(self) -> list[ServerSpec]:
        if not self.base_dir.is_dir():
            return []
        out: list[ServerSpec] = []
        for entry in sorted(self.base_dir.iterdir()):
            if not entry.is_dir():
                continue
            spec = self.load(entry.name)
            if spec is not None:
                out.append(spec)
        return out
