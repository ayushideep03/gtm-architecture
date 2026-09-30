"""
Health-check API routes.

Endpoints:
    GET /health          — API liveness
    GET /health/db       — PostgreSQL connectivity
    GET /health/redis    — Redis connectivity
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.database import check_db_health
from app.core.redis import check_redis_health
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="API liveness")
async def health() -> dict:
    """Returns 200 if the API process is running."""
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/db", summary="PostgreSQL connectivity")
async def health_db() -> JSONResponse:
    """Executes SELECT 1 against PostgreSQL and reports the result."""
    result = await check_db_health()
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(content=result, status_code=status_code)


@router.get("/health/redis", summary="Redis connectivity")
async def health_redis() -> JSONResponse:
    """Pings Redis and reports the result."""
    result = await check_redis_health()
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(content=result, status_code=status_code)
