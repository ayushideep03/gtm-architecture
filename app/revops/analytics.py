"""Analytics and timeline query functions for RevOps."""

import json
from typing import Any, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Event, Interaction, Lead, Task
from app.revops.models import LeadTimeline, RevOpsMetrics, TimelineItem


async def compute_revops_metrics(session: AsyncSession) -> RevOpsMetrics:
    """Compute aggregated operational GTM metrics from persistent state."""
    # Count event types
    event_counts_stmt = select(Event.event_type, func.count(Event.id)).group_by(Event.event_type)
    event_counts_res = await session.execute(event_counts_stmt)
    ev_map = dict(event_counts_res.all())

    # Count tasks by status
    task_counts_stmt = select(Task.status, func.count(Task.id)).group_by(Task.status)
    task_counts_res = await session.execute(task_counts_stmt)
    task_map = dict(task_counts_res.all())

    # Interactions count
    outreach_stmt = select(func.count(Interaction.id)).where(Interaction.interaction_type == "email_sent")
    outreach_count = (await session.execute(outreach_stmt)).scalar() or 0

    meeting_stmt = select(func.count(Interaction.id)).where(Interaction.interaction_type == "meeting")
    meeting_count = (await session.execute(meeting_stmt)).scalar() or 0

    # Lead qualification outcomes
    lead_status_stmt = select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    lead_status_res = await session.execute(lead_status_stmt)
    lead_status_map = dict(lead_status_res.all())

    tasks_completed = task_map.get("completed", 0) + task_map.get("done", 0)
    tasks_failed = task_map.get("failed", 0)
    total_finished = tasks_completed + tasks_failed
    success_rate = round(tasks_completed / total_finished, 4) if total_finished > 0 else 0.0

    return RevOpsMetrics(
        leads_ingested=ev_map.get("lead_ingested", 0),
        companies_enriched=ev_map.get("company_enriched", 0),
        people_enriched=ev_map.get("person_enriched", 0),
        enrichment_failures=ev_map.get("enrichment_failed", 0),
        oxygen_decisions=ev_map.get("oxygen_decision", 0),
        guardrail_blocks=ev_map.get("guardrail_blocked", 0),
        tasks_created=sum(task_map.values()),
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
        execution_success_rate=success_rate,
        simulated_outreach_count=outreach_count,
        simulated_meeting_count=meeting_count,
        qualification_outcomes={str(k): int(v) for k, v in lead_status_map.items()},
    )


async def build_lead_timeline(session: AsyncSession, lead_id: uuid.UUID) -> Optional[LeadTimeline]:
    """Assemble chronological timeline combining Events and Interactions for a lead."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        return None

    # Load events
    ev_stmt = select(Event).where(Event.lead_id == lead_id).order_by(Event.occurred_at.asc())
    events = (await session.execute(ev_stmt)).scalars().all()

    # Load interactions
    inter_stmt = select(Interaction).where(Interaction.lead_id == lead_id).order_by(Interaction.occurred_at.asc())
    interactions = (await session.execute(inter_stmt)).scalars().all()

    items: list[TimelineItem] = []

    for ev in events:
        parsed_payload = {}
        if ev.payload:
            try:
                parsed_payload = json.loads(ev.payload)
            except Exception:
                parsed_payload = {"raw": ev.payload}

        items.append(
            TimelineItem(
                id=ev.id,
                category="event",
                item_type=ev.event_type,
                occurred_at=ev.occurred_at,
                title=f"Event: {ev.event_type.replace('_', ' ').title()}",
                details=parsed_payload,
            )
        )

    for inter in interactions:
        items.append(
            TimelineItem(
                id=inter.id,
                category="interaction",
                item_type=inter.interaction_type,
                occurred_at=inter.occurred_at,
                title=f"Interaction: {inter.subject or inter.interaction_type}",
                details={
                    "interaction_type": inter.interaction_type,
                    "direction": inter.direction,
                    "subject": inter.subject,
                    "body": inter.body,
                    "channel_message_id": inter.channel_message_id,
                },
            )
        )

    # Sort all items chronologically
    items.sort(key=lambda x: x.occurred_at)

    return LeadTimeline(
        lead_id=lead.id,
        lead_status=lead.status,
        lead_score=lead.score,
        timeline=items,
    )
