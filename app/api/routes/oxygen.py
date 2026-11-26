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


def _orchestrator() -> OxygenOrchestrator:
    # Built per request so engine/config changes (e.g. adding an API key, config reload) apply immediately.
    return OxygenOrchestrator()


def decision_response(d) -> DecisionResponse:
    fields = DecisionResponse.model_fields
    return DecisionResponse(**{k: getattr(d, k) for k in fields if hasattr(d, k) and getattr(d, k) is not None})


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
    decision = await _orchestrator().decide_only(db, lead_id)
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )
    return decision_response(decision)


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
    outcome = await _orchestrator().orchestrate(db, lead_id)
    if outcome.status == "error":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=outcome.error or f"Lead {lead_id} not found",
        )

    await db.commit()

    return OrchestrateResponse(
        lead_id=outcome.lead_id,
        decision=decision_response(outcome.decision),
        decision_record_id=outcome.decision_record_id,
        approval_id=outcome.approval_id,
        requires_approval=outcome.requires_approval,
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
    caps = await capability_registry.list_capabilities()
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


@router.get("/engine", summary="Which decision engine and LLM profiles are active")
async def engine_status() -> dict:
    from app.core.gtm_config import get_gtm_config
    from app.llm import LLMRouter
    from app.oxygen.planner import PROMPT_VERSION
    cfg = get_gtm_config().oxygen
    llm = LLMRouter().status(cfg.llm_profile) if cfg.decision_engine == "llm" else None
    return {
        "decision_engine": cfg.decision_engine,
        "fallback_engine": cfg.fallback_engine,
        "llm_profile": cfg.llm_profile,
        "llm": llm,
        "effective": ("llm" if llm and llm["available"] else "rules_fallback") if cfg.decision_engine == "llm" else "rules",
        "prompt_version": PROMPT_VERSION,
        "campaign": cfg.campaign.model_dump(),
    }


@router.get("/leads/{lead_id}/decisions", summary="Decision history for a lead")
async def lead_decisions(lead_id: uuid.UUID, limit: int = 50, db: AsyncSession = Depends(get_db)) -> list[dict]:
    from app.memory.records import decisions_for_lead
    rows = await decisions_for_lead(db, lead_id, limit=limit)
    return [{
        "id": str(d.id), "engine": d.engine, "model": d.model, "prompt_version": d.prompt_version,
        "action": d.action, "capability": d.capability, "channel": d.channel, "parameters": d.parameters,
        "reasoning": d.reasoning, "evidence_refs": d.evidence_refs, "expected_outcome": d.expected_outcome,
        "confidence": d.confidence, "uncertainty": d.uncertainty, "risks": d.risks,
        "requires_approval": d.requires_approval, "guardrail_result": d.guardrail_result, "status": d.status,
        "task_id": str(d.task_id) if d.task_id else None, "details": d.context_snapshot,
        "created_at": d.created_at, "updated_at": d.updated_at,
    } for d in rows]


@router.post("/leads/{lead_id}/cycle", summary="Run one Oxygen cycle: decide, then execute if allowed")
async def run_cycle(lead_id: uuid.UUID) -> dict:
    """
    Decide the next action; if a task was created and needs no approval, execute it
    immediately. External actions stop at the approval queue. Returns everything the
    Control Centre shows: decision, guardrails, task, execution result, and what is next.
    """
    from app.services.autonomy import run_lead_cycle
    result = await run_lead_cycle(lead_id, orchestrator=_orchestrator())
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("error"))
    result["decision"] = decision_response(result["decision"]).model_dump(mode="json")
    return result
