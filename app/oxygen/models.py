"""
Typed domain models for Oxygen Orchestration & Decision Layer.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import uuid

from app.models.base import Company, Event, Interaction, Lead, Person, Task


class DecisionReason(str, Enum):
    """Normalized reasons for decisions taken by the engine."""
    COMPANY_ENRICHMENT_NEEDED = "company_enrichment_needed"
    PERSON_ENRICHMENT_NEEDED = "person_enrichment_needed"
    READY_FOR_EXECUTION = "ready_for_execution"
    TASK_ALREADY_EXISTS = "task_already_exists"
    MISSING_DATA = "missing_data"
    EXECUTION_COMPLETED = "execution_completed"
    NO_ACTION_REQUIRED = "no_action_required"


@dataclass
class Decision:
    """
    The output of a decision engine evaluation.

    Decisions define what action should be taken, for what reason,
    using which capability and parameters, but DO NOT execute the action.
    """
    action: str  # "enrich_company", "enrich_person", "qualify_lead", "wait", "no_action"
    reason: str
    target_id: Optional[uuid.UUID] = None
    target_type: Optional[str] = None  # "company", "person", "lead"
    confidence: Optional[float] = 1.0
    capability: Optional[str] = None  # "company_enrichment", "person_enrichment", "lead_qualification"
    parameters: dict[str, Any] = field(default_factory=dict)
    source_event_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class ActionRequest:
    """A structured request to create an execution Task for a worker."""
    task_type: str
    lead_id: uuid.UUID
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 5


@dataclass
class DecisionOutcome:
    """
    The full result of orchestrating a decision for a lead.
    Tracks whether a Task was created and what Event was emitted.
    """
    lead_id: uuid.UUID
    decision: Decision
    task_id: Optional[uuid.UUID] = None
    task_created: bool = False
    event_id: Optional[uuid.UUID] = None
    status: str = "success"  # "success", "waiting", "no_action", "error"
    error: Optional[str] = None


@dataclass
class OxygenContext:
    """
    A normalized view of the current GTM state for a single lead.
    Read-only context gathered from CompAI CRM and append-only events.
    """
    lead_id: uuid.UUID
    lead: Optional[Lead] = None
    person: Optional[Person] = None
    company: Optional[Company] = None
    events: list[Event] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    is_company_enriched: bool = False
    is_person_enriched: bool = False

    @property
    def has_company(self) -> bool:
        return self.company is not None and bool(self.company.domain)

    @property
    def has_person(self) -> bool:
        return self.person is not None and bool(self.person.email)

    @property
    def pending_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.status in ("pending", "in_progress")]

    def has_pending_task(self, task_type: str) -> bool:
        return any(t.task_type == task_type for t in self.pending_tasks)

    def has_completed_task(self, task_type: str) -> bool:
        return any(t.task_type == task_type and t.status == "done" for t in self.tasks)

    def latest_event(self, event_type: str) -> Optional[Event]:
        for e in self.events:
            if e.event_type == event_type:
                return e
        return None
