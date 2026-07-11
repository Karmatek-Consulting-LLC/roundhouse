"""Orchestrates spec persistence + codegen + Docker deploy for MCP servers."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ServerOwner
from app.platform_settings import (
    SETTING_CUSTOM_CA_CERT,
    SETTING_DOCKER_REGISTRY,
    SETTING_DOCKER_REGISTRY_PASSWORD,
    SETTING_DOCKER_REGISTRY_USERNAME,
    get_setting,
)
from app.services import codegen, global_env, server_auth
from app.services.docker import CONTAINER_PREFIX, DockerClient
from app.services.spec import ServerSpec
from app.services.store import ServerStore
from app.services.template_engine import TemplateEngine

logger = logging.getLogger(__name__)


def _resolve_secret_env(name: str, value: str, app_key: str) -> str | None:
    """Resolve a secret env var's stored value to the plaintext to inject into
    the container. Returns None when it cannot be resolved (caller drops it).

    Three cases, only the last of which loses data:
      - `value` is not an encryption envelope: it's a plaintext value stored
        under the no-APP_KEY dev fallback (`_encrypt_env`). Use it as-is.
      - `value` is an envelope and APP_KEY decrypts it: return the plaintext.
      - `value` is an envelope but decrypt fails (APP_KEY changed since the row
        was saved, or no key is configured): we cannot recover the plaintext.
        Log loudly and return None so the loss is diagnosable instead of the
        silent drop that made env vars mysteriously vanish on redeploy.
    """
    from app.crypto import DecryptError, decrypt, looks_encrypted

    if not looks_encrypted(value):
        # Stored plaintext (APP_KEY was unset when this secret was saved).
        return value
    if not app_key:
        logger.warning(
            "Secret env %r is encrypted but APP_KEY is not configured; it will "
            "be missing from the container until APP_KEY is set.",
            name,
        )
        return None
    try:
        return decrypt(value, app_key)
    except DecryptError as exc:
        logger.warning(
            "Secret env %r failed to decrypt (%s) - APP_KEY likely changed since "
            "it was saved. Re-enter the value to restore it.",
            name,
            exc,
        )
        return None


class ServerService:
    def __init__(
        self,
        docker: DockerClient,
        store: ServerStore,
        templates: TemplateEngine,
    ):
        self.docker = docker
        self.store = store
        self.templates = templates

    # ---- Config helpers ----

    def effective_replicas(self, spec: ServerSpec | None) -> int:
        default = get_settings().mcp_default_server_replicas
        if spec is None or spec.replicas is None:
            return default
        return spec.replicas

    def effective_env(self, db: Session, spec: ServerSpec) -> dict[str, str]:
        from app.config import get_settings
        merged: dict[str, str] = {}
        gdict = global_env.globals_as_dict(db)
        for name in spec.env_global_imports:
            if name in gdict:
                merged[name] = gdict[name]
            else:
                logger.warning(
                    "Server %r imports global env %r but it is not defined in "
                    "platform settings; it will be missing from the container.",
                    spec.name,
                    name,
                )
        app_key = get_settings().app_key
        for ev in spec.env_vars:
            if not ev.secret:
                merged[ev.name] = ev.value
                continue
            if not ev.value:
                # Secret row declared but no value collected yet.
                merged[ev.name] = ""
                continue
            resolved = _resolve_secret_env(ev.name, ev.value, app_key)
            if resolved is not None:
                merged[ev.name] = resolved
        return merged

    def custom_ca_cert(self, db: Session) -> str | None:
        raw = (get_setting(db, SETTING_CUSTOM_CA_CERT, "") or "").strip()
        return raw or None

    def resolve_remote_headers(self, db: Session, spec: ServerSpec) -> dict[str, str]:
        """Map a remote server's outbound header definitions to their decrypted
        values. The header name (e.g. "Authorization") pairs with the env var
        that holds its secret value; effective_env does the decryption. Headers
        whose env var is unset are dropped (discovery will then 401 visibly)."""
        if not spec.is_remote_mode() or not spec.remote_headers:
            return {}
        env = self.effective_env(db, spec)
        out: dict[str, str] = {}
        for h in spec.remote_headers:
            header = (h.get("header") or "").strip()
            env_name = (h.get("env") or "").strip()
            if header and env_name and env.get(env_name):
                out[header] = env[env_name]
        return out

    def discover_primitives(self, db: Session, spec: ServerSpec) -> list[dict]:
        """Introspect a proxied server (code-first or remote) and return its
        primitives reconciled into the existing overlay. Caller persists."""
        import ssl

        from app.services import discovery
        from app.services.mcp_client import McpError, get_mcp_client, verify_for_ca

        headers = self.resolve_remote_headers(db, spec)
        verify = None
        if spec.is_remote_mode():
            # A configured outbound header whose secret didn't resolve would go
            # to the upstream as a missing/empty credential and come back as a
            # bare 401 - indistinguishable from a wrong key. Fail early with an
            # actionable message so the operator knows it's the stored secret,
            # not the upstream, that's the problem.
            unresolved = [
                h.get("header")
                for h in (spec.remote_headers or [])
                if h.get("header") and h.get("env") and not headers.get(h.get("header"))
            ]
            if unresolved:
                raise McpError(
                    f"Outbound credential(s) {', '.join(unresolved)} could not be "
                    "resolved: the backing secret env var(s) are empty or failed to "
                    "decrypt (most likely APP_KEY changed since they were saved). "
                    "Re-enter the value under Env vars, then rediscover."
                )
            # Trust the operator's custom CA for the upstream's TLS, if configured,
            # so discovery doesn't fail with "unable to get local issuer".
            try:
                verify = verify_for_ca(self.custom_ca_cert(db))
            except ssl.SSLError as e:
                raise McpError(f"Configured custom CA certificate is not valid PEM: {e}") from e
            ca_certs = len(verify.get_ca_certs()) if verify is not None else 0
            logger.info(
                "Discovery for %r: %s",
                spec.name,
                f"custom CA active ({ca_certs} cert(s) in trust store)"
                if verify is not None
                else "no custom CA configured (system roots only)",
            )
        # Code-mode servers with tokens gate their MCP endpoint; introspect
        # with a real (decrypted) token like any other internal caller.
        local_headers = None
        if not spec.is_remote_mode():
            token = server_auth.token_plaintext(db, spec.name)
            if token:
                local_headers = {"Authorization": f"Bearer {token}"}
        try:
            return discovery.discover(
                get_mcp_client(), spec,
                remote_headers=headers, local_headers=local_headers, verify=verify,
            )
        except McpError as e:
            # If TLS verification failed, say whether the custom CA was even in
            # play - that distinguishes "CA not applied" (old build / empty
            # setting) from "CA applied but the chain still doesn't validate"
            # (usually a missing intermediate - paste the FULL chain).
            msg = str(e)
            tls = any(s in msg.lower() for s in ("certificate", "ssl", "issuer", "tls"))
            if tls and spec.is_remote_mode():
                if verify is not None:
                    raise McpError(
                        f"{msg} — TLS verification used your configured custom CA "
                        f"({ca_certs} cert(s) loaded) but the upstream's chain still "
                        "didn't validate. Paste the FULL chain (root + any intermediate "
                        "CAs) in Settings → Custom CA, then rediscover."
                    ) from e
                raise McpError(
                    f"{msg} — no custom CA is configured in Settings → Custom CA, so "
                    "only public CAs are trusted. Add your CA (full chain) there."
                ) from e
            raise

    def registry_prefix(self, db: Session) -> str | None:
        raw = (get_setting(db, SETTING_DOCKER_REGISTRY, "") or "").strip()
        return raw.rstrip("/") if raw else None

    def registry_auth(self, db: Session) -> dict[str, str] | None:
        if not self.registry_prefix(db):
            return None
        username = (get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, "") or "").strip()
        password = (get_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD, "") or "").strip()
        if not username or not password:
            return None
        return {"username": username, "password": password}

    def base_url(self, db: Session) -> str:
        # Single source of truth: the public base URL is the deploy-time
        # MCP_BASE_URL (from PUBLIC_HOSTNAME), kept in lockstep with Traefik
        # routing. `db` is accepted for call-site stability but unused.
        return get_settings().mcp_base_url

    def metrics_url(self, server_name: str) -> str:
        """Internal URL the platform scrapes for a server's /metrics snapshot.

        Code-first servers expose /metrics on the platform proxy, not their own
        process. The port to hit differs by backend: Kubernetes reaches servers
        through a Service on the stable port 8000 (which remaps to the proxy's
        targetPort internally), while Docker/Swarm hit the container directly and
        so must target the actual listening port - the proxy port for code-first."""
        port = codegen.BACKEND_PORT
        if self.docker.mode() != "kubernetes":
            spec = self.store.load(server_name)
            if spec is not None:
                port = codegen.route_port_for(spec)
        return f"http://{CONTAINER_PREFIX}{server_name}:{port}/metrics"

    def healthz_url(self, server_name: str) -> str:
        """Internal URL the platform probes for readiness. Same host/port as
        metrics_url; /healthz is unauthenticated and served by the FastMCP app
        itself (or the platform proxy for code-first), so a 200 means the server
        is actually serving requests - not merely that the container/task is
        running, which is all the orchestrator can tell us (and on Swarm, all it
        exposes)."""
        port = codegen.BACKEND_PORT
        if self.docker.mode() != "kubernetes":
            spec = self.store.load(server_name)
            if spec is not None:
                port = codegen.route_port_for(spec)
        return f"http://{CONTAINER_PREFIX}{server_name}:{port}/healthz"

    # ---- Deploy orchestration ----

    def save_spec(self, db: Session, spec: ServerSpec) -> None:
        """Persist a spec change + flag the server as needing a redeploy. Does
        NOT touch Docker - users batch edits, then redeploy. The build context
        is materialized from DB state at deploy time, so there is nothing to
        stage here."""
        spec.tokens = server_auth.tokens_for_codegen(db, spec.name)
        self.store.save(spec)
        server_auth.mark_redeploy_required(db, spec.name)

    def build_and_deploy(self, db: Session, spec: ServerSpec) -> dict:
        from app.services import build_context as buildctx

        spec.tokens = server_auth.tokens_for_codegen(db, spec.name)
        self.store.save(spec)
        env = self.effective_env(db, spec)
        # Leave a deploy-time trail so a missing env var is never a mystery: name
        # every declared secret and whether it actually made it into the env that
        # the container will receive. effective_env() already logs *why* a secret
        # was dropped (decrypt failure); this records the net outcome per deploy.
        declared_secrets = [ev.name for ev in spec.env_vars if ev.secret]
        dropped = [n for n in declared_secrets if not env.get(n)]
        logger.info(
            "Deploy %r: applying %d env var(s); secrets resolved %d/%d%s",
            spec.name,
            len(env),
            len(declared_secrets) - len(dropped),
            len(declared_secrets),
            f"; NOT applied: {dropped}" if dropped else "",
        )
        with buildctx.materialize(spec, self.store, self.custom_ca_cert(db)) as ctx:
            result = self.docker.build_and_start(
                server_name=spec.name,
                build_context=ctx,
                template_name="custom",
                env_vars=env,
                replicas=self.effective_replicas(spec),
                registry_prefix=self.registry_prefix(db),
                registry_auth=self.registry_auth(db),
                cpu_limit=spec.cpu_limit,
                memory_limit_mb=spec.memory_limit_mb,
                route_port=codegen.route_port_for(spec),
                placement_constraints=spec.placement_constraints,
            )
        server_auth.clear_redeploy_required(db, spec.name)
        return result

    def redeploy(self, db: Session, spec: ServerSpec) -> dict:
        self.docker.remove_server(spec.name, self.registry_prefix(db))
        return self.build_and_deploy(db, spec)

    # ---- Runtime env push (no rebuild) ----

    def reapply_runtime_env_for_all_servers(self, db: Session) -> None:
        names = [
            n for (n,) in db.query(ServerOwner.server_name).order_by(ServerOwner.server_name).all()
        ]
        for name in names:
            self.reapply_runtime_env_for(db, name)

    def reapply_runtime_env_for(self, db: Session, server_name: str) -> None:
        spec = self.store.load(server_name) or ServerSpec(name=server_name)
        if not self.docker.get_server(server_name):
            return
        try:
            self.docker.update_runtime_env(server_name, self.effective_env(db, spec))
        except Exception as e:  # noqa: BLE001 - log + continue
            logger.error("Failed to update runtime env for server '%s': %s", server_name, e)


# ---- Factory ----

_singleton: ServerService | None = None


def get_server_service() -> ServerService:
    global _singleton
    if _singleton is None:
        from app.config import templates_dir
        from app.services.docker import get_docker

        store = ServerStore()
        templates = TemplateEngine(templates_dir())
        _singleton = ServerService(get_docker(), store, templates)
    return _singleton
