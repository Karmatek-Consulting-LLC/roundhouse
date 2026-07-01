"""ServerSpec + EnvVar — the on-disk JSON shape we persist per server."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


MODE_STRUCTURED = "structured"
MODE_CODE = "code"
MODE_REMOTE = "remote"

# Modes whose MCP server the platform does not author, so per-tool policy can't
# be baked into generated functions and is enforced in _PlatformMiddleware
# instead: code-first (user's server.py, fronted by a loopback proxy) and remote
# (an external MCP server fronted by an outbound proxy). Structured servers are
# codegen'd in-process but share the same middleware enforcement path.
_PROXIED_MODES = frozenset({MODE_CODE, MODE_REMOTE})
_VALID_MODES = frozenset({MODE_STRUCTURED, MODE_CODE, MODE_REMOTE})


@dataclass(slots=True)
class EnvVar:
    """A per-server environment variable.

    When `secret=True`, `value` carries an encrypted envelope (see
    `app.crypto`). The plaintext is never persisted to disk; codegen /
    container spawn decrypts on demand. Non-secret rows store plaintext."""

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


def _remote_headers_list(items: Any) -> list[dict]:
    """Outbound header -> env-var mapping for a remote proxy. Each entry is
    {"header": "<HTTP header name>", "env": "<ENV_VAR_NAME>"}; the secret value
    itself lives in env_vars (secret=True) under that env name and is never
    stored here. Non-secret mapping only."""
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        header = str(it.get("header") or "").strip()
        env = normalize_env_name(str(it.get("env") or ""))
        if header and env:
            out.append({"header": header, "env": env})
    return out


def _placement_constraints_list(items: Any) -> list[dict]:
    """Swarm node-label placement selectors. Each entry is {"key","value"} and
    is translated at deploy time into a Docker `node.labels.<key>==<value>`
    service constraint. De-duplicated; blank keys/values are dropped."""
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    seen: set[tuple[str, str]] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        value = str(it.get("value") or "").strip()
        if not key or not value or (key, value) in seen:
            continue
        seen.add((key, value))
        out.append({"key": key, "value": value})
    return out


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
    # Swarm node-label placement selectors chosen at deploy time. Each entry is
    # {"key","value"}, translated to a `node.labels.<key>==<value>` service
    # constraint (all ANDed). Empty = schedule anywhere. Ignored off Swarm.
    placement_constraints: list[dict] = field(default_factory=list)
    # Set for servers imported via "Deploy from Git" - lets the platform
    # re-clone the same source on "Update from Git". None for other servers.
    git_url: str | None = None
    git_ref: str | None = None
    # Remote-proxy (mode="remote") fields. remote_url is the upstream MCP
    # endpoint; remote_headers maps outbound HTTP headers to the env vars that
    # carry their (secret) values. The values live in env_vars, never here.
    remote_url: str | None = None
    remote_headers: list[dict] = field(default_factory=list)
    # Default-deny for tools with no assigned scopes, enforced in middleware.
    # Defaulted by mode at create time: remote=True (locked until granted),
    # structured/code=False (open unless scoped) so existing servers don't break.
    deny_unlisted: bool = False
    # Hydrated at codegen time only - never persisted with plaintext.
    tokens: list[dict] = field(default_factory=list)

    def is_code_mode(self) -> bool:
        return self.mode == MODE_CODE

    def is_remote_mode(self) -> bool:
        return self.mode == MODE_REMOTE

    def is_proxied(self) -> bool:
        """True when the platform fronts an MCP server it didn't author (code or
        remote) with a generated proxy. Such servers carry DISCOVERED primitives
        as a scopes-only overlay and enforce scopes via middleware, never via
        baked require_scopes decorators."""
        return self.mode in _PROXIED_MODES

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

        raw_mode = data.get("mode")
        mode = raw_mode if raw_mode in _VALID_MODES else MODE_STRUCTURED
        source = data.get("source") if isinstance(data.get("source"), str) else None
        remote_url = data.get("remote_url") if isinstance(data.get("remote_url"), str) else None

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
            placement_constraints=_placement_constraints_list(data.get("placement_constraints", [])),
            git_url=data.get("git_url") if isinstance(data.get("git_url"), str) else None,
            git_ref=data.get("git_ref") if isinstance(data.get("git_ref"), str) else None,
            remote_url=remote_url,
            remote_headers=_remote_headers_list(data.get("remote_headers", [])),
            deny_unlisted=bool(data.get("deny_unlisted")),
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
            "placement_constraints": self.placement_constraints,
            "git_url": self.git_url,
            "git_ref": self.git_ref,
            "remote_url": self.remote_url,
            "remote_headers": self.remote_headers,
            "deny_unlisted": self.deny_unlisted,
        }
