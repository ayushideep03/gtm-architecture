"""Guardrail Evaluator coordinating context extraction and policy verification."""

from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.guardrails.models import GuardrailContext, GuardrailResult
from app.guardrails.registry import GuardrailRegistry, guardrail_registry
from app.oxygen.models import Decision, OxygenContext

logger = get_logger(__name__)


class GuardrailEvaluator:
    """Evaluates proposed decisions against registered policy guardrails."""

    def __init__(self, registry: Optional[GuardrailRegistry] = None) -> None:
        self.registry = registry or guardrail_registry

    async def evaluate(
        self,
        context: GuardrailContext,
        session: Optional[AsyncSession] = None,
    ) -> GuardrailResult:
        """Evaluate a raw GuardrailContext directly."""
        return await self.registry.evaluate_all(context, session=session)

    async def evaluate_decision(
        self,
        decision: Decision,
        oxygen_context: OxygenContext,
        session: Optional[AsyncSession] = None,
    ) -> GuardrailResult:
        """Translate Oxygen Decision + OxygenContext into GuardrailContext and evaluate."""
        task_history = [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "status": t.status,
            }
            for t in oxygen_context.tasks
        ]

        recent_events = [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "occurred_at": str(e.occurred_at),
            }
            for e in oxygen_context.events
        ]

        guardrail_ctx = GuardrailContext(
            lead_id=oxygen_context.lead_id,
            person_id=oxygen_context.person.id if oxygen_context.person else None,
            company_id=oxygen_context.company.id if oxygen_context.company else None,
            action=decision.action,
            capability=decision.capability,
            task_type=decision.action if decision.action not in ("wait", "no_action") else None,
            target_id=decision.target_id,
            target_type=decision.target_type,
            parameters=decision.parameters,
            recent_events=recent_events,
            task_history=task_history,
            has_company=oxygen_context.has_company,
            has_person=oxygen_context.has_person,
            is_company_enriched=oxygen_context.is_company_enriched,
            is_person_enriched=oxygen_context.is_person_enriched,
        )

        return await self.registry.evaluate_all(guardrail_ctx, session=session)
