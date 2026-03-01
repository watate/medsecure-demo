import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models.schemas import HealthResponse
from app.routers import alerts, config, remediation, replay, reports, repos, scans
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
    description="CodeQL remediation comparison platform — Devin vs Copilot Autofix vs Anthropic vs OpenAI vs Google",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow dashboard frontend
origins = settings.cors_origins.split(",") if settings.cors_origins != "*" else ["*"]
# Auto-add the deployed domain so CORS works without manual CORS_ORIGINS config
if settings.domain and settings.domain != "localhost":
    for scheme in ("https://", "http://"):
        domain_origin = f"{scheme}{settings.domain}"
        if domain_origin not in origins:
            origins.append(domain_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(repos.router)
app.include_router(scans.router)
app.include_router(alerts.router)
app.include_router(remediation.router)
app.include_router(reports.router)
app.include_router(replay.router)
app.include_router(config.router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        repo="(select a repo in the UI)",
        database=settings.database_path,
    )
