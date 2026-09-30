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
