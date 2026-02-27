import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models.schemas import HealthResponse
from app.routers import alerts, config, remediation, replay, reports, scans
from app.services.auth import validate_session
from app.services.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Initializing database at %s", settings.database_path)
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="MedSecure API",
    description="CodeQL remediation comparison platform — Devin vs Copilot Autofix vs Anthropic",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow dashboard frontend
origins = settings.cors_origins.split(",") if settings.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers — all protected by session validation
auth_dep = [Depends(validate_session)]
app.include_router(scans.router, dependencies=auth_dep)
app.include_router(alerts.router, dependencies=auth_dep)
app.include_router(remediation.router, dependencies=auth_dep)
app.include_router(reports.router, dependencies=auth_dep)
app.include_router(replay.router, dependencies=auth_dep)
app.include_router(config.router, dependencies=auth_dep)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        repo=settings.github_repo,
        database=settings.database_path,
    )
