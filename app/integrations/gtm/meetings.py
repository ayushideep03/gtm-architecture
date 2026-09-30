"""Deterministic mock provider for meeting booking simulation."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Interaction, Lead


class MockMeetingBookingProvider(ExecutionProvider):
    """Deterministic simulation provider for calendar and meeting booking."""

    @property
    def provider_name(self) -> str:
        return "mock_meeting_booking"

    @property
    def supported_task_types(self) -> list[str]:
        return ["book_meeting_simulation"]

    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        if context.payload.get("force_fail"):
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="BOOKING_FAILED",
                error_message=context.payload.get("error_message", "Meeting booking simulation failed"),
            )

        meeting_topic = context.payload.get("topic", "Introductory Discovery Call")
        scheduled_for = context.payload.get(
            "scheduled_for",
            (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        )
        simulated_meeting_id = f"sim-meet-{uuid.uuid4()}"

        if session and context.lead_id:
            lead = await session.get(Lead, context.lead_id)
            if lead:
                lead.status = "meeting_booked"

            interaction = Interaction(
                lead_id=context.lead_id,
                interaction_type="meeting",
                direction="outbound",
                subject=f"Confirmed: {meeting_topic}",
                body=f"Simulated meeting scheduled for {scheduled_for}.",
                channel_message_id=simulated_meeting_id,
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(interaction)

        return ExecutionResult(
            success=True,
            status="completed",
            provider=self.provider_name,
            result={
                "booked": True,
                "mode": "simulation",
                "meeting_id": simulated_meeting_id,
                "scheduled_for": scheduled_for,
                "topic": meeting_topic,
                "provider": self.provider_name,
            },
        )
