"""
Campaign pipeline API (stages 1-12). Every route runs inside the caller's organisation
(row-level security) and is authorised by role (app/core/auth.py ROLE_POLICY).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import principal_of
from app.core.database import get_db
from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.models.memory import Approval, ResearchFinding
from app.models.pipeline import Campaign, CampaignAccount, Draft, FollowUp, Meeting
from app.pipeline import campaigns as camp, drafts as dr, followups as fu, meetings as mt, metrics

router = APIRouter(tags=["pipeline"])
SIGNAL_KINDS = ("news", "hiring", "technology", "expansion", "buying_signal", "digital_transformation", "challenge")


def _actor(request: Request) -> str:
    p = principal_of(request)
    return p.name if p else "unknown"


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


async def _get(db: AsyncSession, model, id_: uuid.UUID, what: str):
    row = await db.get(model, id_)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return row


def _page(page: int, size: int) -> tuple[int, int]:
    return (max(page, 1) - 1) * size, size


# ── Campaigns ─────────────────────────────────────────────────────────────────

class CampaignBody(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    offer: Optional[str] = Field(None, max_length=4000)
    value_points: Optional[list[Any]] = None
    icp: Optional[dict] = None
    sequence: Optional[list[dict]] = None


class StatusBody(BaseModel):
    status: Literal["active", "paused", "stopped"]


@router.get("/campaigns", summary="Campaigns with headline performance")
async def list_campaigns(db: AsyncSession = Depends(get_db)) -> list[dict]:
    perf = {p["id"]: p for p in await metrics.campaign_performance(db)}
    rows = (await db.execute(select(Campaign).order_by(Campaign.created_at))).scalars().all()
    return [{**camp.view(c), "performance": perf.get(str(c.id))} for c in rows]


@router.post("/campaigns", summary="Create a campaign")
async def create_campaign(body: CampaignBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    if not body.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
    try:
        c = await camp.create(db, body.model_dump(), _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return camp.view(c)


@router.get("/campaigns/{campaign_id}", summary="Campaign detail with metrics")
async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    return {**camp.view(c), "metrics": await metrics.summary(db, c.id)}


@router.patch("/campaigns/{campaign_id}", summary="Edit a campaign (ICP, offer, sequence...)")
async def edit_campaign(campaign_id: uuid.UUID, body: CampaignBody, request: Request,
                        db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        await camp.update_campaign(db, c, body.model_dump(), _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return camp.view(c)


@router.post("/campaigns/{campaign_id}/status", summary="Activate, pause, resume or stop")
async def campaign_status(campaign_id: uuid.UUID, body: StatusBody, request: Request,
                          db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        effects = await camp.set_status(db, c, body.status, _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return {**camp.view(c), "effects": effects}


@router.post("/campaigns/{campaign_id}/start", summary="Start the pipeline: activate and discover leads")
async def start_campaign(campaign_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        result = await camp.start(db, c, _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return result


@router.get("/campaigns/{campaign_id}/activity", summary="Campaign activity log")
async def campaign_activity(campaign_id: uuid.UUID, limit: int = Query(100, ge=1, le=500),
                            db: AsyncSession = Depends(get_db)) -> list[dict]:
    lead_ids = select(Lead.id).where(Lead.campaign_id == campaign_id)
    rows = (await db.execute(select(Event).where(or_(Event.lead_id.in_(lead_ids),
                                                     Event.payload.contains(str(campaign_id))))
                             .order_by(Event.occurred_at.desc()).limit(limit))).scalars().all()
    return [_event_view(e) for e in rows]


# ── Target accounts & discovery (stages 1-2) ──────────────────────────────────

class AccountIn(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    domain: str = Field(..., max_length=255)
    industry: Optional[str] = Field(None, max_length=128)
    employees: Optional[int] = Field(None, ge=1, le=5_000_000)
    city: Optional[str] = Field(None, max_length=128)
    country: Optional[str] = Field(None, max_length=64)
    technologies: Optional[list[str]] = None
    priority: Literal["high", "medium", "low"] = "medium"


class AccountsBody(BaseModel):
    accounts: list[AccountIn] = Field(..., min_length=1, max_length=500)


class CsvBody(BaseModel):
    csv: str = Field(..., max_length=2_000_000)


class AccountPatch(BaseModel):
    priority: Optional[Literal["high", "medium", "low"]] = None
    selected: Optional[bool] = None


class DiscoverBody(BaseModel):
    account_ids: Optional[list[uuid.UUID]] = None


@router.get("/campaigns/{campaign_id}/accounts", summary="Target accounts (search, filter, paginate)")
async def list_accounts(campaign_id: uuid.UUID, search: Optional[str] = None, industry: Optional[str] = None,
                        discovery_status: Optional[str] = None, priority: Optional[str] = None,
                        selected: Optional[bool] = None, page: int = Query(1, ge=1),
                        page_size: int = Query(25, ge=1, le=200), db: AsyncSession = Depends(get_db)) -> dict:
    return await _accounts(db, campaign_id, search, industry, discovery_status, priority, selected, page, page_size)


async def _accounts(db, campaign_id, search, industry, discovery_status, priority, selected, page, page_size):
    stmt = select(CampaignAccount, Company).join(Company, Company.id == CampaignAccount.company_id)
    if campaign_id:
        stmt = stmt.where(CampaignAccount.campaign_id == campaign_id)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(Company.name).like(like), func.lower(Company.domain).like(like)))
    if industry:
        stmt = stmt.where(Company.industry == industry)
    if discovery_status:
        stmt = stmt.where(CampaignAccount.discovery_status == discovery_status)
    if priority:
        stmt = stmt.where(CampaignAccount.priority == priority)
    if selected is not None:
        stmt = stmt.where(CampaignAccount.selected.is_(selected))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    off, lim = _page(page, page_size)
    rows = (await db.execute(stmt.order_by(CampaignAccount.priority, Company.name).offset(off).limit(lim))).all()
    industries = sorted({i for (i,) in (await db.execute(select(Company.industry).distinct())).all() if i})
    return {"total": total, "page": page, "page_size": page_size, "industries": industries,
            "items": [camp.account_view(a, co) for a, co in rows]}


@router.post("/campaigns/{campaign_id}/accounts", summary="Add target accounts manually")
async def add_accounts(campaign_id: uuid.UUID, body: AccountsBody, request: Request,
                       db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        accts = await camp.add_accounts(db, c, [a.model_dump() for a in body.accounts], _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return {"added": len(accts)}


@router.post("/campaigns/{campaign_id}/accounts/import", summary="Import target accounts from CSV")
async def import_accounts(campaign_id: uuid.UUID, body: CsvBody, request: Request,
                          db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        rows = camp.parse_csv(body.csv)
        accts = await camp.add_accounts(db, c, rows, _actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return {"imported": len(accts)}


@router.patch("/campaigns/{campaign_id}/accounts/{account_id}", summary="Set priority / select or deselect")
async def patch_account(campaign_id: uuid.UUID, account_id: uuid.UUID, body: AccountPatch,
                        db: AsyncSession = Depends(get_db)) -> dict:
    a = await _get(db, CampaignAccount, account_id, "account")
    if a.campaign_id != campaign_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    await camp.update_account(db, a, priority=body.priority, selected=body.selected)
    await db.commit()
    return camp.account_view(a, await db.get(Company, a.company_id))


@router.post("/campaigns/{campaign_id}/discover", summary="Run lead discovery for selected accounts")
async def discover(campaign_id: uuid.UUID, body: DiscoverBody, request: Request,
                   db: AsyncSession = Depends(get_db)) -> dict:
    c = await _get(db, Campaign, campaign_id, "campaign")
    try:
        result = await camp.discover(db, c, _actor(request), body.account_ids)
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return result


@router.get("/discovery/companies", summary="Discovered companies across campaigns")
async def discovery_companies(campaign_id: Optional[uuid.UUID] = None, search: Optional[str] = None,
                              industry: Optional[str] = None, discovery_status: Optional[str] = None,
                              page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                              db: AsyncSession = Depends(get_db)) -> dict:
    return await _accounts(db, campaign_id, search, industry, discovery_status, None, None, page, page_size)


# ── Contacts & qualification (stages 3-4) ─────────────────────────────────────

@router.get("/contacts", summary="Identified contacts (search, filter, paginate)")
async def contacts(campaign_id: Optional[uuid.UUID] = None, search: Optional[str] = None,
                   verification: Optional[str] = None, lead_status: Optional[str] = None,
                   department: Optional[str] = None, page: int = Query(1, ge=1),
                   page_size: int = Query(25, ge=1, le=200), db: AsyncSession = Depends(get_db)) -> dict:
    stmt = (select(Lead, Person, Company).join(Person, Person.id == Lead.person_id)
            .outerjoin(Company, Company.id == Person.company_id))
    if campaign_id:
        stmt = stmt.where(Lead.campaign_id == campaign_id)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(Person.first_name).like(like), func.lower(Person.last_name).like(like),
                              func.lower(Person.email).like(like), func.lower(Company.name).like(like),
                              func.lower(Person.title).like(like)))
    if verification:
        stmt = stmt.where(Person.verification_status == verification)
    if lead_status:
        stmt = stmt.where(Lead.status == lead_status)
    if department:
        stmt = stmt.where(Person.department == department)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    off, lim = _page(page, page_size)
    rows = (await db.execute(stmt.order_by(Company.name, Person.last_name).offset(off).limit(lim))).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [{
        "lead_id": str(l.id), "person_id": str(p.id), "name": " ".join(filter(None, [p.first_name, p.last_name])),
        "title": p.title, "department": p.department, "seniority": p.seniority, "email": p.email,
        "location": p.location, "verification_status": p.verification_status or "unknown",
        "source": p.enrichment_source or l.source, "company": co.name if co else None,
        "company_domain": co.domain if co else None, "lead_status": l.status, "score": l.score,
        "campaign_id": str(l.campaign_id) if l.campaign_id else None,
        "demo": bool(p.email and p.email.endswith(".example"))} for l, p, co in rows]}


class IcpPreviewBody(BaseModel):
    icp: dict


@router.get("/qualification", summary="Qualification results with explanations")
async def qualification(campaign_id: Optional[uuid.UUID] = None, fit: Optional[str] = None,
                        outcome: Optional[Literal["qualified", "disqualified"]] = None,
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                        db: AsyncSession = Depends(get_db)) -> dict:
    latest = (select(ResearchFinding.lead_id, func.max(ResearchFinding.created_at).label("at"))
              .where(ResearchFinding.kind == "qualification").group_by(ResearchFinding.lead_id).subquery())
    stmt = (select(Lead, Person, Company, ResearchFinding)
            .join(latest, latest.c.lead_id == Lead.id)
            .join(ResearchFinding, (ResearchFinding.lead_id == Lead.id) & (ResearchFinding.created_at == latest.c.at)
                  & (ResearchFinding.kind == "qualification"))
            .join(Person, Person.id == Lead.person_id).outerjoin(Company, Company.id == Person.company_id))
    if campaign_id:
        stmt = stmt.where(Lead.campaign_id == campaign_id)
    if outcome:
        stmt = stmt.where(Lead.status == "disqualified") if outcome == "disqualified" else stmt.where(
            Lead.status != "disqualified")
    rows = (await db.execute(stmt.order_by(Lead.score.desc().nullslast()))).all()
    items = []
    for l, p, co, f in rows:
        body = f.body or {}
        if fit and body.get("fit") != fit:
            continue
        items.append({"lead_id": str(l.id), "name": " ".join(filter(None, [p.first_name, p.last_name])),
                      "title": p.title, "company": co.name if co else None, "industry": co.industry if co else None,
                      "employees": co.employee_count if co else None, "country": co.country if co else None,
                      "score": body.get("score", l.score), "fit": body.get("fit"),
                      "qualified": body.get("qualified", body.get("final_qualified")), "lead_status": l.status,
                      "dimensions": body.get("dimensions", []), "reasons_for": body.get("reasons_for", []),
                      "reasons_against": body.get("reasons_against", []), "engine": body.get("engine", f.source),
                      "evaluated_at": f.created_at})
    off, lim = _page(page, page_size)
    return {"total": len(items), "page": page, "page_size": page_size, "items": items[off: off + lim]}


@router.post("/campaigns/{campaign_id}/qualification/preview", summary="Score the campaign's leads against an ICP "
                                                                     "without saving")
async def preview_icp(campaign_id: uuid.UUID, body: IcpPreviewBody, db: AsyncSession = Depends(get_db)) -> dict:
    from app.pipeline.icp import score_prospect
    try:
        icp = camp.validate_icp(body.icp)
    except ValueError as exc:
        raise _bad(exc)
    rows = (await db.execute(select(Lead, Person, Company).join(Person, Person.id == Lead.person_id)
                             .outerjoin(Company, Company.id == Person.company_id)
                             .where(Lead.campaign_id == campaign_id))).all()
    out = []
    for l, p, co in rows:
        r = score_prospect(co, p, icp)
        out.append({"lead_id": str(l.id), "name": f"{p.first_name} {p.last_name or ''}".strip(),
                    "company": co.name if co else None, "current_score": l.score, "new_score": r.score,
                    "fit": r.fit, "qualified": r.qualified})
    changed = sum(1 for x in out if x["current_score"] is not None and x["new_score"] != x["current_score"])
    return {"icp": icp, "leads": len(out), "qualified": sum(1 for x in out if x["qualified"]), "changed": changed,
            "items": sorted(out, key=lambda x: -x["new_score"])}


@router.post("/campaigns/{campaign_id}/qualification/recalculate", summary="Re-score all campaign leads with the "
                                                                         "current ICP")
async def recalculate(campaign_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    from app.pipeline.icp import qualify_lead_record
    c = await _get(db, Campaign, campaign_id, "campaign")
    leads = (await db.execute(select(Lead).where(Lead.campaign_id == c.id,
                                                 Lead.status.in_(("new", "enriching", "qualified", "disqualified")))
                              )).scalars().all()
    q = 0
    for lead in leads:
        r = await qualify_lead_record(db, lead, source=f"icp_rules (recalculated by {_actor(request)})")
        q += int(r.qualified)
    db.add(Event(tenant_id=c.tenant_id, event_type="icp_recalculated", source="api",
                 payload=json.dumps({"campaign_id": str(c.id), "leads": len(leads), "qualified": q,
                                     "by": _actor(request)})))
    await db.commit()
    return {"rescored": len(leads), "qualified": q, "disqualified": len(leads) - q,
            "note": "Leads already contacted keep their stage; only their score is unchanged."}


# ── Research (stage 5) ────────────────────────────────────────────────────────

@router.get("/research", summary="Research signals (filter by lead, company or campaign)")
async def research(lead_id: Optional[uuid.UUID] = None, company_id: Optional[uuid.UUID] = None,
                   campaign_id: Optional[uuid.UUID] = None, kind: Optional[str] = None,
                   limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)) -> dict:
    stmt = select(ResearchFinding).where(ResearchFinding.kind.in_(SIGNAL_KINDS + ("intent_assessment",)))
    if lead_id:
        stmt = stmt.where(ResearchFinding.lead_id == lead_id)
    if company_id:
        stmt = stmt.where(ResearchFinding.entity_id == company_id)
    if campaign_id:
        stmt = stmt.where(ResearchFinding.lead_id.in_(select(Lead.id).where(Lead.campaign_id == campaign_id)))
    if kind:
        stmt = stmt.where(ResearchFinding.kind == kind)
    rows = (await db.execute(stmt.order_by(ResearchFinding.created_at.desc()).limit(limit))).scalars().all()
    seen, items = set(), []
    for f in rows:   # one company's signals are recorded per lead; show each once per company
        key = (str(f.entity_id), f.summary)
        if key in seen:
            continue
        seen.add(key)
        body = f.body or {}
        items.append({"id": str(f.id), "lead_id": str(f.lead_id) if f.lead_id else None,
                      "company_id": str(f.entity_id) if f.entity_type == "company" else None, "kind": f.kind,
                      "label": body.get("label") or f.kind.replace("_", " ").title(), "statement": f.summary,
                      "source": body.get("source") or f.source, "source_ref": body.get("source_ref"),
                      "why_it_matters": body.get("why_it_matters"), "strength": body.get("strength"),
                      "confidence": f.confidence, "simulated": bool(body.get("demo") or "simulated" in (f.source or "")),
                      "citations": f.citations, "created_at": f.created_at})
    companies: dict[str, dict] = {}
    if items:
        ids = {uuid.UUID(i["company_id"]) for i in items if i["company_id"]}
        for co in (await db.execute(select(Company).where(Company.id.in_(ids)))).scalars().all():
            companies[str(co.id)] = {"id": str(co.id), "name": co.name, "domain": co.domain, "industry": co.industry}
    return {"items": items, "companies": companies}


@router.post("/research/leads/{lead_id}/run", summary="Run research for a prospect now")
async def run_research(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _get(db, Lead, lead_id, "lead")
    return await _run_task(db, lead, "research_prospect", {"lead_id": str(lead.id)})


async def _run_task(db: AsyncSession, lead: Lead, capability: str, payload: dict) -> dict:
    from app.core.database import AsyncSessionLocal
    from app.execution.executor import TaskExecutor
    t = Task(tenant_id=lead.tenant_id, lead_id=lead.id, task_type=capability, status="pending",
             payload=json.dumps(payload), priority=7)
    db.add(t)
    await db.commit()
    async with AsyncSessionLocal() as s:
        out = await TaskExecutor().execute(t.id, s)
    return out.model_dump(mode="json")


# ── Drafts (stage 6) ──────────────────────────────────────────────────────────

class GenerateBody(BaseModel):
    lead_id: uuid.UUID
    angle: Optional[str] = Field(None, max_length=200)
    follow_up: bool = False


class DraftEdit(BaseModel):
    subject: Optional[str] = Field(None, max_length=200)
    body: str = Field(..., min_length=1, max_length=8000)


@router.get("/drafts", summary="Drafts (first touch, follow-ups, replies)")
async def list_drafts(status_filter: Optional[str] = Query(None, alias="status"), kind: Optional[str] = None,
                      lead_id: Optional[uuid.UUID] = None, campaign_id: Optional[uuid.UUID] = None,
                      page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                      db: AsyncSession = Depends(get_db)) -> dict:
    stmt = select(Draft, Person, Company).join(Lead, Lead.id == Draft.lead_id).join(Person, Person.id == Lead.person_id)\
        .outerjoin(Company, Company.id == Person.company_id)
    if status_filter:
        stmt = stmt.where(Draft.status == status_filter)
    else:
        stmt = stmt.where(Draft.status != "superseded")
    if kind:
        stmt = stmt.where(Draft.kind == kind)
    if lead_id:
        stmt = stmt.where(Draft.lead_id == lead_id)
    if campaign_id:
        stmt = stmt.where(Draft.campaign_id == campaign_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    off, lim = _page(page, page_size)
    rows = (await db.execute(stmt.order_by(Draft.updated_at.desc()).offset(off).limit(lim))).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {**dr.view(d), "prospect": {"name": f"{p.first_name} {p.last_name or ''}".strip(), "email": p.email,
                                    "title": p.title, "company": co.name if co else None}} for d, p, co in rows]}


@router.post("/drafts/generate", summary="Generate a draft now (AI provider behind the drafting interface)")
async def generate_draft(body: GenerateBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _get(db, Lead, body.lead_id, "lead")
    try:
        d = await dr.generate(db, lead, actor=_actor(request), angle=body.angle, follow_up=body.follow_up)
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return dr.view(d)


@router.post("/drafts/{draft_id}/regenerate", summary="Generate a different version")
async def regenerate_draft(draft_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    d = await _get(db, Draft, draft_id, "draft")
    if d.kind == "reply":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reply drafts are regenerated from the inbox")
    lead = await _get(db, Lead, d.lead_id, "lead")
    try:
        new = await dr.generate(db, lead, actor=_actor(request), variant=d.version, follow_up=d.kind == "follow_up")
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return dr.view(new)


@router.put("/drafts/{draft_id}", summary="Edit and save a draft")
async def edit_draft(draft_id: uuid.UUID, body: DraftEdit, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    d = await _get(db, Draft, draft_id, "draft")
    try:
        await dr.save_edit(db, d, subject=body.subject, body=body.body, actor=_actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return dr.view(d)


@router.post("/drafts/{draft_id}/submit", summary="Submit a draft for approval")
async def submit_draft(draft_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    d = await _get(db, Draft, draft_id, "draft")
    try:
        res = await dr.submit(db, d, actor=_actor(request))
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    return {**dr.view(d), **res}


# ── Follow-ups (stage 8) ──────────────────────────────────────────────────────

class CancelBody(BaseModel):
    reason: str = Field("Cancelled by a user", max_length=300)


@router.get("/follow-ups", summary="Follow-up schedule")
async def list_follow_ups(status_filter: Optional[str] = Query(None, alias="status"),
                          lead_id: Optional[uuid.UUID] = None, campaign_id: Optional[uuid.UUID] = None,
                          limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(FollowUp, Person, Company).join(Lead, Lead.id == FollowUp.lead_id)\
        .join(Person, Person.id == Lead.person_id).outerjoin(Company, Company.id == Person.company_id)
    if status_filter:
        stmt = stmt.where(FollowUp.status == status_filter)
    if lead_id:
        stmt = stmt.where(FollowUp.lead_id == lead_id)
    if campaign_id:
        stmt = stmt.where(FollowUp.campaign_id == campaign_id)
    rows = (await db.execute(stmt.order_by(FollowUp.due_at).limit(limit))).all()
    return [{**fu.view(f), "prospect": {"name": f"{p.first_name} {p.last_name or ''}".strip(),
                                        "company": co.name if co else None}} for f, p, co in rows]


@router.post("/follow-ups/{follow_up_id}/cancel", summary="Cancel a scheduled follow-up")
async def cancel_follow_up(follow_up_id: uuid.UUID, body: CancelBody, request: Request,
                           db: AsyncSession = Depends(get_db)) -> dict:
    f = await _get(db, FollowUp, follow_up_id, "follow-up")
    if f.status not in fu.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"follow-up is already {f.status}")
    f.status, f.reason = "cancelled", f"{body.reason} ({_actor(request)})"
    await db.commit()
    return fu.view(f)


@router.post("/follow-ups/{follow_up_id}/run-now", summary="Make a scheduled follow-up due now")
async def run_follow_up_now(follow_up_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    from datetime import datetime, timezone
    f = await _get(db, FollowUp, follow_up_id, "follow-up")
    if f.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"follow-up is {f.status}")
    f.due_at = datetime.now(timezone.utc)
    n = await fu.process_due(db)
    await db.commit()
    return {**fu.view(f), "made_due": n}


# ── Inbox (stage 9-10) ────────────────────────────────────────────────────────

@router.get("/inbox", summary="Conversations")
async def inbox(search: Optional[str] = None, only_replied: bool = False, db: AsyncSession = Depends(get_db)) -> list[dict]:
    last = (select(Interaction.lead_id, func.max(Interaction.occurred_at).label("at"), func.count(Interaction.id).label("n"),
                   func.count(Interaction.id).filter(Interaction.direction == "inbound").label("inbound"))
            .where(Interaction.interaction_type.in_(("email_sent", "email_received")))
            .group_by(Interaction.lead_id).subquery())
    stmt = (select(Lead, Person, Company, last.c.at, last.c.n, last.c.inbound).join(last, last.c.lead_id == Lead.id)
            .join(Person, Person.id == Lead.person_id).outerjoin(Company, Company.id == Person.company_id))
    if only_replied:
        stmt = stmt.where(last.c.inbound > 0)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(or_(func.lower(Person.first_name).like(like), func.lower(Person.last_name).like(like),
                              func.lower(Company.name).like(like)))
    rows = (await db.execute(stmt.order_by(last.c.at.desc()).limit(300))).all()
    cats = dict((await db.execute(
        select(ResearchFinding.lead_id, func.split_part(func.max(func.concat(ResearchFinding.created_at, "|",
                                                                         ResearchFinding.summary)), "|", 2))
        .where(ResearchFinding.kind == "reply_analysis").group_by(ResearchFinding.lead_id))).all())
    return [{"lead_id": str(l.id), "name": f"{p.first_name} {p.last_name or ''}".strip(), "email": p.email,
             "company": co.name if co else None, "lead_status": l.status, "last_at": at, "messages": n,
             "replies": inbound, "category": (cats.get(l.id) or "").split(":", 1)[0] or None}
            for l, p, co, at, n, inbound in rows]


@router.get("/inbox/{lead_id}", summary="One conversation, with classification and suggested responses")
async def conversation(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    lead = await _get(db, Lead, lead_id, "lead")
    person = await db.get(Person, lead.person_id) if lead.person_id else None
    msgs = (await db.execute(select(Interaction).where(Interaction.lead_id == lead_id)
                             .order_by(Interaction.occurred_at))).scalars().all()
    analyses = (await db.execute(select(ResearchFinding).where(ResearchFinding.lead_id == lead_id,
                                                               ResearchFinding.kind == "reply_analysis")
                                 .order_by(ResearchFinding.created_at))).scalars().all()
    replies = (await db.execute(select(Draft).where(Draft.lead_id == lead_id, Draft.kind == "reply")
                                .order_by(Draft.created_at.desc()))).scalars().all()
    return {"lead_id": str(lead.id), "lead_status": lead.status,
            "prospect": {"name": f"{person.first_name} {person.last_name or ''}".strip() if person else None,
                         "email": person.email if person else None},
            "messages": [{"id": str(m.id), "type": m.interaction_type, "direction": m.direction, "subject": m.subject,
                          "body": m.body, "at": m.occurred_at, "provider": m.provider, "channel": m.channel,
                          "simulated": (m.details or {}).get("mode") in ("simulation", "dry_run")
                          or (m.provider or "").startswith(("demo", "mock"))} for m in msgs],
            "classifications": [{"id": str(a.id), "category": a.summary.split(":", 1)[0], "summary": a.summary,
                                 "objection": (a.body or {}).get("objection"), "quotes": (a.body or {}).get("quotes"),
                                 "model": a.source, "at": a.created_at} for a in analyses],
            "suggested_responses": [dr.view(d) for d in replies]}


# ── Meetings (stage 11) ───────────────────────────────────────────────────────

class RespondBody(BaseModel):
    action: Literal["accept", "decline", "reschedule"]
    slot_index: int = Field(0, ge=0, le=10)


@router.get("/meetings", summary="Meetings")
async def list_meetings(status_filter: Optional[str] = Query(None, alias="status"),
                        db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Meeting, Person, Company).join(Lead, Lead.id == Meeting.lead_id)\
        .join(Person, Person.id == Lead.person_id).outerjoin(Company, Company.id == Person.company_id)
    if status_filter:
        stmt = stmt.where(Meeting.status == status_filter)
    rows = (await db.execute(stmt.order_by(Meeting.updated_at.desc()))).all()
    return [{**mt.view(m), "prospect": {"name": f"{p.first_name} {p.last_name or ''}".strip(), "email": p.email,
                                        "title": p.title, "company": co.name if co else None}} for m, p, co in rows]


@router.get("/meetings/availability", summary="Demo calendar availability (next free slots)")
async def availability() -> dict:
    from app.integrations.demo_providers import DEFAULT_AVAILABILITY, demo_slots
    return {"simulated": True, "availability": DEFAULT_AVAILABILITY,
            "next_free_slots": [s.isoformat() for s in demo_slots(count=6)]}


@router.post("/meetings/{meeting_id}/respond", summary="Simulate the prospect's calendar response (demo)")
async def respond_meeting(meeting_id: uuid.UUID, body: RespondBody, request: Request,
                          db: AsyncSession = Depends(get_db)) -> dict:
    from app.services.demo import demo_allowed
    if not demo_allowed():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prospect responses are simulated only in demo mode")
    m = await _get(db, Meeting, meeting_id, "meeting")
    try:
        res = await mt.respond(db, m, body.action, slot_index=body.slot_index, by=f"prospect (simulated by {_actor(request)})")
    except ValueError as exc:
        raise _bad(exc)
    await db.commit()
    await db.refresh(m)
    return {**mt.view(m), "inbound": res}


# ── Metrics & workflow (stage 12, engine visibility) ──────────────────────────

@router.get("/metrics/summary", summary="Management metrics from database records")
async def metrics_summary(campaign_id: Optional[uuid.UUID] = None, db: AsyncSession = Depends(get_db)) -> dict:
    return await metrics.summary(db, campaign_id)


@router.get("/metrics/campaigns", summary="Per-campaign performance")
async def metrics_campaigns(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await metrics.campaign_performance(db)


STAGES = ["Target account", "Lead discovery", "Contact identified", "ICP qualification", "Research & intent",
          "Personalised draft", "Approval", "Follow-up", "Reply", "Response", "Meeting", "Analytics"]


@router.get("/workflow/leads/{lead_id}", summary="Where this prospect is in the 12-stage pipeline")
async def workflow(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    from app.models.memory import OutcomeAttribution
    lead = await _get(db, Lead, lead_id, "lead")
    person = await db.get(Person, lead.person_id) if lead.person_id else None

    async def n(stmt) -> int:
        return (await db.execute(stmt)).scalar() or 0
    acct = await n(select(func.count(CampaignAccount.id)).where(
        CampaignAccount.campaign_id == lead.campaign_id, CampaignAccount.company_id == (person.company_id if person else None)))
    signals = await n(select(func.count(ResearchFinding.id)).where(ResearchFinding.lead_id == lead_id,
                                                                   ResearchFinding.kind.in_(SIGNAL_KINDS)))
    drafts_n = await n(select(func.count(Draft.id)).where(Draft.lead_id == lead_id))
    appr = dict((await db.execute(select(Approval.status, func.count(Approval.id)).where(Approval.lead_id == lead_id)
                                  .group_by(Approval.status))).all())
    fus = dict((await db.execute(select(FollowUp.status, func.count(FollowUp.id)).where(FollowUp.lead_id == lead_id)
                                 .group_by(FollowUp.status))).all())
    replies = await n(select(func.count(Interaction.id)).where(Interaction.lead_id == lead_id,
                                                               Interaction.interaction_type == "email_received"))
    reply_drafts = await n(select(func.count(Draft.id)).where(Draft.lead_id == lead_id, Draft.kind == "reply"))
    meeting = (await db.execute(select(Meeting).where(Meeting.lead_id == lead_id)
                                .order_by(Meeting.updated_at.desc()).limit(1))).scalar_one_or_none()
    attributed = await n(select(func.count(OutcomeAttribution.id)).where(OutcomeAttribution.lead_id == lead_id))
    qual = "done" if lead.score is not None and lead.status != "disqualified" else \
        "failed" if lead.status == "disqualified" else "pending"
    stages = [
        ("done" if acct or not lead.campaign_id else "pending", "account selected" if acct else "added directly"),
        ("done", f"source: {lead.source}"),
        ("done" if person and person.email else "pending", person.verification_status if person else None),
        (qual, f"score {lead.score}" if lead.score is not None else None),
        ("done" if signals else "pending", f"{signals} signal(s)"),
        ("done" if drafts_n else "pending", f"{drafts_n} draft(s)"),
        ("active" if appr.get("pending") else "done" if appr.get("approved") else "pending",
         ", ".join(f"{v} {k}" for k, v in appr.items()) or None),
        ("active" if fus.get("scheduled") or fus.get("due") or fus.get("drafted") else "done" if fus else "pending",
         ", ".join(f"{v} {k}" for k, v in fus.items()) or None),
        ("done" if replies else "pending", f"{replies} repl{'y' if replies == 1 else 'ies'}"),
        ("done" if reply_drafts or (meeting is not None) else "pending",
         "answer drafted" if reply_drafts else "meeting proposed" if meeting else None),
        ("done" if meeting and meeting.status == "booked" else "active" if meeting else "pending",
         meeting.status if meeting else None),
        ("done" if attributed else "pending", f"{attributed} touch(es) attributed"),
    ]
    if lead.status in ("closed_lost", "disqualified"):
        stages = [(s if s != "pending" else "skipped", d) for s, d in stages]
    events = (await db.execute(select(Event).where(Event.lead_id == lead_id).order_by(Event.occurred_at.desc())
                               .limit(100))).scalars().all()
    return {"lead_id": str(lead.id), "status": lead.status,
            "stages": [{"n": i + 1, "name": STAGES[i], "state": s, "detail": d} for i, (s, d) in enumerate(stages)],
            "history": [_event_view(e) for e in events]}


class CancelWorkflowBody(BaseModel):
    reason: str = Field("Cancelled by a user", max_length=300)


@router.post("/workflow/leads/{lead_id}/cancel", summary="Cancel this prospect's workflow")
async def cancel_workflow(lead_id: uuid.UUID, body: CancelWorkflowBody, request: Request,
                          db: AsyncSession = Depends(get_db)) -> dict:
    from app.compliance.approvals import ApprovalError, reject
    from app.memory.records import complete_work, open_work_for_lead
    lead = await _get(db, Lead, lead_id, "lead")
    actor = _actor(request)
    cancelled_fu = await fu.cancel_for_lead(db, lead.id, f"Workflow cancelled by {actor}: {body.reason}")
    rejected = 0
    for a in (await db.execute(select(Approval).where(Approval.lead_id == lead.id, Approval.status == "pending"))
              ).scalars().all():
        try:
            await reject(db, a.id, reviewer=f"{actor} (workflow cancelled)", note=body.reason)
            rejected += 1
        except ApprovalError:
            pass
    for item in await open_work_for_lead(db, lead.id):
        await complete_work(db, item, "cancelled", force=True)
    pend = (await db.execute(select(Task).where(Task.lead_id == lead.id, Task.status == "pending"))).scalars().all()
    for t in pend:
        t.status, t.error_message = "cancelled", f"workflow cancelled by {actor}"
    lead.status = "closed_lost"
    db.add(Event(tenant_id=lead.tenant_id, lead_id=lead.id, event_type="workflow_cancelled", source="api",
                 payload=json.dumps({"by": actor, "reason": body.reason})))
    await db.commit()
    return {"follow_ups_cancelled": cancelled_fu, "approvals_rejected": rejected, "tasks_cancelled": len(pend)}


@router.get("/workflow/logs", summary="Workflow execution log (most recent events)")
async def workflow_logs(limit: int = Query(200, ge=1, le=1000), event_type: Optional[str] = None,
                        db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Event).order_by(Event.occurred_at.desc()).limit(limit)
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    return [_event_view(e) for e in (await db.execute(stmt)).scalars().all()]


def _event_view(e: Event) -> dict:
    try:
        payload = json.loads(e.payload) if e.payload else {}
    except ValueError:
        payload = {"raw": e.payload}
    return {"id": str(e.id), "type": e.event_type, "source": e.source, "lead_id": str(e.lead_id) if e.lead_id else None,
            "at": e.occurred_at, "payload": payload}
