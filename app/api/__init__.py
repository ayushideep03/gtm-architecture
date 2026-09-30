"""
API routes package.

Register all routers here so main.py only needs to import this module.
"""

from fastapi import APIRouter

from app.api.routes.enrichment import router as enrichment_router
from app.api.routes.execution import router as execution_router
from app.api.routes.health import router as health_router
from app.api.routes.leads import router as leads_router
from app.api.routes.oxygen import router as oxygen_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(leads_router)
api_router.include_router(enrichment_router)
api_router.include_router(oxygen_router)
api_router.include_router(execution_router)

__all__ = ["api_router"]
