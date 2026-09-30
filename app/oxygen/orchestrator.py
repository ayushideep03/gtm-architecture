"""
Oxygen Orchestrator.

Coordinates context gathering, decision engine evaluation, capability validation,
task handoff, and decision event emission. Does not perform external actions.
"""

import json
from typing import Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.base import Event, Task
from app.oxygen.context import OxygenContextBuilder
from app.oxygen.decision import DeterministicDecisionEngine
from app.oxygen.interfaces import DecisionEngine
from app.oxygen.models import Decision, DecisionOutcome, OxygenContext
from app.oxygen.registry import CapabilityRegistry, capability_registry

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
    ) -> None:
        self.context_builder = context_builder or OxygenContextBuilder()
        self.decision_engine = decision_engine or DeterministicDecisionEngine()
        self.registry = registry or capability_registry

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
        return self.decision_engine.decide(context)

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
        decision = self.decision_engine.decide(context)

        # 3. Validate capability
        if decision.capability:
            if not self.registry.is_available(decision.capability):
                logger.warning(
                    "Oxygen decision requested unavailable capability: %s",
                    decision.capability,
                )
                decision.action = "wait"
                decision.reason = f"Required capability '{decision.capability}' is currently unavailable"

        # 4. Idempotency & Task Creation
        task: Optional[Task] = None
        task_created = False

        if decision.action not in ("wait", "no_action"):
            # Check existing pending/in-progress tasks for this lead with the same task_type
            already_scheduled = context.has_pending_task(decision.action)
            if not already_scheduled:
                task = Task(
                    lead_id=lead_id,
                    task_type=decision.action,
                    status="pending",
                    payload=json.dumps(decision.parameters, default=str),
                )
                db.add(task)
                await db.flush()
                task_created = True
                logger.info(
                    "Oxygen created Task [%s] type=%r for lead=%s",
                    task.id,
                    task.task_type,
                    lead_id,
                )
            else:
                # Idempotent wait: task already exists
                logger.info(
                    "Oxygen detected existing pending task of type %r for lead=%s; skipping creation",
                    decision.action,
                    lead_id,
                )
                decision.action = "wait"
                decision.reason = f"A pending task of type '{decision.action}' already exists"

        # 5. Emit oxygen_decision event (append-only)
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
        }

        event = Event(
            lead_id=lead_id,
            event_type="oxygen_decision",
            source="oxygen",
            payload=json.dumps(event_payload, default=str),
        )
        db.add(event)
        await db.flush()

        # Determine overall outcome status
        status = "success"
        if decision.action == "wait":
            status = "waiting"
        elif decision.action == "no_action":
            status = "no_action"

        return DecisionOutcome(
            lead_id=lead_id,
            decision=decision,
            task_id=task.id if task else None,
            task_created=task_created,
            event_id=event.id,
            status=status,
        )
