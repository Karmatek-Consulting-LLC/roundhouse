from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import db_session, init_db

FRONTEND_DIR = Path("/app/public/frontend")

logger = logging.getLogger("roundhouse-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.services.event_retention import retention_loop

    init_db()
    # Migrate any legacy filesystem server state (the per-node `server-data`
    # volume) into Postgres. Idempotent + best-effort: never block startup.
    try:
        from app.services.spec_import import import_on_startup

        import_on_startup()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("Filesystem spec import failed")
    _seed_admin_if_needed()
    _check_docker_reachable()
    # Prune expired request_events on a schedule. Runs in every worker but is
    # single-flighted via a Postgres advisory lock (see event_retention).
    retention_task = asyncio.create_task(retention_loop())
    try:
        yield
    finally:
        retention_task.cancel()


def _check_docker_reachable() -> None:
    """Fail fast at startup if the docker backend can't reach its daemon, so a
    misconfigured MCP_DOCKER_HOST crashes loudly here instead of surfacing as a
    500 on every server-backed page. Skipped for the kubernetes orchestrator."""
    cfg = get_settings()
    if (cfg.mcp_orchestrator or "docker").strip().lower() != "docker":
        return
    from app.services.docker_http import DockerHttp

    try:
        DockerHttp(cfg.docker_host).get("info")
    except Exception as e:  # noqa: BLE001 - surface any connection failure clearly
        raise RuntimeError(
            f"Cannot reach Docker at {cfg.docker_host!r} (MCP_DOCKER_HOST/"
            f"MCP_DOCKER_SOCKET): {e}. Check the endpoint is correct and the "
            "socket-proxy is reachable on the stack network."
        ) from e
    logger.info("Docker backend reachable at %s", cfg.docker_host)


def _seed_admin_if_needed() -> None:
    """If the users table is empty, seed an initial superadmin from env.

    This runs in every uvicorn worker at startup (the image launches with
    `--workers 2`), so on a fresh DB two workers would otherwise both observe an
    empty table and both INSERT the same admin email — the loser hitting the
    `users.email` unique constraint and crashing startup. We single-flight the
    check-and-insert behind a blocking advisory lock so exactly one worker seeds
    while the rest wait and then see the row. The IntegrityError catch is a
    backstop for backends without advisory locks (sqlite/tests) and any other
    concurrent insert path.
    """
    from sqlalchemy.exc import IntegrityError

    from app.auth import hash_password
    from app.db import SEED_LOCK_KEY, advisory_lock
    from app.models import User

    cfg = get_settings()
    try:
        with advisory_lock(SEED_LOCK_KEY), db_session() as db:
            if db.query(User).first() is not None:
                return
            admin = User(
                email=cfg.admin_email,
                password_hash=hash_password(cfg.admin_password),
                display_name="Admin",
                role="superadmin",
            )
            db.add(admin)
            logger.info("Seeded initial admin user %s", cfg.admin_email)
    except IntegrityError:
        logger.info("Admin user already seeded by another worker; skipping.")


app = FastAPI(title="MCP Platform API", lifespan=lifespan)


# Error envelope: `{"detail": "..."}` for HTTP errors, with `errors` added on
# 422 validation failures. The frontend expects this shape.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail if isinstance(exc.detail, str) else "Request failed"},
        headers=exc.headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    # Best-effort short message in `detail`, plus the full `errors` array.
    first = errors[0].get("msg") if errors else "Validation failed"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": first or "Validation failed", "errors": errors},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Routers
from app.routes import audit as audit_route  # noqa: E402
from app.routes import auth as auth_route  # noqa: E402
from app.routes import backup as backup_route  # noqa: E402
from app.routes import dashboard as dashboard_route  # noqa: E402
from app.routes import ingest as ingest_route  # noqa: E402
from app.routes import invoke as invoke_route  # noqa: E402
from app.routes import observability as observability_route  # noqa: E402
from app.routes import oidc as oidc_route  # noqa: E402
from app.routes import pypi as pypi_route  # noqa: E402
from app.routes import role_mappings as role_mappings_route  # noqa: E402
from app.routes import servers as servers_route  # noqa: E402
from app.routes import server_scopes as server_scopes_route  # noqa: E402
from app.routes import server_tokens as server_tokens_route  # noqa: E402
from app.routes import settings as settings_route  # noqa: E402
from app.routes import teams as teams_route  # noqa: E402
from app.routes import templates as templates_route  # noqa: E402
from app.routes import users as users_route  # noqa: E402

app.include_router(audit_route.router)
app.include_router(auth_route.router)
app.include_router(oidc_route.router)
app.include_router(role_mappings_route.router)
app.include_router(dashboard_route.router)
app.include_router(observability_route.router)
app.include_router(ingest_route.router)
app.include_router(users_route.router)
app.include_router(teams_route.router)
app.include_router(templates_route.router)
app.include_router(servers_route.router)
app.include_router(server_scopes_route.router)
app.include_router(server_tokens_route.router)
app.include_router(invoke_route.router)
app.include_router(pypi_route.router)
app.include_router(settings_route.router)
app.include_router(backup_route.router)


class SPAStaticFiles(StaticFiles):
    """Serve the React SPA: static assets when they exist, index.html for
    every other path so client-side routing (React Router) resolves."""

    async def get_response(self, path: str, scope):
        # Routers handle /api/* first; this guard is a safety net so a stray
        # /api request that fell through still returns a JSON 404, not HTML.
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


# Must be mounted AFTER all routers so /api/* wins. The dir existence check
# keeps `uvicorn app.main:app` runnable in dev without a frontend build.
if FRONTEND_DIR.is_dir():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIR, html=True), name="spa")
else:
    logger.warning("Frontend dir %s not found; SPA will not be served", FRONTEND_DIR)
