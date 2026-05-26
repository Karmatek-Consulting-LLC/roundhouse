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
    SETTING_EXTERNAL_HTTPS,
    SETTING_HOSTNAME,
    get_setting,
)
from app.services import codegen, global_env, server_auth
from app.services.docker import DockerClient
from app.services.spec import ServerSpec
from app.services.store import ServerStore
from app.services.template_engine import TemplateEngine

logger = logging.getLogger(__name__)


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
        merged: dict[str, str] = {}
        gdict = global_env.globals_as_dict(db)
        for name in spec.env_global_imports:
            if name in gdict:
                merged[name] = gdict[name]
        for ev in spec.env_vars:
            merged[ev.name] = ev.value
        return merged

    def custom_ca_cert(self, db: Session) -> str | None:
        raw = (get_setting(db, SETTING_CUSTOM_CA_CERT, "") or "").strip()
        return raw or None

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
        hostname = (get_setting(db, SETTING_HOSTNAME, "") or "").strip()
        if not hostname:
            return get_settings().mcp_base_url
        scheme = "https" if get_setting(db, SETTING_EXTERNAL_HTTPS, "") == "true" else "http"
        return f"{scheme}://{hostname}"

    # ---- Deploy orchestration ----

    def save_spec(self, db: Session, spec: ServerSpec) -> None:
        """Persist a spec change to disk + flag the server as needing a
        redeploy. Does NOT touch Docker - users batch edits, then redeploy."""
        spec.tokens = server_auth.tokens_for_codegen(db, spec.name)
        codegen.write_build_context(
            spec, self.store.server_dir(spec.name), self.custom_ca_cert(db)
        )
        self.store.save(spec)
        server_auth.mark_redeploy_required(db, spec.name)

    def build_and_deploy(self, db: Session, spec: ServerSpec) -> dict:
        spec.tokens = server_auth.tokens_for_codegen(db, spec.name)
        build_context = codegen.write_build_context(
            spec, self.store.server_dir(spec.name), self.custom_ca_cert(db)
        )
        self.store.save(spec)
        result = self.docker.build_and_start(
            server_name=spec.name,
            build_context=build_context,
            template_name="custom",
            env_vars=self.effective_env(db, spec),
            replicas=self.effective_replicas(spec),
            registry_prefix=self.registry_prefix(db),
            registry_auth=self.registry_auth(db),
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
        except Exception as e:  # noqa: BLE001 - log + continue, same as Laravel
            logger.error("Failed to update runtime env for server '%s': %s", server_name, e)


# ---- Factory ----

_singleton: ServerService | None = None


def get_server_service() -> ServerService:
    global _singleton
    if _singleton is None:
        from app.config import servers_dir, templates_dir
        from app.services.docker import get_docker

        store = ServerStore(servers_dir())
        templates = TemplateEngine(templates_dir(), servers_dir())
        _singleton = ServerService(get_docker(), store, templates)
    return _singleton
