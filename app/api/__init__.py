"""
API routes package.

Register all routers here so main.py only needs to import this module.
"""

from fastapi import APIRouter, Depends

from app.api.routes.admin import admin_router, integrations_router
from app.api.routes.auth import router as auth_router
from app.api.routes.pipeline import router as pipeline_router
from app.core.auth import require_principal

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
# Public: liveness, sign-in, and webhooks (which verify their own HMAC signatures).
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(webhooks_router)

# Everything else requires an authenticated principal (see app/core/auth.py).
_protected = [Depends(require_principal)]
for _r in (leads_router, enrichment_router, oxygen_router, execution_router, guardrails_router, revops_router,
           evals_router, memory_router, approvals_router, compliance_router, imports_router, autonomy_router,
           dashboard_router, demo_router, pipeline_router, admin_router, integrations_router):
    api_router.include_router(_r, dependencies=_protected)

__all__ = ["api_router"]
