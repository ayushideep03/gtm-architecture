"""
Prospect memory: everything Oxygen knows about one lead, assembled from the
authoritative Postgres tables. This is the context the Sales Controller
reasons over and what the dashboard's Lead Intelligence view shows.

Nothing here is cached or duplicated: it reads the CRM, the evidence ledger,
findings, interactions, decisions, tasks, approvals, work items and the
compliance state on demand.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.rules import check_contact_allowed
from app.memory.facts import FactLedger
from app.memory.records import decisions_for_lead, findings_for_lead, open_work_for_lead
from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.models.memory import Approval

_SKIP = {"tenant_id"}


def _row(obj: Any) -> Optional[dict]:
    if obj is None:
        return None
    out = {}
    for col in obj.__table__.columns:
        if col.name in _SKIP:
            continue
        v = getattr(obj, col.name)
        out[col.name] = v.isoformat() if isinstance(v, datetime) else (str(v) if isinstance(v, uuid.UUID) else v)
    return out


def _loads(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


async def build_prospect_memory(db: AsyncSession, lead_id: uuid.UUID, *, history_limit: int = 50) -> Optional[dict]:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        return None
    person = await db.get(Person, lead.person_id) if lead.person_id else None
    company = await db.get(Company, person.company_id) if person is not None and person.company_id else None
    ledger = FactLedger()

    interactions = (await db.execute(select(Interaction).where(Interaction.lead_id == lead_id)
                                     .order_by(Interaction.occurred_at.desc()).limit(history_limit))).scalars().all()
    tasks = (await db.execute(select(Task).where(Task.lead_id == lead_id)
                              .order_by(Task.created_at.desc()).limit(history_limit))).scalars().all()
    approvals = (await db.execute(select(Approval).where(Approval.lead_id == lead_id)
                                  .order_by(Approval.requested_at.desc()))).scalars().all()
    events = (await db.execute(select(Event).where(Event.lead_id == lead_id)
                               .order_by(Event.occurred_at.desc()).limit(history_limit))).scalars().all()
    findings = await findings_for_lead(db, lead_id)
    decisions = await decisions_for_lead(db, lead_id, limit=20)
    work = await open_work_for_lead(db, lead_id)
    compliance = await check_contact_allowed(db, lead)
    pipeline = await _pipeline_state(db, lead)

    return {
        **pipeline,
        "lead": _row(lead),
        "person": _row(person),
        "company": _row(company),
        "facts": {
            "company": await ledger.facts_for(db, "company", company.id) if company else [],
            "person": await ledger.facts_for(db, "person", person.id) if person else [],
            "lead": await ledger.facts_for(db, "lead", lead.id),
        },
        "findings": [
            {"id": str(f.id), "kind": f.kind, "summary": f.summary, "body": f.body, "citations": f.citations,
             "confidence": f.confidence, "source": f.source, "created_at": f.created_at.isoformat(),
             "stale_after": f.stale_after.isoformat() if f.stale_after else None}
            for f in findings
        ],
        "interactions": [
            {"id": str(i.id), "type": i.interaction_type, "direction": i.direction, "channel": i.channel,
             "subject": i.subject, "body": i.body, "provider": i.provider, "details": i.details,
             "occurred_at": i.occurred_at.isoformat(), "task_id": str(i.task_id) if i.task_id else None}
            for i in interactions
        ],
        "decisions": [
            {"id": str(d.id), "engine": d.engine, "model": d.model, "action": d.action, "capability": d.capability,
             "channel": d.channel, "parameters": d.parameters, "reasoning": d.reasoning,
             "evidence_refs": d.evidence_refs, "expected_outcome": d.expected_outcome, "confidence": d.confidence,
             "uncertainty": d.uncertainty, "risks": d.risks, "requires_approval": d.requires_approval,
             "guardrail_result": d.guardrail_result, "status": d.status,
             "task_id": str(d.task_id) if d.task_id else None, "created_at": d.created_at.isoformat()}
            for d in decisions
        ],
        "tasks": [
            {"id": str(t.id), "capability": t.task_type, "status": t.status, "payload": _loads(t.payload),
             "result": _loads(t.result), "error": t.error_message, "attempt_count": t.attempt_count,
             "needs_review": t.needs_review, "created_at": t.created_at.isoformat(),
             "completed_at": t.completed_at.isoformat() if t.completed_at else None}
            for t in tasks
        ],
        "approvals": [
            {"id": str(a.id), "task_id": str(a.task_id), "capability": a.capability, "status": a.status,
             "original_payload": a.original_payload, "final_payload": a.final_payload, "edited": a.edited,
             "reasoning": a.reasoning, "requested_at": a.requested_at.isoformat(),
             "decided_by": a.decided_by, "decided_at": a.decided_at.isoformat() if a.decided_at else None}
            for a in approvals
        ],
        "work_items": [
            {"id": str(w.id), "kind": w.kind, "reason": w.reason, "due_at": w.due_at.isoformat(),
             "priority": w.priority, "attempts": w.attempts}
            for w in work
        ],
        "events": [
            {"id": str(e.id), "type": e.event_type, "source": e.source, "occurred_at": e.occurred_at.isoformat(),
             "payload": _loads(e.payload)}
            for e in events
        ],
        "compliance": compliance.as_dict(),
    }


async def _pipeline_state(db: AsyncSession, lead: Lead) -> dict:
    """Campaign, follow-up schedule, meetings and drafts for this lead (the campaign layer)."""
    from app.models.pipeline import Campaign, Draft, FollowUp, Meeting
    from app.pipeline import followups as fu, meetings as mt, drafts as dr
    campaign = await db.get(Campaign, lead.campaign_id) if lead.campaign_id else None
    f_rows = (await db.execute(select(FollowUp).where(FollowUp.lead_id == lead.id).order_by(FollowUp.step))).scalars().all()
    m_rows = (await db.execute(select(Meeting).where(Meeting.lead_id == lead.id)
                               .order_by(Meeting.created_at.desc()))).scalars().all()
    d_rows = (await db.execute(select(Draft).where(Draft.lead_id == lead.id)
                               .order_by(Draft.created_at.desc()).limit(10))).scalars().all()
    return {
        "campaign": None if campaign is None else {
            "id": str(campaign.id), "name": campaign.name, "status": campaign.status, "offer": campaign.offer,
            "value_points": [v if isinstance(v, str) else v.get("topic") for v in campaign.value_points or []],
            "icp": campaign.icp, "sequence": campaign.sequence},
        "follow_ups": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fu.view(f).items()}
                       for f in f_rows],
        "meetings": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in mt.view(m).items()
                      if k in ("id", "status", "title", "start_at", "proposed_slots", "provider", "updated_at")}
                     for m in m_rows],
        "drafts": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dr.view(d).items()
                    if k in ("id", "kind", "step", "version", "status", "subject", "source_task_id")} for d in d_rows],
    }
