"""
Event type registry.

``events.event_type`` is a validated string (not a database enum), so new
capabilities and integrations can introduce event types without a migration.
Validation still rejects typos: a type must be registered here or by a module
that calls ``register_event_type`` at start-up.
"""

from __future__ import annotations

import re
import threading

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

CORE_EVENT_TYPES: tuple[str, ...] = (
    "lead_created",
    "lead_enriched",
    "lead_ingested",
    "lead_qualified",
    "lead_disqualified",
    "email_sent",
    "reply_received",
    "meeting_booked",
    "task_created",
    "task_started",
    "task_completed",
    "task_failed",
    "company_enriched",
    "person_enriched",
    "enrichment_completed",
    "enrichment_failed",
    "oxygen_decision",
    "oxygen_task_created",
    "oxygen_waiting",
    "oxygen_no_action",
    "guardrail_allowed",
    "guardrail_blocked",
    "prospect_researched",
    "outreach_drafted",
    "outreach_simulated",
    "meeting_booking_simulated",
    "crm_updated",
    # Phase 2: identity & reliability
    "lead_resourced",          # a known person/lead seen again by a source
    "task_retry_scheduled",    # transient failure; task will run again later
    "task_retry_requested",    # explicit recovery of a failed task
    "task_recovered",          # stale in_progress task returned to the queue
    "task_needs_review",       # outcome of an external action is unknown; human must reconcile
    # Memory & compliance
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "approval_expired",
    "suppression_added",
    "suppression_lifted",
    "compliance_blocked",
    "finding_recorded",
    "fact_reviewed",
    "work_scheduled",
    # Inbound engagement & meetings
    "email_delivered",
    "email_opened",
    "email_clicked",
    "email_bounced",
    "unsubscribed",
    "complaint_received",
    "reply_classified",
    "meeting_cancelled",
    "meeting_rescheduled",
    "inbound_unmatched",
    # Autonomy
    "autonomy_paused",
    "autonomy_resumed",
)

_lock = threading.Lock()
_registered: set[str] = set(CORE_EVENT_TYPES)


def register_event_type(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"invalid event type name {name!r} (lowercase snake_case, max 64 chars)")
    with _lock:
        _registered.add(name)
    return name


def is_registered(name: str) -> bool:
    return name in _registered


def registered_event_types() -> list[str]:
    return sorted(_registered)


def validate_event_type(name: str) -> str:
    if name not in _registered:
        raise ValueError(f"unregistered event type {name!r}; register it with register_event_type()")
    return name
