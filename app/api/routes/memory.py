"""
Memory, approvals and compliance APIs.

    GET  /api/v1/memory/leads/{lead_id}            full prospect memory (Lead Intelligence)
    POST /api/v1/memory/facts                      record a fact (e.g. human-entered) with evidence
    POST /api/v1/memory/facts/{id}/accept          accept a proposed fact
    POST /api/v1/memory/facts/{id}/dismiss         dismiss a proposed fact
    POST /api/v1/memory/leads/{lead_id}/findings   record a research finding
    POST /api/v1/memory/leads/{lead_id}/rechecks   schedule a recheck / evaluation
    GET  /api/v1/memory/work-items                 open work queue

    GET  /api/v1/approvals                         approval queue (filter by status)
    GET  /api/v1/approvals/{id}
    POST /api/v1/approvals/{id}/approve            approve (optionally with an edited payload)
    POST /api/v1/approvals/{id}/reject
    POST /api/v1/approvals/expire                  expire overdue approvals

    GET  /api/v1/compliance/leads/{lead_id}        contactability (suppression, basis, frequency)
    GET  /api/v1/compliance/suppressions
    POST /api/v1/compliance/suppressions
    POST /api/v1/compliance/suppressions/{id}/lift
    POST /api/v1/compliance/consents
    POST /api/v1/compliance/consents/withdraw

Reviewer / actor identity is passed explicitly until authentication is added;
every change is audited in the event log.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import approvals as approval_svc
from app.compliance.rules import (
    add_suppression,
    check_contact_allowed,
    lift_suppression,
    record_consent,
    withdraw_consent,
)
from app.core.auth import reviewer_for
from app.core.database import get_db
from app.core.tenancy import DEFAULT_TENANT_ID
from app.memory.context import build_prospect_memory
from app.memory.facts import ENTITY_MODELS, EvidenceInput, FactLedger
from app.memory.records import record_finding, schedule_work
from app.models.base import Company, Event, Lead, Person
from app.models.memory import Approval, Suppression, WorkItem

memory_router = APIRouter(prefix="/memory", tags=["memory"])
approvals_router = APIRouter(prefix="/approvals", tags=["approvals"])
compliance_router = APIRouter(prefix="/compliance", tags=["compliance"])


async def _lead_or_404(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lead {lead_id} not found")
    return lead


def _audit(db, lead_id, tenant_id, event_type, payload) -> None:
    db.add(Event(lead_id=lead_id, tenant_id=tenant_id, event_type=event_type, source="api",
                 payload=json.dumps(payload, default=str)))


# ── Memory ────────────────────────────────────────────────────────────────────

@memory_router.get("/leads/{lead_id}", summary="Full prospect memory for a lead")
async def get_prospect_memory(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    memory = await build_prospect_memory(db, lead_id)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lead {lead_id} not found")
    return memory


class EvidenceBody(BaseModel):
    kind: str
    detail: Optional[str] = None
    source_url: Optional[str] = None
    source_ref: Optional[str] = None


class FactBody(BaseModel):
    entity_type: Literal["company", "person", "lead"]
    entity_id: uuid.UUID
    field: str
    value: Any
    evidence: list[EvidenceBody] = Field(default_factory=lambda: [EvidenceBody(kind="human_entered")])
    actor: str = Field(..., description="Who is recording this fact")
    rationale: Optional[str] = None


@memory_router.post("/facts", summary="Record a fact with evidence")
async def post_fact(body: FactBody, db: AsyncSession = Depends(get_db)) -> dict:
    entity = await db.get(ENTITY_MODELS[body.entity_type], body.entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{body.entity_type} {body.entity_id} not found")
    try:
        outcome = await FactLedger().record(
            db, entity, body.field, body.value, [EvidenceInput(**e.model_dump()) for e in body.evidence],
            method=body.actor, rationale=body.rationale, decided_by=body.actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    await db.commit()
    return {"status": outcome.status, "changed": outcome.changed, "reason": outcome.reason,
            "fact_id": str(outcome.fact.id) if outcome.fact else None}


class ReviewBody(BaseModel):
    reviewer: str
    note: Optional[str] = None


@memory_router.post("/facts/{fact_id}/accept", summary="Accept a proposed fact")
async def accept_fact(fact_id: uuid.UUID, body: ReviewBody, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        fact = await FactLedger().accept(db, fact_id, body.reviewer)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    return {"fact_id": str(fact.id), "status": fact.status}


@memory_router.post("/facts/{fact_id}/dismiss", summary="Dismiss a proposed fact")
async def dismiss_fact(fact_id: uuid.UUID, body: ReviewBody, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        fact = await FactLedger().dismiss(db, fact_id, body.reviewer, body.note or "")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    return {"fact_id": str(fact.id), "status": fact.status}


class FindingBody(BaseModel):
    kind: str
    summary: str
    body: Optional[dict] = None
    citations: list[dict] = Field(default_factory=list)
    confidence: Optional[float] = None
    source: str


@memory_router.post("/leads/{lead_id}/findings", summary="Record a research finding")
async def post_finding(lead_id: uuid.UUID, body: FindingBody, db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _lead_or_404(db, lead_id)
    finding = await record_finding(db, tenant_id=lead.tenant_id, lead_id=lead.id, entity_type="lead",
                                   entity_id=lead.id, **body.model_dump())
    _audit(db, lead.id, lead.tenant_id, "finding_recorded", {"finding_id": str(finding.id), "kind": body.kind})
    await db.commit()
    return {"finding_id": str(finding.id), "stale_after": finding.stale_after}


class RecheckBody(BaseModel):
    reason: str = Field(..., min_length=10)
    kind: str = "recheck"
    due_in_hours: float = Field(24.0, ge=0)
    priority: int = 5
    created_by: str


@memory_router.post("/leads/{lead_id}/rechecks", summary="Schedule a recheck / re-evaluation")
async def post_recheck(lead_id: uuid.UUID, body: RecheckBody, db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _lead_or_404(db, lead_id)
    item = await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind=body.kind, reason=body.reason,
                               due_at=datetime.now(timezone.utc) + timedelta(hours=body.due_in_hours),
                               priority=body.priority, created_by=body.created_by)
    _audit(db, lead.id, lead.tenant_id, "work_scheduled", {"work_item_id": str(item.id), "kind": item.kind,
                                                           "due_at": item.due_at, "reason": item.reason})
    await db.commit()
    return {"work_item_id": str(item.id), "due_at": item.due_at, "kind": item.kind}


@memory_router.get("/work-items", summary="Open work queue")
async def list_work_items(due_only: bool = False, limit: int = Query(100, le=500),
                          db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(WorkItem).where(WorkItem.finished_at.is_(None)).order_by(WorkItem.due_at).limit(limit)
    if due_only:
        stmt = stmt.where(WorkItem.due_at <= datetime.now(timezone.utc))
    return [{"id": str(w.id), "lead_id": str(w.lead_id), "kind": w.kind, "reason": w.reason,
             "due_at": w.due_at, "priority": w.priority, "attempts": w.attempts, "leased_by": w.leased_by}
            for w in (await db.execute(stmt)).scalars().all()]


# ── Approvals ────────────────────────────────────────────────────────────────

async def _approval_view(db: AsyncSession, a: Approval) -> dict:
    lead = await db.get(Lead, a.lead_id) if a.lead_id else None
    person = await db.get(Person, lead.person_id) if lead and lead.person_id else None
    company = await db.get(Company, person.company_id) if person and person.company_id else None
    return {
        "id": str(a.id), "task_id": str(a.task_id), "lead_id": str(a.lead_id) if a.lead_id else None,
        "decision_id": str(a.decision_id) if a.decision_id else None, "capability": a.capability,
        "status": a.status, "original_payload": a.original_payload, "final_payload": a.final_payload,
        "edited": a.edited, "reasoning": a.reasoning, "evidence": a.evidence, "reviewer_note": a.reviewer_note,
        "requested_at": a.requested_at, "expires_at": a.expires_at, "decided_at": a.decided_at,
        "decided_by": a.decided_by,
        "prospect": {"name": " ".join(filter(None, [person.first_name, person.last_name])) if person else None,
                     "email": person.email if person else None, "title": person.title if person else None,
                     "company": company.name if company else None},
    }


@approvals_router.get("", summary="Approval queue")
async def list_approvals(status_filter: Optional[str] = Query("pending", alias="status"),
                         limit: int = Query(200, ge=1, le=1000),
                         db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Approval).order_by(Approval.requested_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Approval.status == status_filter)
    return [await _approval_view(db, a) for a in (await db.execute(stmt)).scalars().all()]


@approvals_router.get("/{approval_id}", summary="Approval detail")
async def get_approval(approval_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    a = await db.get(Approval, approval_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    return await _approval_view(db, a)


class ApproveBody(BaseModel):
    reviewer: str
    payload: Optional[dict] = None   # edited content; omit to approve as proposed
    note: Optional[str] = None


@approvals_router.post("/{approval_id}/approve", summary="Approve (optionally edited)")
async def approve_route(approval_id: uuid.UUID, body: ApproveBody, request: Request,
                        db: AsyncSession = Depends(get_db)) -> dict:
    try:
        a = await approval_svc.approve(db, approval_id, reviewer=reviewer_for(request, body.reviewer),
                                       payload=body.payload, note=body.note)
    except approval_svc.ApprovalError as exc:
        await db.commit()  # persist an expiry if one happened
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    await db.commit()
    return await _approval_view(db, a)


@approvals_router.post("/{approval_id}/reject", summary="Reject")
async def reject_route(approval_id: uuid.UUID, body: ReviewBody, request: Request,
                       db: AsyncSession = Depends(get_db)) -> dict:
    try:
        a = await approval_svc.reject(db, approval_id, reviewer=reviewer_for(request, body.reviewer), note=body.note)
    except approval_svc.ApprovalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    await db.commit()
    return await _approval_view(db, a)


@approvals_router.post("/expire", summary="Expire overdue approvals")
async def expire_route(db: AsyncSession = Depends(get_db)) -> dict:
    n = await approval_svc.expire_stale(db)
    await db.commit()
    return {"expired": n}


# ── Compliance ───────────────────────────────────────────────────────────────

@compliance_router.get("/leads/{lead_id}", summary="Can this lead be contacted right now?")
async def lead_compliance(lead_id: uuid.UUID, channel: str = "email", db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _lead_or_404(db, lead_id)
    return (await check_contact_allowed(db, lead, channel)).as_dict()


class SuppressionBody(BaseModel):
    scope: Literal["email", "domain", "person", "company"]
    value: str
    reason: Literal["unsubscribe", "hard_bounce", "complaint", "manual", "legal", "do_not_contact_request"]
    created_by: str
    note: Optional[str] = None
    source: str = "manual"


@compliance_router.get("/suppressions", summary="Active suppressions")
async def list_suppressions(include_lifted: bool = False, db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Suppression).order_by(Suppression.created_at.desc())
    if not include_lifted:
        stmt = stmt.where(Suppression.lifted_at.is_(None))
    return [{"id": str(s.id), "scope": s.scope, "value": s.value, "reason": s.reason, "source": s.source,
             "note": s.note, "created_by": s.created_by, "created_at": s.created_at, "lifted_at": s.lifted_at}
            for s in (await db.execute(stmt)).scalars().all()]


@compliance_router.post("/suppressions", summary="Add a suppression")
async def post_suppression(body: SuppressionBody, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        s = await add_suppression(db, tenant_id=DEFAULT_TENANT_ID, scope=body.scope, value=body.value,
                                  reason=body.reason, source=body.source, created_by=body.created_by, note=body.note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    _audit(db, None, DEFAULT_TENANT_ID, "suppression_added", {"suppression_id": str(s.id), "scope": s.scope,
                                                               "value": s.value, "reason": s.reason, "by": body.created_by})
    await db.commit()
    return {"id": str(s.id), "scope": s.scope, "value": s.value}


class LiftBody(BaseModel):
    lifted_by: str


@compliance_router.post("/suppressions/{suppression_id}/lift", summary="Lift a suppression")
async def lift_route(suppression_id: uuid.UUID, body: LiftBody, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        s = await lift_suppression(db, suppression_id, body.lifted_by)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    _audit(db, None, s.tenant_id, "suppression_lifted", {"suppression_id": str(s.id), "by": body.lifted_by})
    await db.commit()
    return {"id": str(s.id), "lifted_at": s.lifted_at}


class ConsentBody(BaseModel):
    person_id: uuid.UUID
    basis: str
    source: str
    channel: Optional[str] = None
    evidence: Optional[str] = None


@compliance_router.post("/consents", summary="Record a lawful basis for a contact")
async def post_consent(body: ConsentBody, db: AsyncSession = Depends(get_db)) -> dict:
    person = await db.get(Person, body.person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    c = await record_consent(db, tenant_id=person.tenant_id, **body.model_dump())
    await db.commit()
    return {"id": str(c.id), "basis": c.basis}


class WithdrawBody(BaseModel):
    person_id: uuid.UUID
    actor: str


@compliance_router.post("/consents/withdraw", summary="Withdraw all consents for a contact")
async def withdraw_route(body: WithdrawBody, db: AsyncSession = Depends(get_db)) -> dict:
    person = await db.get(Person, body.person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    n = await withdraw_consent(db, tenant_id=person.tenant_id, person_id=person.id)
    await db.commit()
    return {"withdrawn": n}
