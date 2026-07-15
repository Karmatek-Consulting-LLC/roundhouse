"""Phase 2: unified codegen + middleware scope enforcement.

Covers the structured decorator->middleware migration, the resource/template
decorator carve-out, and the unified (code + remote) proxy generator. Every
generated source is compile()-checked so a syntax slip fails loudly.
"""
from app.services import codegen
from app.services.spec import ServerSpec

TOKENS = [{"name": "client-a", "token": "secrettoken123", "scopes": ["es:read"]}]


def _compiles(src: str, filename: str = "<gen>") -> bool:
    compile(src, filename, "exec")
    return True


# ---- Structured: tool/prompt scopes move to middleware ----

def test_structured_tool_scopes_go_to_middleware_not_decorator():
    spec = ServerSpec(
        name="s",
        primitives=[
            {"kind": "tool", "name": "search", "scopes": ["es:read"], "code": "return 'ok'"},
            {"kind": "tool", "name": "open_tool", "code": "return 'ok'"},
        ],
        tokens=TOKENS,
    )
    src = codegen.generate_server_py(spec, format_output=False)
    assert _compiles(src)
    # No baked auth= decorator on the tool anymore.
    assert "@mcp.tool()" in src
    assert "auth=require_scopes" not in src
    # Scope lives in the middleware config + helpers + list filter exist.
    assert "'required_scopes': ['es:read']" in src
    assert "def _mw_scope_allows(" in src
    assert "def _mw_client_scopes(" in src
    assert "async def on_list_tools(" in src
    assert "_MW_AUTH_ENABLED = True" in src
    assert "_MW_DENY_UNLISTED = False" in src  # structured defaults to open


def test_structured_without_tokens_disables_enforcement():
    spec = ServerSpec(
        name="s",
        primitives=[{"kind": "tool", "name": "t", "scopes": ["x"], "code": "return 1"}],
    )
    src = codegen.generate_server_py(spec, format_output=False)
    assert _compiles(src)
    assert "_MW_AUTH_ENABLED = False" in src
    # No StaticTokenVerifier without tokens.
    assert "StaticTokenVerifier" not in src


# ---- Structured: resource/template keep decorators (fail-safe carve-out) ----

def test_resource_scopes_still_use_require_scopes_decorator():
    spec = ServerSpec(
        name="s",
        primitives=[
            {"kind": "resource", "name": "doc", "uri": "x://doc", "scopes": ["es:read"], "code": "return ''"},
        ],
        tokens=TOKENS,
    )
    src = codegen.generate_server_py(spec, format_output=False)
    assert _compiles(src)
    assert "from fastmcp.server.auth import" in src and "require_scopes" in src
    assert "auth=require_scopes(\"es:read\")" in src
    # Resource scope NOT duplicated into the middleware config map (decorator
    # owns it); the helper's own reference to required_scopes is unrelated.
    assert "'required_scopes': ['es:read']" not in src


# ---- Remote proxy ----

def _remote_spec():
    return ServerSpec(
        name="elastic",
        mode="remote",
        remote_url="https://kibana.example/api/agent_builder/mcp",
        remote_headers=[{"header": "Authorization", "env": "RH_REMOTE_AUTHORIZATION"}],
        deny_unlisted=True,
        primitives=[{"kind": "tool", "name": "platform_core_search", "scopes": ["es:read"], "discovered": True}],
        tokens=TOKENS,
    )


def test_remote_proxy_generation():
    src = codegen.generate_proxy_py(_remote_spec(), format_output=False)
    assert _compiles(src)
    assert "FastMCPProxy(client_factory=" in src
    assert "StreamableHttpTransport" in src
    # Header injected from env; trust boundary closed.
    assert '_os.environ.get("RH_REMOTE_AUTHORIZATION", "")' in src
    assert "forward_incoming_headers = False" in src
    # Auth + scope enforcement wired; default-deny for unlisted.
    assert "StaticTokenVerifier" in src
    assert "_MW_DENY_UNLISTED = True" in src
    assert "'required_scopes': ['es:read']" in src
    # Sole process on BACKEND_PORT, no child supervisor.
    assert f"port={codegen.BACKEND_PORT}" in src
    assert "subprocess" not in src


