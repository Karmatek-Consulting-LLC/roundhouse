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


# Port layout.
#   BACKEND_PORT - where the MCP server itself listens. For structured servers
#                  this is the only process. For code-first servers it's the
#                  user's own server.py, whose port we cannot change.
#   PROXY_PORT   - code-first only: the platform proxy listens here, applies the
#                  same middleware structured servers get baked in, and forwards
#                  to the user's server on BACKEND_PORT. Traefik/K8s route to
#                  whichever port is "public" for the server (see route_port_for).
BACKEND_PORT = 8000
PROXY_PORT = 8001


def route_port_for(spec: ServerSpec) -> int:
    """Container port the orchestrator should route external traffic to.

    Code-first servers are fronted by the platform proxy (PROXY_PORT) so their
    tool calls flow through platform middleware; structured servers are routed
    straight to their own process (BACKEND_PORT)."""
    return PROXY_PORT if spec.is_code_mode() else BACKEND_PORT


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
        if _PLATFORM_LOG.isEnabledFor(_logging.DEBUG):
            try:
                _PLATFORM_LOG.debug(_json.dumps(
                    {"event": "mcp.call.start", "kind": kind, "name": name,
                     "client_id": client_id, "arguments": arguments},
                    default=str,
                ))
            except Exception:
                pass
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
            duration_ms = round((_time.perf_counter() - started) * 1000, 2)
            _metrics_record(kind, name, client_id, duration_ms, err_type)
            if cfg.get("log_calls", True):
                rec = {
                    "event": "mcp.call",
                    "kind": kind,
                    "name": name,
                    "client_id": client_id,
                    "duration_ms": duration_ms,
                    "error": err_type,
                }
                debug_on = _PLATFORM_LOG.isEnabledFor(_logging.DEBUG)
                if (cfg.get("log_arguments") or debug_on) and arguments is not None:
                    try:
                        rec["arguments"] = arguments
                    except Exception:
                        pass
                try:
                    if err_type is not None:
                        _PLATFORM_LOG.warning(_json.dumps(rec, default=str))
                    else:
                        _PLATFORM_LOG.info(_json.dumps(rec, default=str))
                except Exception:
                    pass
'''


# Metrics module is emitted *before* the middleware class so _invoke can
# call _metrics_record() directly. Kept as its own block for readability.
_METRICS_MODULE_SRC = '''
# --- Metrics (auto-generated) ---
from collections import deque as _deque

# Per-(kind,name): rolling sample of durations + counters.
_METRICS_PRIM: dict = {}
# Per-(name,client_id) call counters.
_METRICS_BY_CLIENT: dict = {}
_METRICS_STARTED = _time.time()
_DURATION_SAMPLES = 256


def _metrics_record(kind, name, client_id, duration_ms, err_type):
    key = (kind, name)
    p = _METRICS_PRIM.get(key)
    if p is None:
        p = {
            "kind": kind, "name": name,
            "calls": 0, "errors": 0,
            "rate_limited": 0, "concurrency_denied": 0,
            "last_call_ts": 0.0,
            "durations": _deque(maxlen=_DURATION_SAMPLES),
        }
        _METRICS_PRIM[key] = p
    p["calls"] += 1
    p["last_call_ts"] = _time.time()
    p["durations"].append(float(duration_ms))
    if err_type is not None:
        p["errors"] += 1
        if err_type == "ToolError":
            # Tag the two middleware-injected rejection paths so we can split
            # them out from user errors in the dashboard.
            # (Best-effort: the exception message carries the discriminator.)
            pass

    tk = (str(name), str(client_id or ""))
    t = _METRICS_BY_CLIENT.get(tk)
    if t is None:
        t = {"name": name, "client_id": client_id, "calls": 0, "last_call_ts": 0.0}
        _METRICS_BY_CLIENT[tk] = t
    t["calls"] += 1
    t["last_call_ts"] = _time.time()


def _percentile(samples, q):
    if not samples:
        return None
    arr = sorted(samples)
    idx = max(0, min(len(arr) - 1, int(q * (len(arr) - 1))))
    return round(arr[idx], 2)


def _metrics_snapshot():
    primitives = []
    for p in _METRICS_PRIM.values():
        durs = list(p["durations"])
        primitives.append({
            "kind": p["kind"], "name": p["name"],
            "calls": p["calls"], "errors": p["errors"],
            "last_call_ts": p["last_call_ts"],
            "p50_ms": _percentile(durs, 0.50),
            "p95_ms": _percentile(durs, 0.95),
            "p99_ms": _percentile(durs, 0.99),
            "samples": len(durs),
        })
    tokens = []
    for t in _METRICS_BY_CLIENT.values():
        tokens.append({
            "name": t["name"], "client_id": t["client_id"],
            "calls": t["calls"], "last_call_ts": t["last_call_ts"],
        })
    primitives.sort(key=lambda r: (-r["calls"], r["name"]))
    tokens.sort(key=lambda r: (-r["calls"], r.get("client_id") or ""))
    return {
        "started_ts": _METRICS_STARTED,
        "now_ts": _time.time(),
        "primitives": primitives,
        "tokens": tokens,
    }
