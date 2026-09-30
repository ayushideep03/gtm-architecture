"""
Deterministic Decision Engine for Oxygen.

Applies explicit, testable, and deterministic policy rules to an OxygenContext.
Contains NO LLM calls, NO arbitrary scoring logic, and NO invented probabilities.
"""

from typing import Optional

from app.oxygen.interfaces import DecisionEngine
from app.oxygen.models import Decision, DecisionReason, OxygenContext


class DeterministicDecisionEngine(DecisionEngine):
    """
    Deterministic rule-based decision engine.

    Policy:
        1. If lead lacks both company and person data -> NO_ACTION (missing data)
        2. If company exists and is unenriched:
            - If task exists -> WAIT
            - Else -> ENRICH_COMPANY
        3. If person exists and is unenriched:
            - If task exists -> WAIT
            - Else -> ENRICH_PERSON
        4. If enrichment is satisfied:
            - If qualification task exists/pending -> WAIT
            - If qualification already completed -> NO_ACTION
            - Else -> QUALIFY_LEAD (execution task handoff)
        5. Fallback -> NO_ACTION
    """

    def decide(self, context: OxygenContext) -> Decision:
        event_ids = [e.id for e in context.events[:5]]

        # Rule 1: Missing core entities
        if not context.person and not context.company:
            return Decision(
                action="no_action",
                reason=DecisionReason.MISSING_DATA.value,
                target_id=context.lead_id,
                target_type="lead",
                confidence=1.0,
                source_event_ids=event_ids,
            )

        # Rule 2: Company enrichment needed
        if context.has_company and not context.is_company_enriched:
            if context.has_pending_task("enrich_company"):
                return Decision(
                    action="wait",
                    reason=DecisionReason.TASK_ALREADY_EXISTS.value,
                    target_id=context.company.id,
                    target_type="company",
                    capability="company_enrichment",
                    confidence=1.0,
                    source_event_ids=event_ids,
                )
            return Decision(
                action="enrich_company",
                reason=DecisionReason.COMPANY_ENRICHMENT_NEEDED.value,
                target_id=context.company.id,
                target_type="company",
                capability="company_enrichment",
                parameters={
                    "company_id": str(context.company.id),
                    "domain": context.company.domain,
                },
                confidence=1.0,
                source_event_ids=event_ids,
            )

        # Rule 3: Person enrichment needed
        if context.has_person and not context.is_person_enriched:
            if context.has_pending_task("enrich_person"):
                return Decision(
                    action="wait",
                    reason=DecisionReason.TASK_ALREADY_EXISTS.value,
                    target_id=context.person.id,
                    target_type="person",
                    capability="person_enrichment",
                    confidence=1.0,
                    source_event_ids=event_ids,
                )
            return Decision(
                action="enrich_person",
                reason=DecisionReason.PERSON_ENRICHMENT_NEEDED.value,
                target_id=context.person.id,
                target_type="person",
                capability="person_enrichment",
                parameters={
                    "person_id": str(context.person.id),
                    "email": context.person.email,
                },
                confidence=1.0,
                source_event_ids=event_ids,
            )

        # Rule 4: Enrichment complete -> execution task handoff (qualify_lead)
        if context.has_pending_task("qualify_lead"):
            return Decision(
                action="wait",
                reason=DecisionReason.TASK_ALREADY_EXISTS.value,
                target_id=context.lead_id,
                target_type="lead",
                capability="lead_qualification",
                confidence=1.0,
                source_event_ids=event_ids,
            )

        if context.has_completed_task("qualify_lead"):
            return Decision(
                action="no_action",
                reason=DecisionReason.EXECUTION_COMPLETED.value,
                target_id=context.lead_id,
                target_type="lead",
                confidence=1.0,
                source_event_ids=event_ids,
            )

        return Decision(
            action="qualify_lead",
            reason=DecisionReason.READY_FOR_EXECUTION.value,
            target_id=context.lead_id,
            target_type="lead",
            capability="lead_qualification",
            parameters={"lead_id": str(context.lead_id)},
            confidence=1.0,
            source_event_ids=event_ids,
        )