def test_remote_build_context_has_proxy_no_server(tmp_path):
    codegen.write_build_context(_remote_spec(), tmp_path)
    assert (tmp_path / "proxy.py").exists()
    assert not (tmp_path / "server.py").exists()
    dockerfile = (tmp_path / "Dockerfile").read_text()
    assert 'CMD ["python", "proxy.py"]' in dockerfile
    assert f"EXPOSE {codegen.BACKEND_PORT}" in dockerfile


# ---- Code-first proxy still has supervisor + loopback, now with optional auth ----

def test_code_proxy_keeps_supervisor_and_loopback():
    spec = ServerSpec(name="c", mode="code", source="print('x')", tokens=TOKENS)
    src = codegen.generate_proxy_py(spec, format_output=False)
    assert _compiles(src)
    assert f"127.0.0.1:{codegen.BACKEND_PORT}" in src
    assert "subprocess.Popen" in src
    assert f"port={codegen.PROXY_PORT}" in src
    # Tokens present -> auth attached so code-first can enforce scopes too.
    assert "StaticTokenVerifier" in src


def test_code_proxy_without_tokens_has_no_auth():
    spec = ServerSpec(name="c", mode="code", source="print('x')")
    src = codegen.generate_proxy_py(spec, format_output=False)
    assert _compiles(src)
    assert "StaticTokenVerifier" not in src
    assert "_MW_AUTH_ENABLED = False" in src


# ---- Multi-stage Dockerfile targeting a non-root distroless (DHI) runtime ----

def test_dockerfile_is_multi_stage_build_then_runtime():
    spec = ServerSpec(name="s", primitives=[{"kind": "tool", "name": "t", "code": "return 'ok'"}])
    df = codegen.generate_dockerfile(
        spec, build_image="dhi.io/python:3.14-debian13-dev",
        runtime_image="dhi.io/python:3.14-debian13",
    )
    # Two stages: build compiles deps into a venv, runtime receives it.
    assert "FROM dhi.io/python:3.14-debian13-dev AS build" in df
    assert "FROM dhi.io/python:3.14-debian13 AS runtime" in df
    assert "RUN python -m venv --copies /opt/venv" in df
    assert "COPY --from=build /opt/venv /opt/venv" in df
    assert f"RUN pip install --no-cache-dir fastmcp=={codegen.FASTMCP_VERSION}" in df
    # No 3.12-slim anywhere (TRM-unauthorized).
    assert "python:3.12-slim" not in df


def test_dockerfile_healthcheck_is_exec_form_and_entrypoint_reset():
    spec = ServerSpec(name="s", primitives=[{"kind": "tool", "name": "t", "code": "return 'ok'"}])
    df = codegen.generate_dockerfile(spec)
    # Exec-form healthcheck (JSON array) so it needs no /bin/sh in distroless.
    assert 'CMD ["python", "-c"' in df
    assert "|| exit 1" not in df
    # Entrypoint reset so CMD runs `python server.py` deterministically.
    assert "ENTRYPOINT []" in df
    assert 'CMD ["python", "server.py"]' in df


def test_dockerfile_defaults_come_from_settings():
    spec = ServerSpec(name="s", primitives=[{"kind": "tool", "name": "t", "code": "return 'ok'"}])
    df = codegen.generate_dockerfile(spec)
    # Defaults target the TRM-authorized DHI 3.14 Debian 13 line.
    assert "FROM dhi.io/python:3.14-debian13-dev AS build" in df
    assert "FROM dhi.io/python:3.14-debian13 AS runtime" in df


def test_dockerfile_custom_ca_carried_into_runtime():
    spec = ServerSpec(name="s", primitives=[{"kind": "tool", "name": "t", "code": "return 'ok'"}])
    df = codegen.generate_dockerfile(spec, custom_ca="-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----")
    # Trust bundle built in the build stage (has update-ca-certificates)...
    assert "update-ca-certificates" in df
    # ...then carried into the distroless runtime, which can't run it.
    assert (
        "COPY --from=build /etc/ssl/certs/ca-certificates.crt "
        "/etc/ssl/certs/ca-certificates.crt" in df
    )
    assert "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" in df


def test_dockerfile_apt_packages_install_in_build_stage_only():
    spec = ServerSpec(
        name="s",
        primitives=[{"kind": "tool", "name": "t", "code": "return 'ok'"}],
        apt_packages=["libxml2"],
    )
    df = codegen.generate_dockerfile(spec)
    build_stage, runtime_stage = df.split("FROM ", 2)[1], df.split("FROM ", 2)[2]
    assert "apt-get install" in build_stage
    assert "apt-get" not in runtime_stage
