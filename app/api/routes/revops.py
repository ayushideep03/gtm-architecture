"""API routes for RevOps, metrics, event audit log, and lead timelines."""

import json
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.revops import EventListResponse, EventResponse
from app.core.database import get_db
from app.models.base import Event
from app.revops.models import LeadTimeline, RevOpsMetrics
from app.revops.services import revops_service

router = APIRouter(prefix="/revops", tags=["revops"])


@router.get(
    "/metrics",
    response_model=RevOpsMetrics,
    status_code=status.HTTP_200_OK,
    summary="Get RevOps metrics",
    description="Returns aggregate counts of ingested leads, enrichment runs, tasks, and conversion milestones.",
)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
) -> RevOpsMetrics:
    """Compute and return system operational metrics."""
    return await revops_service.get_metrics(db)


@router.get(
    "/events",
    response_model=EventListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get domain events log",
    description="Query append-only domain events with optional filters by event_type or lead_id.",
)
async def get_events(
    event_type: Optional[str] = Query(None, description="Filter by event_type"),
    lead_id: Optional[uuid.UUID] = Query(None, description="Filter by lead_id"),
    limit: int = Query(50, ge=1, le=200, description="Max events to return"),
    db: AsyncSession = Depends(get_db),
) -> EventListResponse:
    """Query domain events log."""
    query = select(Event).order_by(Event.occurred_at.desc()).limit(limit)
    if event_type:
        query = query.where(Event.event_type == event_type)
    if lead_id:
        query = query.where(Event.lead_id == lead_id)

    res = await db.execute(query)
    events = res.scalars().all()

    formatted_events: list[EventResponse] = []
    for ev in events:
        payload_data = None
        if ev.payload:
            try:
                payload_data = json.loads(ev.payload)
            except Exception:
                payload_data = {"raw": ev.payload}

        formatted_events.append(
            EventResponse(
                id=ev.id,
                lead_id=ev.lead_id,
                event_type=ev.event_type,
                source=ev.source,
                payload=payload_data,
                occurred_at=ev.occurred_at,
            )
        )

    return EventListResponse(count=len(formatted_events), events=formatted_events)


@router.get(
    "/leads/{lead_id}/timeline",
    response_model=LeadTimeline,
    status_code=status.HTTP_200_OK,
    summary="Get lead chronological timeline",
    description="Returns a merged chronological sequence of domain events and interactions for a lead.",
)
async def get_lead_timeline(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LeadTimeline:
    """Assemble and return chronological lead timeline."""
    timeline = await revops_service.get_lead_timeline(db, lead_id)
    if not timeline:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )
    return timeline


# ── RevOps dashboard, outcomes & learning (Phase F) ──────────────────────────

@router.get("/dashboard", summary="Pipeline, conversion, effectiveness, providers, costs, recommendations")
async def get_dashboard(days: int = Query(90, ge=1, le=730), session: AsyncSession = Depends(get_db)) -> dict:
    from app.revops.dashboard import revops_dashboard
    return await revops_dashboard(session, days)


@router.post("/attribute", summary="Attribute outcomes to outbound touches now")
async def run_attribution(session: AsyncSession = Depends(get_db)) -> dict:
    from app.revops.outcomes import attribute_outcomes
    written = await attribute_outcomes(session)
    await session.commit()
    return {"attributions_written": written}


@router.get("/outcomes", summary="Touch-level outcome attributions")
async def list_outcomes(limit: int = Query(100, ge=1, le=1000), session: AsyncSession = Depends(get_db)) -> list[dict]:
    from sqlalchemy import select as _select
    from app.models.memory import OutcomeAttribution
    rows = (await session.execute(_select(OutcomeAttribution).order_by(OutcomeAttribution.sent_at.desc())
                                  .limit(limit))).scalars().all()
    return [{"touch_id": str(r.touch_id), "lead_id": str(r.lead_id), "channel": r.channel, "angle": r.angle,
             "engine": r.engine, "provider": r.provider, "draft_provider": r.draft_provider,
             "personalisation_points": r.personalisation_points, "research_before_touch": r.research_before_touch,
             "segment": r.segment, "sent_at": r.sent_at, "outcome": r.outcome, "outcome_at": r.outcome_at,
             "outcome_detail": r.outcome_detail, "final": r.final} for r in rows]


@router.get("/insights/leads/{lead_id}", summary="What Oxygen has learned that applies to this prospect")
async def lead_insights(lead_id: uuid.UUID, session: AsyncSession = Depends(get_db)) -> dict:
    from app.models.base import Lead
    from app.revops.insights import planner_insights
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Lead {lead_id} not found")
    return await planner_insights(session, lead)
