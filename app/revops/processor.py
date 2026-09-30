"""Durable event stream processor for RevOps and feedback loop coordination."""

from datetime import datetime, timezone
import json
from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.base import Event, EventProcessingState, Lead
from app.revops.models import EventProcessingResult

logger = get_logger(__name__)


class EventProcessor:
    """Processes append-only Events durably using PostgreSQL state cursors."""

    async def get_or_create_state(
        self,
        session: AsyncSession,
        consumer_name: str,
    ) -> EventProcessingState:
        """Fetch or initialize the durable cursor for a consumer."""
        state = await session.get(EventProcessingState, consumer_name)
        if state is None:
            state = EventProcessingState(
                consumer_name=consumer_name,
                processed_count=0,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(state)
            await session.flush()
        return state

    async def process_unprocessed_events(
        self,
        session: AsyncSession,
        consumer_name: str = "revops_core",
        limit: int = 100,
    ) -> EventProcessingResult:
        """Fetch and process unconsumed events in chronological order without mutating history.

        Args:
            session: Active database session.
            consumer_name: Unique consumer cursor identifier.
            limit: Maximum events to consume in one batch.

        Returns:
            EventProcessingResult summarizing events processed and feedback triggered.
        """
        state = await self.get_or_create_state(session, consumer_name)

        # Build query for events after the last processed timestamp
        query = select(Event).order_by(Event.occurred_at.asc(), Event.id.asc()).limit(limit)
        if state.last_processed_timestamp:
            query = query.where(Event.occurred_at >= state.last_processed_timestamp)

        events = (await session.execute(query)).scalars().all()

        actions_triggered: list[str] = []
        processed_count = 0
        last_event_id: Optional[uuid.UUID] = state.last_processed_event_id
        last_timestamp = state.last_processed_timestamp

        for ev in events:
            # Skip if this is the exact event already processed at the boundary timestamp
            if state.last_processed_event_id and ev.id == state.last_processed_event_id:
                continue

            # Deterministic processing rules based on event_type
            action_desc = self._process_single_event(ev)
            if action_desc:
                actions_triggered.append(action_desc)

            last_event_id = ev.id
            last_timestamp = ev.occurred_at
            processed_count += 1

        # Update cursor state
        if processed_count > 0:
            state.last_processed_event_id = last_event_id
            state.last_processed_timestamp = last_timestamp
            state.processed_count += processed_count
            state.updated_at = datetime.now(timezone.utc)
            await session.commit()

        logger.info(
            "EventProcessor [%s] processed %d events. Total processed: %d",
            consumer_name,
            processed_count,
            state.processed_count,
        )

        return EventProcessingResult(
            consumer_name=consumer_name,
            events_processed=processed_count,
            last_event_id=last_event_id,
            actions_triggered=actions_triggered,
        )

    def _process_single_event(self, event: Event) -> Optional[str]:
        """Evaluate deterministic feedback reaction for an individual domain event."""
        et = event.event_type

        if et in ("lead_ingested", "company_enriched", "person_enriched", "enrichment_completed"):
            return f"OxygenReconsiderationReady: Lead {event.lead_id} received {et}"

        elif et == "guardrail_blocked":
            return f"RevOpsAuditAlert: Guardrail blocked action for Lead {event.lead_id}"

        elif et == "task_started":
            return f"ExecutionMonitoring: Task started for Lead {event.lead_id}"

        elif et == "task_completed":
            return f"CRMFeedbackApplied: Task completed for Lead {event.lead_id}"

        elif et == "task_failed":
            return f"ExecutionFailureLogged: Task failed for Lead {event.lead_id}"

        elif et == "oxygen_decision":
            return f"OrchestrationTracked: Oxygen decision logged for Lead {event.lead_id}"

        return None
