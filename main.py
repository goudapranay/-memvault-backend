"""
main.py  –  MemVault FastAPI application entry point
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models.database import init_db, settings
from routers.auth import router as auth_router
from routers.memories import router as memories_router
from routers.sharing import router as sharing_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s – %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MemVault API…")
    await init_db()
    logger.info("Database initialised")
    yield
    logger.info("Shutting down MemVault API")


app = FastAPI(
    title="MemVault API",
    description="Backend for MemVault – compressed family memory storage with Google Drive",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = [o.strip() for o in settings.allowed_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(memories_router)
app.include_router(sharing_router)


@app.get("/", tags=["health"])
async def root():
    return {
        "service": "MemVault API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
