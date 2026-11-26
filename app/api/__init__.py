"""
API routes package.

Register all routers here so main.py only needs to import this module.
"""

from fastapi import APIRouter

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.demo import router as demo_router
from app.api.routes.enrichment import router as enrichment_router
from app.api.routes.evals import router as evals_router
from app.api.routes.execution import router as execution_router
from app.api.routes.guardrails import router as guardrails_router
from app.api.routes.health import router as health_router
from app.api.routes.inbound import autonomy_router, imports_router, webhooks_router
from app.api.routes.leads import router as leads_router
from app.api.routes.memory import approvals_router, compliance_router, memory_router
from app.api.routes.oxygen import router as oxygen_router
from app.api.routes.revops import router as revops_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(leads_router)
api_router.include_router(enrichment_router)
api_router.include_router(oxygen_router)
api_router.include_router(execution_router)
api_router.include_router(guardrails_router)
api_router.include_router(revops_router)
api_router.include_router(evals_router)
api_router.include_router(memory_router)
api_router.include_router(approvals_router)
api_router.include_router(compliance_router)
api_router.include_router(webhooks_router)
api_router.include_router(imports_router)
api_router.include_router(autonomy_router)
api_router.include_router(dashboard_router)
api_router.include_router(demo_router)

__all__ = ["api_router"]
