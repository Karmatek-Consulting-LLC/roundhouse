"""Generate Python FastMCP server.py and Dockerfile from a ServerSpec."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.formatter import format_python
from app.services.spec import ServerSpec


# fastmcp version pinned to a known-good release that exposes
# StaticTokenVerifier and require_scopes at fastmcp.server.auth. Bump
# deliberately - generated server.py is coupled to this API surface.
FASTMCP_VERSION = "3.3.1"


_PYTHON_TYPE_MAP = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list": "list",
    "dict": "dict",
}


def _py_string(s: str) -> str:
    """Safe Python string literal. JSON encoding is valid Python."""
    return json.dumps(s, ensure_ascii=False)


def _py_dict(value: dict | list) -> str:
    """Python dict/list literal. JSON encoding is valid Python for
    str/int/list/dict shapes - which is all we emit in the tokens map."""
    return json.dumps(value, ensure_ascii=False)


def _indent(code: str, level: int = 1) -> str:
    prefix = "    " * level
    out_lines: list[str] = []
    for line in code.rstrip().split("\n"):
        out_lines.append("" if not line.strip() else prefix + line)
    return "\n".join(out_lines)


def _param_signature(params: list[dict]) -> str:
    required: list[str] = []
    optional: list[str] = []
    for p in params or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        type_ = _PYTHON_TYPE_MAP.get(p.get("type", "str"), "str")
        name = p["name"]
        if p.get("required", True):
            required.append(f"{name}: {type_}")
        else:
            default = p.get("default")
            default_repr = "None" if default is None else _py_string(str(default))
            optional.append(f"{name}: {type_} = {default_repr}")
    return ", ".join([*required, *optional])


def _any_primitive_has_scopes(primitives: list[dict]) -> bool:
    return any(
        isinstance(p, dict) and isinstance(p.get("scopes"), list) and p["scopes"]
        for p in primitives
    )


def _auth_clause(p: dict, auth_enabled: bool) -> str:
    if not auth_enabled:
        return ""
    scopes = p.get("scopes") or []
    if not isinstance(scopes, list):
        return ""
    args = [_py_string(s) for s in scopes if isinstance(s, str) and s]
    if not args:
        return ""
    return "auth=require_scopes(" + ", ".join(args) + ")"


def _decorator_args(positional: str, auth: str) -> str:
    parts = [p for p in (positional, auth) if p]
    return ", ".join(parts)


def _tokens_map(tokens: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for t in tokens:
        plain = str(t.get("token") or "")
        if not plain:
            continue
        out[plain] = {
            "client_id": str(t.get("name") or ""),
            "scopes": [s for s in (t.get("scopes") or []) if isinstance(s, str) and s],
        }
    return out


def _gen_tool(t: dict, auth_enabled: bool) -> str:
    sig = _param_signature(t.get("parameters", []))
    ret_py = "dict" if t.get("return_type") == "dict" else "str"
    default_body = 'return "Not implemented"' if ret_py == "str" else "return {}"
    body = (t.get("code") or "").strip() or default_body
    doc = _py_string(t.get("description") or t.get("name") or "")
    name = t.get("name", "")
    args = _decorator_args("", _auth_clause(t, auth_enabled))
    return f"\n@mcp.tool({args})\ndef {name}({sig}) -> {ret_py}:\n    {doc}\n{_indent(body)}\n"


def _gen_resource(r: dict, auth_enabled: bool) -> str:
    body = (r.get("code") or "").strip() or f'return "{r.get("name", "")}"'
    doc = _py_string(r.get("description") or r.get("name") or "")
    uri = _py_string(str(r.get("uri", "")))
    name = r.get("name", "")
    args = _decorator_args(uri, _auth_clause(r, auth_enabled))
    return f"\n@mcp.resource({args})\ndef {name}() -> str:\n    {doc}\n{_indent(body)}\n"


def _gen_resource_template(rt: dict, auth_enabled: bool) -> str:
    import re

    template = str(rt.get("uri_template", ""))
    params = re.findall(r"\{(\w+)\}", template)
    sig = ", ".join(f"{p}: str" for p in params)
    body = (rt.get("code") or "").strip() or f'return "{rt.get("name", "")}"'
    doc = _py_string(rt.get("description") or rt.get("name") or "")
    uri = _py_string(template)
    name = rt.get("name", "")
    args = _decorator_args(uri, _auth_clause(rt, auth_enabled))
    return f"\n@mcp.resource({args})\ndef {name}({sig}) -> str:\n    {doc}\n{_indent(body)}\n"


def _gen_prompt(p: dict, auth_enabled: bool) -> str:
    sig = _param_signature(p.get("parameters", []))
    body = (p.get("code") or "").strip() or 'return "Not implemented"'
    doc = _py_string(p.get("description") or p.get("name") or "")
    name = p.get("name", "")
    args = _decorator_args("", _auth_clause(p, auth_enabled))
    return f"\n@mcp.prompt({args})\ndef {name}({sig}) -> str:\n    {doc}\n{_indent(body)}\n"


def _gen_primitive(p: dict, auth_enabled: bool) -> str:
    return {
        "tool": _gen_tool,
        "resource": _gen_resource,
        "resource_template": _gen_resource_template,
        "prompt": _gen_prompt,
    }.get(p.get("kind", ""), lambda *_: "")(p, auth_enabled)


# Default middleware config baked into every generated server. Per-server
# `middleware_defaults` overrides these; per-primitive `middleware` overrides
# the server defaults. Keep keys stable - they're read by the generated
# _PlatformMiddleware class.
_MIDDLEWARE_BASE_DEFAULTS: dict = {
    "log_calls": True,
    "log_arguments": False,
    "max_argument_bytes": 1_000_000,
}


def _merge_middleware_config(spec: ServerSpec) -> dict[str, dict]:
    """Resolve the per-primitive middleware config map emitted into server.py.

    The map always contains a "_default" entry (base defaults overlaid with
    spec.middleware_defaults). Entries for individual primitives are emitted
    only when that primitive carries a non-empty `middleware` dict."""
    base = dict(_MIDDLEWARE_BASE_DEFAULTS)
    if isinstance(spec.middleware_defaults, dict):
        base.update({k: v for k, v in spec.middleware_defaults.items() if isinstance(k, str)})
    out: dict[str, dict] = {"_default": base}
    for p in spec.primitives:
        name = p.get("name")
        cfg = p.get("middleware")
        if not isinstance(name, str) or not name or not isinstance(cfg, dict) or not cfg:
            continue
        out[name] = {k: v for k, v in cfg.items() if isinstance(k, str)}
    return out


_MIDDLEWARE_CLASS_SRC = '''
# In-process rate-limit + concurrency state. Keyed on (kind:name, client_id).
# Buckets: [tokens_available, last_refill_perf_counter]
_RATE_BUCKETS: dict = {}
# Gates: {"in_flight": int, "cap": int}
_CONCURRENCY_GATES: dict = {}
_MW_LOCK = _asyncio.Lock()
# Sentinel: distinguishes "no limit configured" (None) from "capacity exceeded".
_GATE_DENIED = object()


async def _rate_allow(key, rpm):
    """Token-bucket admission. rpm <= 0 / non-numeric disables the check."""
    if not isinstance(rpm, (int, float)) or rpm <= 0:
        return True
    now = _time.perf_counter()
    async with _MW_LOCK:
        bucket = _RATE_BUCKETS.get(key)
        if bucket is None:
            bucket = [float(rpm), now]
            _RATE_BUCKETS[key] = bucket
        else:
            elapsed = max(0.0, now - bucket[1])
            bucket[0] = min(float(rpm), bucket[0] + elapsed * (rpm / 60.0))
            bucket[1] = now
        if bucket[0] >= 1.0:
            bucket[0] -= 1.0
            return True
        return False


async def _acquire_gate(key, cap):
    """Non-blocking concurrency admission. Returns:
      None         - no cap configured, no release needed
      _GATE_DENIED - at capacity, caller should reject
      info dict    - admitted, caller must pass it to _release_gate"""
    if not isinstance(cap, int) or cap <= 0:
        return None
    async with _MW_LOCK:
        info = _CONCURRENCY_GATES.get(key)
        if info is None or info["cap"] != cap:
            info = {"in_flight": 0, "cap": cap}
            _CONCURRENCY_GATES[key] = info
        if info["in_flight"] >= cap:
            return _GATE_DENIED
        info["in_flight"] += 1
        return info


async def _release_gate(info):
    if not isinstance(info, dict):
        return
    async with _MW_LOCK:
        info["in_flight"] = max(0, info["in_flight"] - 1)


class _PlatformMiddleware(_Middleware):
    """Platform-managed middleware. Per-call pipeline (configurable via
    _MIDDLEWARE_CONFIG):
      1. argument-size guard
      2. rate limit (token bucket, per name+client_id)
      3. concurrency gate (semaphore, per name+client_id)
      4. dispatch + duration timing
      5. structured request log"""

    async def on_call_tool(self, context, call_next):
        msg = context.message
        return await self._invoke("tool", getattr(msg, "name", ""), getattr(msg, "arguments", None), context, call_next)

    async def on_read_resource(self, context, call_next):
        msg = context.message
        return await self._invoke("resource", str(getattr(msg, "uri", "")), None, context, call_next)

    async def on_get_prompt(self, context, call_next):
        msg = context.message
        return await self._invoke("prompt", getattr(msg, "name", ""), getattr(msg, "arguments", None), context, call_next)

    async def _invoke(self, kind, name, arguments, context, call_next):
        cfg = _mw_config_for(name)
        client_id = _mw_client_id()
        gate_key = (f"{kind}:{name}", client_id or "")
        started = _time.perf_counter()
        err_type: str | None = None
        gate_info = None
        try:
            max_args = cfg.get("max_argument_bytes")
            if isinstance(max_args, int) and max_args > 0 and arguments is not None:
                try:
                    size = len(_json.dumps(arguments, default=str).encode("utf-8"))
                except Exception:
                    size = 0
                if size > max_args:
                    raise _ToolError(f"Arguments for {name!r} exceed {max_args} bytes")
            rpm = cfg.get("rate_limit_rpm")
            if not await _rate_allow(gate_key, rpm):
                raise _ToolError(f"Rate limit exceeded for {name!r} ({rpm} rpm)")
            admit = await _acquire_gate(gate_key, cfg.get("max_concurrent"))
            if admit is _GATE_DENIED:
                raise _ToolError(f"Concurrency cap reached for {name!r}")
            gate_info = admit
            return await call_next(context)
        except Exception as e:
            err_type = type(e).__name__
            raise
        finally:
            await _release_gate(gate_info)
            if cfg.get("log_calls", True):
                rec = {
                    "event": "mcp.call",
                    "kind": kind,
                    "name": name,
                    "client_id": client_id,
                    "duration_ms": round((_time.perf_counter() - started) * 1000, 2),
                    "error": err_type,
                }
                if cfg.get("log_arguments") and arguments is not None:
                    try:
                        rec["arguments"] = arguments
                    except Exception:
                        pass
                try:
                    _PLATFORM_LOG.info(_json.dumps(rec, default=str))
                except Exception:
                    pass
'''


def _gen_middleware(spec: ServerSpec) -> tuple[list[str], str]:
    """Return (extra_import_lines, middleware_body).

    The body is inserted after the `mcp = FastMCP(...)` line and is followed
    by a single `mcp.add_middleware(_PlatformMiddleware())` call."""
    config_map = _merge_middleware_config(spec)
    imports = [
        "import asyncio as _asyncio",
        "import json as _json",
        "import logging as _logging",
        "import time as _time",
        "from fastmcp.exceptions import ToolError as _ToolError",
        "from fastmcp.server.middleware import Middleware as _Middleware",
    ]
    body_lines = [
        "",
        "# --- Platform middleware (auto-generated) ---",
        "try:",
        "    from fastmcp.server.dependencies import get_access_token as _get_access_token",
        "except Exception:  # noqa: BLE001",
        "    _get_access_token = lambda: None",
        "",
        "_PLATFORM_LOG = _logging.getLogger('mcp.platform')",
        "_PLATFORM_LOG.setLevel(_logging.INFO)",
        "if not _PLATFORM_LOG.handlers:",
        "    _h = _logging.StreamHandler()",
        "    _h.setFormatter(_logging.Formatter('%(message)s'))",
        "    _PLATFORM_LOG.addHandler(_h)",
        "    _PLATFORM_LOG.propagate = False",
        "",
        # repr() over json.dumps so booleans / None render as valid Python.
        "_MIDDLEWARE_CONFIG = " + repr(config_map),
        "",
        "def _mw_config_for(name):",
        "    base = _MIDDLEWARE_CONFIG['_default']",
        "    override = _MIDDLEWARE_CONFIG.get(name) or {}",
        "    return {**base, **override}",
        "",
        "def _mw_client_id():",
        "    try:",
        "        tok = _get_access_token()",
        "    except Exception:",
        "        return None",
        "    return getattr(tok, 'client_id', None) if tok is not None else None",
        _MIDDLEWARE_CLASS_SRC,
        "mcp.add_middleware(_PlatformMiddleware())",
        "",
    ]
    return imports, "\n".join(body_lines)


def generate_server_py(spec: ServerSpec, *, format_output: bool = True) -> str:
    auth_enabled = bool(spec.tokens)
    any_scoped = auth_enabled and _any_primitive_has_scopes(spec.primitives)

    primitives_code = "\n".join(_gen_primitive(p, auth_enabled) for p in spec.primitives).strip()

    import_lines: list[str] = ["from fastmcp import FastMCP"]
    auth_imports: list[str] = []
    if auth_enabled:
        auth_imports.append("StaticTokenVerifier")
    if any_scoped:
        auth_imports.append("require_scopes")
    if auth_imports:
        import_lines.append("from fastmcp.server.auth import " + ", ".join(auth_imports))

    mw_imports, mw_body = _gen_middleware(spec)
    import_lines.extend(mw_imports)

    # Preserve blank-line separators in user imports - the editor relies on them.
    import_lines.extend(spec.imports)

    mcp_args = [_py_string(spec.name)]
    if auth_enabled:
        mcp_args.append("auth=StaticTokenVerifier(tokens=" + _py_dict(_tokens_map(spec.tokens)) + ")")

    lines: list[str] = [
        *import_lines,
        "",
        "mcp = FastMCP(" + ", ".join(mcp_args) + ")",
        mw_body,
        primitives_code,
        "",
    ]
    lines.append('if __name__ == "__main__":')
    lines.append("    mcp.run(")
    lines.append('        transport="streamable-http",')
    lines.append('        host="0.0.0.0",')
    lines.append("        port=8000,")
    lines.append("        stateless_http=True,")
    lines.append("        json_response=True,")
    lines.append("    )")
    lines.append("")
    output = "\n".join(lines)
    if format_output:
        return format_python(output)
    return output


def _has_custom_ca(ca: str | None) -> bool:
    return bool(ca and ca.strip())


def generate_dockerfile(spec: ServerSpec, custom_ca: str | None = None) -> str:
    lines: list[str] = [
        "FROM python:3.12-slim",
        "WORKDIR /app",
    ]
    if _has_custom_ca(custom_ca):
        # Append the corp CA to the existing trust bundle before any network
        # call. python:3.12-slim already has ca-certificates - we just edit it.
        lines.append("COPY custom-ca.crt /usr/local/share/ca-certificates/custom-ca.crt")
        lines.append(
            "RUN cat /usr/local/share/ca-certificates/custom-ca.crt >> /etc/ssl/certs/ca-certificates.crt \\"
        )
        lines.append("    && update-ca-certificates")
        lines.append("ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \\")
        lines.append("    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \\")
        lines.append("    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt")

    if spec.apt_packages:
        apt = " ".join(spec.apt_packages)
        lines.append(
            f"RUN apt-get update && apt-get install -y --no-install-recommends {apt} "
            "&& rm -rf /var/lib/apt/lists/*"
        )

    pip_install = f"fastmcp=={FASTMCP_VERSION}"
    if spec.pip_packages:
        pip_install += " " + " ".join(spec.pip_packages)
    lines.append(f"RUN pip install --no-cache-dir {pip_install}")
    lines.append("COPY server.py .")
    lines.append("EXPOSE 8000")
    lines.append('CMD ["python", "server.py"]')
    lines.append("")
    return "\n".join(lines)


def write_build_context(spec: ServerSpec, output_dir: Path | str, custom_ca: str | None = None) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    server_py = (spec.source or "") if spec.is_code_mode() else generate_server_py(spec)
    (out / "server.py").write_text(server_py, encoding="utf-8")
    (out / "Dockerfile").write_text(generate_dockerfile(spec, custom_ca), encoding="utf-8")
    ca_path = out / "custom-ca.crt"
    if _has_custom_ca(custom_ca):
        ca_path.write_text(custom_ca, encoding="utf-8")
    elif ca_path.exists():
        ca_path.unlink()
    return out
