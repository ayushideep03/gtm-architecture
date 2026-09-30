"""RevOps service orchestrating metrics, event processing, and timelines."""

from typing import Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.revops.analytics import build_lead_timeline, compute_revops_metrics
from app.revops.models import EventProcessingResult, LeadTimeline, RevOpsMetrics
from app.revops.processor import EventProcessor


class RevOpsService:
    """Service facade for revenue operations analytics and event processing."""

    def __init__(self, processor: Optional[EventProcessor] = None) -> None:
        self.processor = processor or EventProcessor()

    async def get_metrics(self, session: AsyncSession) -> RevOpsMetrics:
        """Compute system-wide GTM performance and operational metrics."""
        return await compute_revops_metrics(session)

    async def get_lead_timeline(
        self, session: AsyncSession, lead_id: uuid.UUID
    ) -> Optional[LeadTimeline]:
        """Assemble chronological touchpoint history for a lead."""
        return await build_lead_timeline(session, lead_id)

    async def process_events(
        self,
        session: AsyncSession,
        consumer_name: str = "revops_core",
        limit: int = 100,
    ) -> EventProcessingResult:
        """Advance the durable event cursor and execute feedback processing."""
        return await self.processor.process_unprocessed_events(
            session=session, consumer_name=consumer_name, limit=limit
        )


revops_service = RevOpsService()
