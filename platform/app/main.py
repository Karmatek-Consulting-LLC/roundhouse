import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import SessionLocal, engine
from app.db_models import Base
from app.routers.auth import router as auth_router
from app.routers.pypi import router as pypi_router
from app.routers.servers import router as servers_router
from app.routers.teams import router as teams_router
from app.routers.users import router as users_router
from app.seed import seed_admin

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_admin(db)
    yield


app = FastAPI(title="MCP Platform", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(servers_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(teams_router, prefix="/api")
app.include_router(pypi_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
