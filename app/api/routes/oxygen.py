"""
API routes for Oxygen Orchestration and Decision Layer.

Endpoints:
    POST /api/v1/oxygen/leads/{lead_id}/decide      — pure decision evaluation (read-only)
    POST /api/v1/oxygen/leads/{lead_id}/orchestrate — evaluate and execute task creation
    GET  /api/v1/oxygen/capabilities               — list registered Oxygen capabilities
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.oxygen import (
    CapabilityListResponse,
    CapabilityResponse,
    DecisionResponse,
    OrchestrateResponse,
)
from app.core.database import get_db
from app.oxygen.orchestrator import OxygenOrchestrator
from app.oxygen.registry import capability_registry

router = APIRouter(prefix="/oxygen", tags=["oxygen"])
_orchestrator = OxygenOrchestrator()


@router.post(
    "/leads/{lead_id}/decide",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate decision for a lead without side effects",
    description="Builds context and runs the decision engine in read-only mode (no tasks or events).",
)
async def decide_for_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DecisionResponse:
    decision = await _orchestrator.decide_only(db, lead_id)
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )
    return DecisionResponse(
        action=decision.action,
        reason=decision.reason,
        target_id=decision.target_id,
        target_type=decision.target_type,
        confidence=decision.confidence,
        capability=decision.capability,
        parameters=decision.parameters,
        source_event_ids=decision.source_event_ids,
    )


@router.post(
    "/leads/{lead_id}/orchestrate",
    response_model=OrchestrateResponse,
    status_code=status.HTTP_200_OK,
    summary="Orchestrate next action for a lead",
    description="Evaluates context, decides next action, creates tasks idempotently, and emits events.",
)
async def orchestrate_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OrchestrateResponse:
    outcome = await _orchestrator.orchestrate(db, lead_id)
    if outcome.status == "error":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=outcome.error or f"Lead {lead_id} not found",
        )

    await db.commit()

    return OrchestrateResponse(
        lead_id=outcome.lead_id,
        decision=DecisionResponse(
            action=outcome.decision.action,
            reason=outcome.decision.reason,
            target_id=outcome.decision.target_id,
            target_type=outcome.decision.target_type,
            confidence=outcome.decision.confidence,
            capability=outcome.decision.capability,
            parameters=outcome.decision.parameters,
            source_event_ids=outcome.decision.source_event_ids,
        ),
        task_id=outcome.task_id,
        task_created=outcome.task_created,
        event_id=outcome.event_id,
        status=outcome.status,
        error=outcome.error,
    )


@router.get(
    "/capabilities",
    response_model=CapabilityListResponse,
    status_code=status.HTTP_200_OK,
    summary="List available Oxygen capabilities",
)
async def list_capabilities() -> CapabilityListResponse:
    caps = capability_registry.list_capabilities()
    return CapabilityListResponse(
        capabilities=[
            CapabilityResponse(
                name=c.name,
                description=c.description,
                input_contract=c.input_contract,
                is_available=c.is_available,
            )
            for c in caps
        ]
    )