'''


_METRICS_ROUTE_SRC = '''
# --- /metrics + /healthz routes (auto-generated) ---
try:
    from starlette.responses import JSONResponse as _JSONResponse, PlainTextResponse as _PlainTextResponse
except Exception:  # noqa: BLE001 - starlette ships with fastmcp
    _JSONResponse = None
    _PlainTextResponse = None


@mcp.custom_route("/metrics", methods=["GET"])
async def _platform_metrics(request):
    if _JSONResponse is None or _PlainTextResponse is None:
        return None
    auth = request.headers.get("authorization", "")
    expected = "Bearer " + _METRICS_TOKEN
    if auth != expected:
        return _PlainTextResponse("unauthorized", status_code=401)
    return _JSONResponse(_metrics_snapshot())


@mcp.custom_route("/healthz", methods=["GET"])
async def _platform_healthz(request):
    """Liveness probe used by Docker HEALTHCHECK + the platform UI. No auth -
    Docker has no way to send headers and the response carries no secrets."""
    if _PlainTextResponse is None:
        return None
    return _PlainTextResponse("ok", status_code=200)
'''


def _gen_middleware(spec: ServerSpec, metrics_token: str) -> tuple[list[str], str]:
    """Return (extra_import_lines, middleware_body).

    The body is inserted after the `mcp = FastMCP(...)` line and is followed
    by `mcp.add_middleware(_PlatformMiddleware())` and the /metrics route."""
    config_map = _merge_middleware_config(spec)
    imports = [
        "import asyncio as _asyncio",
        "import json as _json",
        "import logging as _logging",
        "import os as _os",
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
        "_PLATFORM_LOG_LEVEL_NAME = (_os.environ.get('LOG_LEVEL') or 'INFO').strip().upper()",
        "_PLATFORM_LOG_LEVEL = _logging.getLevelName(_PLATFORM_LOG_LEVEL_NAME)",
        "if not isinstance(_PLATFORM_LOG_LEVEL, int):",
        "    _PLATFORM_LOG_LEVEL = _logging.INFO",
        "    _PLATFORM_LOG_LEVEL_NAME = 'INFO'",
        "_PLATFORM_LOG.setLevel(_PLATFORM_LOG_LEVEL)",
        "if not _PLATFORM_LOG.handlers:",
        "    _h = _logging.StreamHandler()",
        "    _h.setFormatter(_logging.Formatter('%(asctime)s %(levelname)s %(message)s'))",
        "    _PLATFORM_LOG.addHandler(_h)",
        "    _PLATFORM_LOG.propagate = False",
        "_PLATFORM_LOG.info(_json.dumps({'event': 'mcp.startup', 'log_level': _PLATFORM_LOG_LEVEL_NAME}))",
        "",
        "_METRICS_TOKEN = " + _py_string(metrics_token),
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
        _METRICS_MODULE_SRC,
        _MIDDLEWARE_CLASS_SRC,
        "mcp.add_middleware(_PlatformMiddleware())",
        _METRICS_ROUTE_SRC,
        "",
    ]
    return imports, "\n".join(body_lines)


def generate_server_py(spec: ServerSpec, *, format_output: bool = True) -> str:
    from app.services.metrics_auth import metrics_token_for

    auth_enabled = bool(spec.tokens)
    any_scoped = auth_enabled and _any_primitive_has_scopes(spec.primitives)

    primitives_code = "\n".join(_gen_primitive(p, auth_enabled) for p in spec.primitives).strip()

    import_lines: list[str] = [
        "from fastmcp import FastMCP",
        # Convenience for tool/resource code: reference uploaded assets as
        # `(ASSETS_DIR / "foo.json").read_text()`. The directory exists in
        # the image even when empty (see codegen.write_build_context).
        "from pathlib import Path as _AssetsPath",
        'ASSETS_DIR = _AssetsPath("/app/assets")',
    ]
    auth_imports: list[str] = []
    if auth_enabled:
        auth_imports.append("StaticTokenVerifier")
    if any_scoped:
        auth_imports.append("require_scopes")
    if auth_imports:
        import_lines.append("from fastmcp.server.auth import " + ", ".join(auth_imports))

    mw_imports, mw_body = _gen_middleware(spec, metrics_token_for(spec.name))
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


def _proxy_supervisor_src() -> str:
    """The `__main__` block for the code-first proxy: launch the user's
    server.py as a child, wait for it to bind, then run the proxy. If the
    child dies we exit so the orchestrator restarts both together."""
    return f'''
if __name__ == "__main__":
    import os
    import socket
    import subprocess
    import sys
    import threading
    import time

    # The user's server owns BACKEND_PORT; we never touch their code or port.
    _child = subprocess.Popen([sys.executable, "server.py"])

    def _reap():
        code = _child.wait()
        # Bring the whole container down so the orchestrator restarts cleanly
        # rather than leaving a proxy fronting a dead backend.
        os._exit(code if isinstance(code, int) else 1)

    threading.Thread(target=_reap, daemon=True).start()

    # Best-effort readiness wait so the first proxied request doesn't race the
    # backend's startup. We probe the TCP port (not /healthz) so it works even
    # for backends that expose no health route.
    _deadline = time.monotonic() + 30
    while time.monotonic() < _deadline:
        try:
            with socket.create_connection(("127.0.0.1", {BACKEND_PORT}), timeout=1):
                break
        except OSError:
            time.sleep(0.5)

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port={PROXY_PORT},
        stateless_http=True,
        json_response=True,
    )
