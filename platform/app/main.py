import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from app.database import SessionLocal
from app.routers.auth import router as auth_router
from app.routers.pypi import router as pypi_router
from app.routers.servers import router as servers_router
from app.routers.teams import router as teams_router
from app.routers.settings import router as settings_router
from app.routers.users import router as users_router
from app.seed import seed_admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migrations() -> None:
    from sqlalchemy import inspect
    from app.database import engine

    alembic_cfg = Config("/app/alembic.ini")
    alembic_cfg.set_main_option("script_location", "/app/alembic")

    # If tables exist but alembic_version doesn't, stamp to current
    # (handles migration from create_all to alembic)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "users" in tables and "alembic_version" not in tables:
        logger.info("Existing tables found without alembic_version, stamping to head")
        command.stamp(alembic_cfg, "head")
    else:
        command.upgrade(alembic_cfg, "head")

    logger.info("Database migrations complete")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    with SessionLocal() as db:
        seed_admin(db)
    yield


app = FastAPI(title="MCP Platform", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(servers_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(teams_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(pypi_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve frontend static files in production
# (only mounts if the dist directory exists in the image)
from pathlib import Path

_static_dir = Path("/app/static")
if _static_dir.exists() and (_static_dir / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    # Serve built assets (JS, CSS, fonts)
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="static-assets")

    # SPA catch-all: serve index.html for any non-API, non-asset route
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file = _static_dir / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_static_dir / "index.html")
