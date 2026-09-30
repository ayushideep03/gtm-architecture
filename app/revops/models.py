"""Domain models and schemas for RevOps, metrics, and event processing."""

from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class RevOpsMetrics(BaseModel):
    """Aggregated operational metrics for the GTM autonomous system."""

    model_config = ConfigDict(from_attributes=True)

    leads_ingested: int = 0
    companies_enriched: int = 0
    people_enriched: int = 0
    enrichment_failures: int = 0
    oxygen_decisions: int = 0
    guardrail_blocks: int = 0
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    execution_success_rate: float = 0.0
    simulated_outreach_count: int = 0
    simulated_meeting_count: int = 0
    qualification_outcomes: dict[str, int] = Field(default_factory=dict)


class TimelineItem(BaseModel):
    """A chronological touchpoint or event in a lead's journey."""

    id: uuid.UUID
    category: str  # "event" or "interaction"
    item_type: str
    occurred_at: datetime
    title: str
    details: dict[str, Any] = Field(default_factory=dict)


class LeadTimeline(BaseModel):
    """Complete chronological representation of events and interactions for a lead."""

    lead_id: uuid.UUID
    lead_status: str
    lead_score: Optional[int] = None
    timeline: list[TimelineItem] = Field(default_factory=list)


class EventProcessingResult(BaseModel):
    """Result of processing a batch of append-only events."""

    consumer_name: str
    events_processed: int
    last_event_id: Optional[uuid.UUID] = None
    actions_triggered: list[str] = Field(default_factory=list)