'''


def generate_proxy_py(spec: ServerSpec, *, format_output: bool = True) -> str:
    """Generate the platform proxy that fronts a code-first server.

    The proxy re-exposes the user's MCP server through the SAME middleware that
    structured servers get (call logging, rate limiting, concurrency caps,
    /metrics, /healthz) - the only way to apply platform policy to code we don't
    author. It forwards to the user's server over loopback on BACKEND_PORT."""
    from app.services.metrics_auth import metrics_token_for

    mw_imports, mw_body = _gen_middleware(spec, metrics_token_for(spec.name))
    backend_url = f"http://127.0.0.1:{BACKEND_PORT}/mcp"
    proxy_name = spec.name + "-proxy"

    lines: list[str] = [
        '"""Auto-generated platform proxy for a code-first MCP server.',
        "",
        f"Fronts the user's server (127.0.0.1:{BACKEND_PORT}) and re-exposes it on",
        f"port {PROXY_PORT} with platform middleware that code-first servers would",
        "otherwise bypass. Generated by codegen.generate_proxy_py - do not edit.",
        '"""',
        *mw_imports,
        "",
        # Prefer the modern create_proxy API (fastmcp >= 3.3); fall back to the
        # deprecated FastMCP.as_proxy on older builds. ProxyClient forwards
        # client context (auth header, sampling, progress) to the backend.
        "try:",
        "    from fastmcp.server import create_proxy as _create_proxy",
        "    from fastmcp.server.providers.proxy import ProxyClient as _ProxyClient",
        "    mcp = _create_proxy(_ProxyClient("
        + _py_string(backend_url)
        + "), name="
        + _py_string(proxy_name)
        + ")",
        "except Exception:  # noqa: BLE001 - older fastmcp without create_proxy",
        "    from fastmcp import FastMCP as _FastMCP",
        "    mcp = _FastMCP.as_proxy("
        + _py_string(backend_url)
        + ", name="
        + _py_string(proxy_name)
        + ")",
        mw_body,
        _proxy_supervisor_src(),
    ]
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
    # Copy the whole build context so multi-file servers work (helper modules,
    # data files, git-imported repos). write_build_context always writes
    # server.py plus an assets/ dir here, so ASSETS_DIR=/app/assets stays valid
    # even when nothing was uploaded. Roundhouse owns the Dockerfile, so the
    # context is small and self-contained.
    lines.append("COPY . .")
    # Code-first servers run behind the platform proxy: the proxy listens on
    # PROXY_PORT (the public/routed port) and supervises the user's server.py
    # on BACKEND_PORT. Structured servers are a single process on BACKEND_PORT.
    code_mode = spec.is_code_mode()
    public_port = PROXY_PORT if code_mode else BACKEND_PORT
    entry = "proxy.py" if code_mode else "server.py"
    lines.append(f"EXPOSE {public_port}")
    # python's urllib avoids needing curl in the base image. exit 0 on 200,
    # non-zero otherwise; Docker flips the container to unhealthy after the
    # configured retries. Probe the public port so health reflects whatever
    # actually serves traffic (the proxy in code mode, the server otherwise).
    lines.append(
        'HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\\n'
        '  CMD python -c "import urllib.request,sys; '
        f"r=urllib.request.urlopen('http://127.0.0.1:{public_port}/healthz', timeout=2); "
        'sys.exit(0 if r.status==200 else 1)" || exit 1'
    )
    lines.append(f'CMD ["python", "{entry}"]')
    lines.append("")
    return "\n".join(lines)


def write_build_context(spec: ServerSpec, output_dir: Path | str, custom_ca: str | None = None) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    server_py = (spec.source or "") if spec.is_code_mode() else generate_server_py(spec)
    (out / "server.py").write_text(server_py, encoding="utf-8")
    (out / "Dockerfile").write_text(generate_dockerfile(spec, custom_ca), encoding="utf-8")
    # Code-first servers get a platform proxy alongside their own server.py so
    # their tool calls pass through platform middleware. Structured servers have
    # the middleware baked into server.py and need no proxy - drop any stale one.
    proxy_path = out / "proxy.py"
    if spec.is_code_mode():
        proxy_path.write_text(generate_proxy_py(spec), encoding="utf-8")
    elif proxy_path.exists():
        proxy_path.unlink()
    # Ensure assets/ exists so the Dockerfile's COPY doesn't fail when the
    # user hasn't uploaded any files. Existing assets are left in place -
    # the asset store writes to this same directory directly.
    (out / "assets").mkdir(exist_ok=True)
    ca_path = out / "custom-ca.crt"
    if _has_custom_ca(custom_ca):
        ca_path.write_text(custom_ca, encoding="utf-8")
    elif ca_path.exists():
        ca_path.unlink()
    return out
