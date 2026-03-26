import logging

from fastapi import FastAPI

from app.routers.servers import router as servers_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MCP Platform", version="0.1.0")
app.include_router(servers_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
