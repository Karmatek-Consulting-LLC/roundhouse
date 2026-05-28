"""Parse a cloned repo's optional `roundhouse.json` deploy descriptor.

Roundhouse owns the Dockerfile for code-mode servers, so a git-imported repo
declares its needs via a manifest instead of shipping a Dockerfile:

    {
      "env": [
        {"name": "LM_COMPANY", "secret": false, "description": "..."},
        {"name": "LM_BEARER_TOKEN", "secret": true}
      ],
      "pip_packages": ["httpx"],
      "apt_packages": []
    }

`roundhouse.json` is authoritative. When it omits a section we fall back to
conventional files: `requirements.txt` for pip packages and `env.example`
for env var names (seeded non-secret, empty).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.services.spec import EnvVar, normalize_env_name

# codegen always installs fastmcp at a pinned version - never let a repo's
# own pin sneak in via requirements.txt and shadow / conflict with it.
_RESERVED_PIP = {"fastmcp"}


@dataclass(slots=True)
class GitManifest:
    env_vars: list[EnvVar] = field(default_factory=list)
    pip_packages: list[str] = field(default_factory=list)
    apt_packages: list[str] = field(default_factory=list)


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _pip_pkg_name(requirement: str) -> str:
    """Bare distribution name from a requirement line, lowercased."""
    name = requirement
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip().lower()


def _parse_requirements(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if _pip_pkg_name(line) in _RESERVED_PIP:
            continue
        out.append(line)
    return out


def _parse_env_example(path: Path, exclude: set[str]) -> list[EnvVar]:
    if not path.is_file():
        return []
    out: list[EnvVar] = []
    seen: set[str] = set(exclude)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = normalize_env_name(line.split("=", 1)[0])
        if name and name not in seen:
            seen.add(name)
            out.append(EnvVar(name=name, value="", secret=False))
    return out


def parse_manifest(repo_dir: Path) -> GitManifest:
    """Read roundhouse.json (+ requirements.txt / env.example fallbacks) into
    spec-ready fields. Values are always empty - the importer pre-populates the
    editor with the declared names so the operator fills them before deploy."""
    repo_dir = Path(repo_dir)
    data: dict = {}
    manifest = repo_dir / "roundhouse.json"
    if manifest.is_file():
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}

    env_vars: list[EnvVar] = []
    seen_env: set[str] = set()
    for row in data.get("env") or []:
        if not isinstance(row, dict):
            continue
        name = normalize_env_name(str(row.get("name") or ""))
        if not name or name in seen_env:
            continue
        seen_env.add(name)
        env_vars.append(EnvVar(name=name, value="", secret=bool(row.get("secret"))))

    pip_packages = _str_list(data.get("pip_packages"))
    pip_packages = [p for p in pip_packages if _pip_pkg_name(p) not in _RESERVED_PIP]
    apt_packages = _str_list(data.get("apt_packages"))

    if not pip_packages:
        pip_packages = _parse_requirements(repo_dir / "requirements.txt")
    if not env_vars:
        env_vars = _parse_env_example(repo_dir / "env.example", seen_env)

    return GitManifest(env_vars=env_vars, pip_packages=pip_packages, apt_packages=apt_packages)
