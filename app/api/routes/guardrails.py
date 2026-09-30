"""API routes for Guardrails & Policy Enforcement."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.guardrails import (
    GuardrailEvaluateRequest,
    GuardrailEvaluateResponse,
    GuardrailRuleListResponse,
    GuardrailRuleResponse,
)
from app.core.database import get_db
from app.guardrails.evaluator import GuardrailEvaluator
from app.guardrails.models import GuardrailContext
from app.guardrails.registry import guardrail_registry

router = APIRouter(prefix="/guardrails", tags=["guardrails"])
_evaluator = GuardrailEvaluator(guardrail_registry)


@router.get(
    "/rules",
    response_model=GuardrailRuleListResponse,
    status_code=status.HTTP_200_OK,
    summary="List registered guardrail rules",
    description="Returns all registered policy rules and their enforcement severities.",
)
async def list_guardrail_rules() -> GuardrailRuleListResponse:
    """List all registered guardrail rules."""
    rules = guardrail_registry.list_rules()
    return GuardrailRuleListResponse(
        count=len(rules),
        rules=[
            GuardrailRuleResponse(
                rule_id=r.rule_id,
                description=r.description,
                severity=r.severity.value,
                enabled=r.enabled,
            )
            for r in rules
        ],
    )


@router.post(
    "/evaluate",
    response_model=GuardrailEvaluateResponse,
    status_code=status.HTTP_200_OK,
    summary="Diagnostically evaluate guardrail policies",
    description="Evaluates a proposed action context against all active guardrail rules.",
)
async def evaluate_guardrails(
    payload: GuardrailEvaluateRequest,
    db: AsyncSession = Depends(get_db),
) -> GuardrailEvaluateResponse:
    """Diagnostically evaluate context against guardrail rules."""
    ctx = GuardrailContext(
        lead_id=payload.lead_id,
        person_id=payload.person_id,
        company_id=payload.company_id,
        action=payload.action,
        capability=payload.capability,
        task_type=payload.task_type or payload.action,
        target_id=payload.target_id,
        target_type=payload.target_type,
        parameters=payload.parameters,
        has_company=payload.has_company,
        has_person=payload.has_person,
        is_company_enriched=payload.is_company_enriched,
        is_person_enriched=payload.is_person_enriched,
    )
    result = await _evaluator.evaluate(ctx, session=db)
    return GuardrailEvaluateResponse(
        allowed=result.allowed,
        decision=result.decision,
        reason=result.reason,
        rule_ids=result.rule_ids,
        warnings=result.warnings,
        metadata=result.metadata,
    )
