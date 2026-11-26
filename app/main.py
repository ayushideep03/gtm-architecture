"""
GTM Autonomous System — FastAPI application entry point.

This module:
- Creates and configures the FastAPI application
- Registers all middleware (logging, CORS)
- Registers all API routers
- Handles startup / shutdown lifecycle events

Router mounting:
    /health, /health/db, /health/redis  — root-level (no version prefix)
    /api/v1/leads/*                     — versioned API
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, logging_middleware
from app.core.redis import close_redis_pool, get_redis_pool

settings = get_settings()

# ── Logging must be configured before anything else logs ─────────────────────
configure_logging(
    level=settings.LOG_LEVEL,
    development=settings.is_development,
)

logger = get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Application startup / shutdown lifecycle."""
    logger.info(
        "Starting %s v%s [%s]",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )

    # Warm up Redis pool
    get_redis_pool()
    logger.info("Redis pool initialised")

    yield  # <- application runs here

    # Graceful shutdown
    await close_redis_pool()
    logger.info("GTM system shutting down")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Autonomous AI Sales/GTM system. "
            "Lead ingestion layer: source providers, normalization, persistence."
        ),
        version=settings.APP_VERSION,
        # Interactive API docs only outside production (they map every endpoint for an attacker).
        docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
        redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
        openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.middleware("http")(logging_middleware)

    # The dashboard is served from this same origin, so CORS is only for a separate dev server
    # (Vite on :5173) or origins listed in CORS_ORIGINS. Never "*" together with credentials.
    import os
    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if not origins and settings.is_development:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    # Health endpoints at root (no version prefix) — /health, /health/db, etc.
    app.include_router(health_router)

    # Versioned API — /api/v1/leads/ingest, etc.
    app.include_router(api_router)

    # ── Dashboard (built React app) ───────────────────────────────────────────
    # Served from frontend/dist when it has been built (npm run build, or start.py).
    from pathlib import Path
    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")

    return app


app = create_app()

