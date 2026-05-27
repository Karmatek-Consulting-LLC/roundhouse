"""ServerSpec + EnvVar — the on-disk JSON shape we persist per server."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


MODE_STRUCTURED = "structured"
MODE_CODE = "code"


@dataclass(slots=True)
class EnvVar:
    """A per-server environment variable.

    When `secret=True`, `value` carries Laravel-format ciphertext (see
    laravel_crypto). The plaintext is never persisted to disk; codegen /
    container spawn decrypts on demand. Plain rows store plaintext as
    before."""

    name: str
    value: str = ""
    secret: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "EnvVar | None":
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        value = data.get("value", "")
        if not isinstance(value, str):
            value = ""
        secret = bool(data.get("secret"))
        return cls(name=name, value=value, secret=secret)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "value": self.value}
        if self.secret:
            out["secret"] = True
        return out


def normalize_env_name(n: str) -> str:
    s = (n or "").strip().upper()
    return re.sub(r"[^A-Z0-9_]", "", s)


def normalize_env_imports(items: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, str):
            continue
        name = normalize_env_name(it)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _string_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, str)]


def _primitive_list(items: Any) -> list[dict]:
    if not isinstance(items, list):
        return []
    return [p for p in items if isinstance(p, dict) and "kind" in p]


@dataclass(slots=True)
class ServerSpec:
    """Persisted server definition. Primitives are dicts with a `kind`
    discriminator so the JSON layout matches the Python platform's exactly."""

    name: str
    description: str = ""
    imports: list[str] = field(default_factory=list)
    primitives: list[dict] = field(default_factory=list)
    pip_packages: list[str] = field(default_factory=list)
    env_global_imports: list[str] = field(default_factory=list)
    env_vars: list[EnvVar] = field(default_factory=list)
    replicas: int | None = None
    mode: str = MODE_STRUCTURED
    source: str | None = None
    apt_packages: list[str] = field(default_factory=list)
    middleware_defaults: dict = field(default_factory=dict)
    # None = no cap (Docker default). cpu_limit is whole CPUs (0.5 = half).
    cpu_limit: float | None = None
    memory_limit_mb: int | None = None
    # Hydrated at codegen time only - never persisted with plaintext.
    tokens: list[dict] = field(default_factory=list)

    def is_code_mode(self) -> bool:
        return self.mode == MODE_CODE

    @classmethod
    def from_dict(cls, data: dict) -> "ServerSpec":
        env_vars: list[EnvVar] = []
        for ev in data.get("env_vars", []) or []:
            parsed = EnvVar.from_dict(ev)
            if parsed:
                env_vars.append(parsed)

        replicas = data.get("replicas")
        if replicas is not None:
            try:
                replicas = int(replicas)
            except (TypeError, ValueError):
                replicas = None

        mode = MODE_CODE if data.get("mode") == MODE_CODE else MODE_STRUCTURED
        source = data.get("source") if isinstance(data.get("source"), str) else None

        mw_defaults = data.get("middleware_defaults")
        if not isinstance(mw_defaults, dict):
            mw_defaults = {}

        def _opt_float(v: Any) -> float | None:
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if f > 0 else None

        def _opt_int(v: Any) -> int | None:
            if v is None:
                return None
            try:
                i = int(v)
            except (TypeError, ValueError):
                return None
            return i if i > 0 else None

        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            imports=_string_list(data.get("imports", [])),
            primitives=_primitive_list(data.get("primitives", [])),
            pip_packages=_string_list(data.get("pip_packages", [])),
            env_global_imports=normalize_env_imports(data.get("env_global_imports", [])),
            env_vars=env_vars,
            replicas=replicas,
            mode=mode,
            source=source,
            apt_packages=_string_list(data.get("apt_packages", [])),
            middleware_defaults=mw_defaults,
            cpu_limit=_opt_float(data.get("cpu_limit")),
            memory_limit_mb=_opt_int(data.get("memory_limit_mb")),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "imports": self.imports,
            "primitives": self.primitives,
            "pip_packages": self.pip_packages,
            "env_global_imports": self.env_global_imports,
            "env_vars": [v.to_dict() for v in self.env_vars],
            "replicas": self.replicas,
            "mode": self.mode,
            "source": self.source,
            "apt_packages": self.apt_packages,
            "middleware_defaults": self.middleware_defaults,
            "cpu_limit": self.cpu_limit,
            "memory_limit_mb": self.memory_limit_mb,
        }
