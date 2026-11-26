"""
Read models for the dashboard (all from persisted data — no frontend-only counters).

    GET /api/v1/overview                 KPI tiles + engine / autonomy status
    GET /api/v1/prospects                prospect list (search, status filter) with next action
    GET /api/v1/activity                 recent activity feed (events with prospect names)
    GET /api/v1/decisions                recent Oxygen decisions across prospects
    GET /api/v1/stream                   Server-Sent Events: new events as they happen
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.models.memory import AgentDecision, Approval, WorkItem

router = APIRouter(tags=["dashboard"])


def _name(person: Optional[Person]) -> Optional[str]:
    if person is None:
        return None
    return " ".join(p for p in (person.first_name, person.last_name) if p and p != "Unknown") or person.email


def _payload(raw: Optional[str]) -> dict:
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}


@router.get("/overview", summary="Dashboard KPIs")
async def overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.services.autonomy import is_paused

    async def count(stmt) -> int:
        return (await db.execute(stmt)).scalar() or 0

    statuses = dict((await db.execute(select(Lead.status, func.count()).group_by(Lead.status))).all())
    # Drafts not yet referenced by any send task (= waiting to be proposed / approved / sent)
    drafts = set((await db.execute(select(Task.id).where(Task.task_type == "draft_outreach",
                                                         Task.status == "completed"))).scalars().all())
    used = {_payload(p).get("draft_task_id") for p in (await db.execute(
        select(Task.payload).where(Task.task_type == "send_email"))).scalars().all()}
    kpis = {
        "accounts": await count(select(func.count(Company.id))),
        "prospects": sum(statuses.values()),
        "qualified": sum(statuses.get(s, 0) for s in ("qualified", "contacted", "replied", "meeting_booked",
                                                       "closed_won")),
        "under_research": await count(select(func.count(func.distinct(Task.lead_id))).where(
            Task.task_type.in_(("research_prospect", "enrich_company", "enrich_person", "analyze_intent")),
            Task.status.in_(("pending", "in_progress")))),
        "active_workflows": await count(select(func.count(func.distinct(WorkItem.lead_id))).where(
            WorkItem.finished_at.is_(None))),
        "outreach_drafts": sum(1 for d in drafts if str(d) not in used),
        "pending_approvals": await count(select(func.count(Approval.id)).where(Approval.status == "pending")),
        "meetings_booked": statuses.get("meeting_booked", 0),
        "tasks_completed": await count(select(func.count(Task.id)).where(Task.status.in_(("completed", "done")))),
        "tasks_failed": await count(select(func.count(Task.id)).where(Task.status == "failed")),
        "needs_review": await count(select(func.count(WorkItem.id)).where(
            WorkItem.finished_at.is_(None), WorkItem.kind == "human_review")),
    }
    from app.api.routes.oxygen import engine_status
    return {"kpis": kpis, "lead_status": statuses, "engine": await engine_status(),
            "autonomy": {"paused": await is_paused()}}


@router.get("/prospects", summary="Prospect list")
async def prospects(q: Optional[str] = None, status: Optional[str] = None, limit: int = Query(200, le=1000),
                    db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = (select(Lead, Person, Company).outerjoin(Person, Person.id == Lead.person_id)
            .outerjoin(Company, Company.id == Person.company_id).order_by(Lead.updated_at.desc()).limit(limit))
    if status:
        stmt = stmt.where(Lead.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Person.first_name).like(like), func.lower(Person.last_name).like(like),
                              func.lower(Person.email).like(like), func.lower(Company.name).like(like),
                              func.lower(Company.domain).like(like)))
    rows = (await db.execute(stmt)).all()
    lead_ids = [r[0].id for r in rows]
    last_decision: dict = {}
    if lead_ids:
        sub = (select(AgentDecision.lead_id, func.max(AgentDecision.created_at).label("m"))
               .where(AgentDecision.lead_id.in_(lead_ids)).group_by(AgentDecision.lead_id).subquery())
        for d in (await db.execute(select(AgentDecision).join(sub, and_(AgentDecision.lead_id == sub.c.lead_id,
                                                                        AgentDecision.created_at == sub.c.m)))).scalars():
            last_decision[d.lead_id] = d
    pending = dict((await db.execute(select(Approval.lead_id, func.count()).where(
        Approval.status == "pending", Approval.lead_id.in_(lead_ids)).group_by(Approval.lead_id))).all()) if lead_ids else {}
    next_work = {}
    if lead_ids:
        for w in (await db.execute(select(WorkItem).where(WorkItem.lead_id.in_(lead_ids), WorkItem.finished_at.is_(None))
                                   .order_by(WorkItem.due_at))).scalars():
            next_work.setdefault(w.lead_id, w)
    out = []
    for lead, person, company in rows:
        d, w = last_decision.get(lead.id), next_work.get(lead.id)
        out.append({
            "id": str(lead.id), "status": lead.status, "score": lead.score, "source": lead.source,
            "sample": (lead.notes or "").startswith("[SAMPLE"),
            "name": _name(person), "email": person.email if person else None, "title": person.title if person else None,
            "company": company.name if company else None, "domain": company.domain if company else None,
            "industry": company.industry if company else None,
            "employees": company.employee_count if company else None,
            "last_decision": {"action": d.action, "status": d.status, "engine": d.engine, "confidence": d.confidence,
                              "at": d.created_at.isoformat()} if d else None,
            "next_work": {"kind": w.kind, "due_at": w.due_at.isoformat(), "reason": w.reason} if w else None,
            "pending_approvals": pending.get(lead.id, 0),
            "updated_at": lead.updated_at.isoformat(),
        })
    return out


async def _people_for(db: AsyncSession, lead_ids: set) -> dict:
    if not lead_ids:
        return {}
    rows = (await db.execute(select(Lead.id, Person, Company).outerjoin(Person, Person.id == Lead.person_id)
                             .outerjoin(Company, Company.id == Person.company_id)
                             .where(Lead.id.in_(lead_ids)))).all()
    return {lid: {"name": _name(p), "company": c.name if c else None} for lid, p, c in rows}


def _event_view(e: Event, people: dict) -> dict:
    who = people.get(e.lead_id, {})
    return {"id": str(e.id), "type": e.event_type, "source": e.source, "at": e.occurred_at.isoformat(),
            "lead_id": str(e.lead_id) if e.lead_id else None, "prospect": who.get("name"),
            "company": who.get("company"), "payload": _payload(e.payload)}


@router.get("/activity", summary="Recent activity")
async def activity(limit: int = Query(80, le=500), lead_id: Optional[str] = None,
                   db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Event).order_by(Event.occurred_at.desc()).limit(limit)
    if lead_id:
        stmt = stmt.where(Event.lead_id == lead_id)
    events = (await db.execute(stmt)).scalars().all()
    people = await _people_for(db, {e.lead_id for e in events if e.lead_id})
    return [_event_view(e, people) for e in events]


@router.get("/decisions", summary="Recent Oxygen decisions across prospects")
async def decisions(limit: int = Query(50, le=300), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(AgentDecision).order_by(AgentDecision.created_at.desc()).limit(limit))).scalars().all()
    people = await _people_for(db, {d.lead_id for d in rows})
    out = []
    for d in rows:
        task = await db.get(Task, d.task_id) if d.task_id else None
        provider = None
        if task is not None:
            done = (await db.execute(select(Event).where(Event.event_type.in_(("task_completed", "task_failed")),
                                                         Event.payload.contains(str(task.id))).limit(1))).scalar_one_or_none()
            provider = _payload(done.payload).get("provider") if done else None
        out.append({
            "id": str(d.id), "lead_id": str(d.lead_id), **people.get(d.lead_id, {}), "engine": d.engine,
            "model": d.model, "action": d.action, "capability": d.capability, "channel": d.channel,
            "reasoning": d.reasoning, "confidence": d.confidence, "uncertainty": d.uncertainty, "risks": d.risks,
            "expected_outcome": d.expected_outcome, "evidence_refs": d.evidence_refs, "parameters": d.parameters,
            "guardrails": d.guardrail_result, "status": d.status, "requires_approval": d.requires_approval,
            "details": d.context_snapshot, "task_id": str(d.task_id) if d.task_id else None,
            "task_status": task.status if task else None, "task_result": _payload(task.result) if task else None,
            "provider": provider, "created_at": d.created_at.isoformat(),
        })
    return out


@router.get("/stream", summary="Live event stream (SSE)")
async def stream(request: Request) -> StreamingResponse:
    async def gen():
        since = datetime.now(timezone.utc) - timedelta(seconds=2)
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            async with AsyncSessionLocal() as s:
                events = (await s.execute(select(Event).where(Event.occurred_at > since)
                                          .order_by(Event.occurred_at).limit(200))).scalars().all()
                people = await _people_for(s, {e.lead_id for e in events if e.lead_id})
            for e in events:
                since = max(since, e.occurred_at)
                yield f"event: oxygen\ndata: {json.dumps(_event_view(e, people), default=str)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(1.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
