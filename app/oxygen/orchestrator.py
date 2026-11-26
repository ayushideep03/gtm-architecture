"""
Oxygen Orchestrator.

Coordinates context gathering, decision engine evaluation, capability validation,
task handoff, and decision event emission. Does not perform external actions.
"""

import inspect
import json
from typing import Any, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.base import Event, Task
from app.oxygen.context import OxygenContextBuilder
from app.oxygen.decision import DeterministicDecisionEngine  # noqa: F401  (re-export)
from app.oxygen.interfaces import DecisionEngine
from app.oxygen.models import NON_ACTIONS, Decision, DecisionOutcome, OxygenContext
from app.oxygen.registry import CapabilityRegistry, CatalogueCapabilityView, capability_registry

logger = get_logger(__name__)


class OxygenOrchestrator:
    """
    Core orchestrator for Oxygen.

    Stateless: takes a database session and coordinates context gathering,
    decision making, task creation, and event emission.
    """

    def __init__(
        self,
        context_builder: Optional[OxygenContextBuilder] = None,
        decision_engine: Optional[DecisionEngine] = None,
        registry: Optional[CapabilityRegistry] = None,
        guardrail_evaluator: Optional[Any] = None,
    ) -> None:
        self.context_builder = context_builder or OxygenContextBuilder()
        self.decision_engine = decision_engine or build_decision_engine()
        self.registry = registry or capability_registry
        if guardrail_evaluator is None:
            from app.guardrails.evaluator import GuardrailEvaluator
            self.guardrail_evaluator = GuardrailEvaluator()
        else:
            self.guardrail_evaluator = guardrail_evaluator

    async def decide_only(
        self,
        db: AsyncSession,
        lead_id: uuid.UUID,
    ) -> Optional[Decision]:
        """
        Pure read-only decision evaluation.
        Does NOT create tasks, does NOT emit events, does NOT mutate DB.

        Args:
            db: Active async session.
            lead_id: Target lead UUID.

        Returns:
            Decision instance or None if lead does not exist.
        """
        context = await self.context_builder.build(db, lead_id)
        if context is None:
            return None
        return await self._decide(context)

    async def _decide(self, context) -> Decision:
        """Engines may be sync (rules) or async (LLM Sales Controller)."""
        result = self.decision_engine.decide(context)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def orchestrate(
        self,
        db: AsyncSession,
        lead_id: uuid.UUID,
    ) -> DecisionOutcome:
        """
        Full orchestration pipeline for a lead.

        Flow:
            1. Build context from CRM & Events.
            2. Evaluate DecisionEngine.
            3. Validate capability availability.
            4. Idempotently create Task if execution is needed.
            5. Emit oxygen_decision Event.
            6. Return DecisionOutcome.

        Args:
            db: Active async session (caller or route manages commit).
            lead_id: Target lead UUID.

        Returns:
            DecisionOutcome describing the decision, created task, and event.
        """
        # 1. Build context
        context = await self.context_builder.build(db, lead_id)
        if context is None:
            return DecisionOutcome(
                lead_id=lead_id,
                decision=Decision(
                    action="no_action",
                    reason="Lead not found in database",
                    target_id=lead_id,
                    target_type="lead",
                ),
                status="error",
                error=f"Lead {lead_id} not found",
            )

        # 2. Decide
        decision = await self._decide(context)

        tenant_id = getattr(context.lead, "tenant_id", None)

        # 2b. Email proposals carry the exact draft content, so the human approves the real text
        if decision.action not in NON_ACTIONS:
            await self._attach_draft(db, context, decision)

        # 3. Validate capability (catalogue + live provider availability for this tenant)
        if decision.capability:
            if isinstance(self.registry, CatalogueCapabilityView):
                available = await self.registry.is_available(decision.capability, tenant_id)
            else:
                available = self.registry.is_available(decision.capability)
            if not available:
                logger.warning(
                    "Oxygen decision requested unavailable capability: %s",
                    decision.capability,
                )
                decision.action = "wait"
                decision.reason = f"Required capability '{decision.capability}' is currently unavailable"

        # 4. Guardrail Evaluation & Policy Enforcement
        task: Optional[Task] = None
        task_created = False
        guardrail_result = None

        if decision.action not in NON_ACTIONS:
            guardrail_result = await self.guardrail_evaluator.evaluate_decision(
                decision, context, session=db
            )

            if not guardrail_result.allowed:
                logger.warning(
                    "Guardrail BLOCKED Oxygen action %r for lead=%s: %s",
                    decision.action,
                    lead_id,
                    guardrail_result.reason,
                )
                blocked_event = Event(
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    event_type="guardrail_blocked",
                    source="guardrails",
                    payload=json.dumps({
                        "lead_id": str(lead_id),
                        "decision": decision.action,
                        "capability": decision.capability,
                        "task_type": decision.action,
                        "allowed": False,
                        "rule_ids": guardrail_result.rule_ids,
                        "reason": guardrail_result.reason,
                    }),
                )
                db.add(blocked_event)
                await db.flush()
                record = await self._persist_decision(db, context, decision, "blocked",
                                                      guardrail_result.model_dump(), None, False)

                return DecisionOutcome(
                    lead_id=lead_id,
                    decision=decision,
                    task_id=None,
                    task_created=False,
                    event_id=blocked_event.id,
                    status="blocked",
                    error=guardrail_result.reason,
                    decision_record_id=record.id,
                )

            # Record guardrail_allowed event
            allowed_event = Event(
                lead_id=lead_id,
                tenant_id=tenant_id,
                event_type="guardrail_allowed",
                source="guardrails",
                payload=json.dumps({
                    "lead_id": str(lead_id),
                    "decision": decision.action,
                    "capability": decision.capability,
                    "task_type": decision.action,
                    "allowed": True,
                    "rule_ids": guardrail_result.rule_ids,
                    "reason": guardrail_result.reason,
                }),
            )
            db.add(allowed_event)
            await db.flush()

            # 5. Idempotent Task Creation
            from sqlalchemy.exc import IntegrityError
            from app.capabilities import get_capability_catalogue
            catalogue = get_capability_catalogue()
            task_type = catalogue.canonical(decision.action) or decision.action
            spec = catalogue.get(task_type, tenant_id)
            idem_key = None
            if spec is not None:
                validated = spec.validate_parameters(
                    decision.parameters,
                    {"lead_id": lead_id,
                     "company_id": context.company.id if context.company else None,
                     "person_id": context.person.id if context.person else None},
                )
                idem_key = spec.idempotency_key(lead_id, validated.parameters)

            if context.has_pending_task(task_type):
                logger.info("Oxygen: pending %r task already exists for lead=%s", task_type, lead_id)
                decision.action = "wait"
                decision.reason = f"A pending task of type '{task_type}' already exists"
            else:
                candidate = Task(
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    task_type=task_type,
                    status="pending",
                    payload=json.dumps(decision.parameters, default=str),
                    idempotency_key=idem_key,
                )
                try:
                    # Savepoint: the (tenant_id, idempotency_key) unique index is the
                    # last line of defence against recording the same action twice.
                    async with db.begin_nested():
                        db.add(candidate)
                        await db.flush()
                    task, task_created = candidate, True
                    logger.info("Oxygen created Task [%s] type=%r for lead=%s", task.id, task.task_type, lead_id)
                except IntegrityError:
                    logger.warning("Oxygen: action %r already recorded (idempotency key %s)", task_type, idem_key)
                    decision.action = "wait"
                    decision.reason = f"Action already recorded under idempotency key {idem_key}"

        # 6. Persist the decision and route external actions to human approval
        guardrail_snapshot = guardrail_result.model_dump() if guardrail_result is not None else None
        requires_approval = False
        if task_created and task is not None:
            from app.capabilities import get_capability_catalogue as _cat
            requires_approval = _cat().requires_approval(task.task_type, tenant_id)
        record_status = ("awaiting_approval" if requires_approval else "queued") if task_created else {
            "wait": "waiting", "stop": "stopped", "escalate": "escalated"}.get(decision.action, "no_action")
        record = await self._persist_decision(db, context, decision, record_status, guardrail_snapshot,
                                              task if task_created else None, requires_approval)
        await self._schedule_follow_up(db, context, decision, record)
        approval = None
        if requires_approval:
            from app.compliance.approvals import request_approval
            approval = await request_approval(db, task, reasoning=decision.reason,
                                              evidence=[str(e) for e in decision.source_event_ids],
                                              decision_id=record.id)

        # 7. Emit oxygen_decision event (append-only)
        event_payload = {
            "lead_id": str(lead_id),
            "action": decision.action,
            "target_id": str(decision.target_id) if decision.target_id else None,
            "target_type": decision.target_type,
            "reason": decision.reason,
            "capability": decision.capability,
            "task_id": str(task.id) if task else None,
            "task_created": task_created,
            "parameters": decision.parameters,
            "source_event_ids": [str(eid) for eid in decision.source_event_ids],
            "decision_record_id": str(record.id),
            "requires_approval": requires_approval,
            "approval_id": str(approval.id) if approval else None,
            "engine": decision.engine,
            "model": decision.model,
            "confidence": decision.confidence,
            "fallback_reason": decision.fallback_reason,
        }

        event = Event(
            lead_id=lead_id,
            tenant_id=tenant_id,
            event_type="oxygen_decision",
            source="oxygen",
            payload=json.dumps(event_payload, default=str),
        )
        db.add(event)
        await db.flush()

        # Determine overall outcome status
        status = "success"
        if requires_approval:
            status = "awaiting_approval"
        else:
            status = {"wait": "waiting", "no_action": "no_action", "stop": "stopped",
                      "escalate": "escalated"}.get(decision.action, status)

        return DecisionOutcome(
            lead_id=lead_id,
            decision=decision,
            task_id=task.id if task else None,
            task_created=task_created,
            event_id=event.id,
            status=status,
            decision_record_id=record.id,
            approval_id=approval.id if approval else None,
            requires_approval=requires_approval,
        )

    async def _attach_draft(self, db, context, decision: Decision) -> None:
        """send_email with draft_task_id: copy subject/body from that lead's completed draft task."""
        draft_id = (decision.parameters or {}).get("draft_task_id")
        if (decision.capability or decision.action) not in ("send_email",) or not draft_id:
            return
        try:
            draft = await db.get(Task, uuid.UUID(str(draft_id)))
        except ValueError:
            draft = None
        if draft is None or draft.lead_id != context.lead_id or draft.status not in ("completed", "done") \
                or draft.task_type != "draft_outreach":
            decision.action, decision.capability = "wait", None
            decision.reason = f"Referenced draft {draft_id} is not a completed draft for this lead"
            return
        content = json.loads(draft.result or "{}")
        decision.parameters.setdefault("subject", content.get("subject"))
        decision.parameters.setdefault("body", content.get("body"))

    async def _schedule_follow_up(self, db, context, decision: Decision, record) -> None:
        """wait → schedule re-evaluation; escalate → human review item; stop → close open work."""
        from datetime import datetime, timedelta, timezone
        from app.memory.records import complete_work, open_work_for_lead, schedule_work
        tenant = getattr(context.lead, "tenant_id", None)
        now = datetime.now(timezone.utc)
        if decision.action == "wait" and decision.wait_hours:
            await schedule_work(db, tenant_id=tenant, lead_id=context.lead_id, kind="evaluate",
                                reason=decision.reasoning or decision.reason, created_by="oxygen",
                                due_at=now + timedelta(hours=decision.wait_hours),
                                payload={"decision_id": str(record.id)})
        elif decision.action == "escalate":
            await schedule_work(db, tenant_id=tenant, lead_id=context.lead_id, kind="human_review",
                                reason=decision.reasoning or decision.reason, created_by="oxygen", priority=8,
                                payload={"decision_id": str(record.id)})
        elif decision.action == "stop":
            for item in await open_work_for_lead(db, context.lead_id):
                await complete_work(db, item, "stopped_by_oxygen")

    async def _persist_decision(self, db, context, decision: Decision, status: str,
                                guardrail_result: Optional[dict], task: Optional[Task], requires_approval: bool):
        """Every proposal is kept in agent_decisions (decision history used by memory and the dashboard)."""
        from app.memory.records import record_decision
        engine = decision.engine or getattr(self.decision_engine, "engine_name", "rules")
        return await record_decision(
            db,
            tenant_id=getattr(context.lead, "tenant_id", None),
            lead_id=context.lead_id,
            engine=engine,
            model=getattr(decision, "model", None),
            prompt_version=getattr(decision, "prompt_version", None),
            action=decision.action,
            capability=decision.capability,
            channel=getattr(decision, "channel", None),
            parameters=json.loads(json.dumps(decision.parameters or {}, default=str)),
            reasoning=getattr(decision, "reasoning", None) or decision.reason,
            evidence_refs=getattr(decision, "evidence_refs", None) or [str(e) for e in decision.source_event_ids],
            expected_outcome=getattr(decision, "expected_outcome", None),
            confidence=decision.confidence,
            uncertainty=getattr(decision, "uncertainty", None),
            risks=getattr(decision, "risks", None),
            requires_approval=requires_approval,
            guardrail_result=json.loads(json.dumps(guardrail_result, default=str)) if guardrail_result else None,
            status=status,
            task_id=task.id if task else None,
            context_snapshot=json.loads(json.dumps({
                "situation_summary": decision.situation_summary,
                "missing_information": decision.missing_information,
                "interest_signals": decision.interest_signals,
                "worth_pursuing": decision.worth_pursuing,
                "alternatives": decision.alternatives,
                "model_requested_approval": decision.model_requested_approval,
                "wait_hours": decision.wait_hours,
                "fallback_reason": decision.fallback_reason,
                "llm": decision.llm_meta,
            }, default=str)),
        )


def build_decision_engine() -> DecisionEngine:
    """Engine from config: 'llm' (Sales Controller with rules fallback) or 'rules'."""
    from app.core.gtm_config import get_gtm_config
    if get_gtm_config().oxygen.decision_engine == "llm":
        from app.oxygen.llm_engine import LLMDecisionEngine
        return LLMDecisionEngine()
    return DeterministicDecisionEngine()
