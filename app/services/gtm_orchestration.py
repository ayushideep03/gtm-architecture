"""End-to-End Autonomous GTM Control Loop Service.

Orchestrates the complete deterministic cycle across:
Ingestion/CRM -> Identity/Enrichment -> Oxygen -> Guardrails -> Task -> Execution -> Events -> RevOps -> Next Decision.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.execution.executor import TaskExecutor
from app.execution.models import ExecutionOutcome
from app.models.base import Lead
from app.oxygen.models import Decision, DecisionOutcome
from app.oxygen.orchestrator import OxygenOrchestrator
from app.revops.models import EventProcessingResult
from app.revops.processor import EventProcessor

logger = get_logger(__name__)


@dataclass
class GtmCycleResult:
    """Summary of a single deterministic autonomous GTM cycle."""

    lead_id: uuid.UUID
    cycle_started_at: datetime
    cycle_completed_at: datetime
    initial_decision: Optional[Decision] = None
    initial_outcome: Optional[DecisionOutcome] = None
    execution_outcome: Optional[ExecutionOutcome] = None
    revops_result: Optional[EventProcessingResult] = None
    subsequent_decision: Optional[Decision] = None
    status: str = "success"
    error: Optional[str] = None


class GtmOrchestrationService:
    """Coordinates a single deterministic control loop iteration for a Lead."""

    def __init__(
        self,
        oxygen_orchestrator: Optional[OxygenOrchestrator] = None,
        task_executor: Optional[TaskExecutor] = None,
        event_processor: Optional[EventProcessor] = None,
    ) -> None:
        self.orchestrator = oxygen_orchestrator or OxygenOrchestrator()
        self.executor = task_executor or TaskExecutor()
        self.processor = event_processor or EventProcessor()

    async def execute_cycle(
        self,
        db: AsyncSession,
        lead_id: uuid.UUID,
    ) -> GtmCycleResult:
        """Run a single deterministic GTM control loop step for a given lead.

        Flow:
            1. Validate lead exists.
            2. Run Oxygen orchestration (context -> decision -> guardrails -> task creation -> event).
            3. If task created, execute through TaskExecutor (row lock -> provider dispatch -> completion -> event).
            4. Process newly emitted events with durable RevOps processor (updates processing cursor).
            5. Re-evaluate Oxygen to observe updated state and compute next decision.
            6. Return structured GtmCycleResult.
        """
        start_time = datetime.now(timezone.utc)
        logger.info("Starting Autonomous GTM cycle for lead [%s]", lead_id)

        # 1. Lead check
        lead = await db.get(Lead, lead_id)
        if not lead:
            end_time = datetime.now(timezone.utc)
            return GtmCycleResult(
                lead_id=lead_id,
                cycle_started_at=start_time,
                cycle_completed_at=end_time,
                status="error",
                error=f"Lead {lead_id} not found",
            )

        # 2. Oxygen Orchestration
        outcome = await self.orchestrator.orchestrate(db, lead_id)
        await db.commit()

        exec_outcome: Optional[ExecutionOutcome] = None

        # 3. Execution (if task was created)
        if outcome.task_created and outcome.task_id:
            logger.info("GTM cycle executing task [%s] for lead [%s]", outcome.task_id, lead_id)
            exec_outcome = await self.executor.execute(outcome.task_id, db)

        # 4. RevOps Durable Event Processing
        revops_result = await self.processor.process_unprocessed_events(
            db, consumer_name=f"gtm_cycle_{lead_id}"
        )

        # 5. Subsequent Oxygen Re-evaluation (feedback loop verification)
        subsequent_decision = await self.orchestrator.decide_only(db, lead_id)

        end_time = datetime.now(timezone.utc)
        logger.info(
            "Finished Autonomous GTM cycle for lead [%s] in %.2fs. Next decision: %r",
            lead_id,
            (end_time - start_time).total_seconds(),
            subsequent_decision.action if subsequent_decision else None,
        )

        return GtmCycleResult(
            lead_id=lead_id,
            cycle_started_at=start_time,
            cycle_completed_at=end_time,
            initial_decision=outcome.decision,
            initial_outcome=outcome,
            execution_outcome=exec_outcome,
            revops_result=revops_result,
            subsequent_decision=subsequent_decision,
            status="success" if outcome.status != "error" else "error",
            error=outcome.error,
        )


gtm_orchestration_service = GtmOrchestrationService()
