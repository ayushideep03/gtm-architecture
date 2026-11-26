"""
Campaign management and lead discovery (stages 1-3).

A campaign holds the ICP, the offer, the follow-up sequence and a set of selected target
accounts. Starting it runs lead discovery for every selected account through the
``find_leads`` provider (demo: DemoApolloProvider; live: ApolloProvider — same
``LeadSourceProvider.search_leads`` interface), ingests the contacts with identity
resolution, attaches the leads to the campaign, and the autonomous loop takes over from
``lead_ingested``. Pausing holds the campaign's leads; stopping ends their outreach.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.base import Company, Event, Lead, Person
from app.models.pipeline import Campaign, CampaignAccount

logger = get_logger(__name__)
PRIORITIES = ("high", "medium", "low")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(db, tenant_id, event_type: str, payload: dict, lead_id=None) -> None:
    db.add(Event(tenant_id=tenant_id, lead_id=lead_id, event_type=event_type, source="campaigns",
                 payload=json.dumps(payload, default=str)))


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

def validate_sequence(seq: list[dict]) -> list[dict]:
    out = []
    for i, s in enumerate(seq or [], start=1):
        delay = float(s.get("delay_days", 0 if i == 1 else 3))
        if delay < 0 or delay > 90:
            raise ValueError("delay_days must be between 0 and 90")
        out.append({"step": i, "delay_days": delay, "angle": (s.get("angle") or "").strip()[:200] or None})
    if not out:
        raise ValueError("a campaign needs at least one sequence step")
    if len(out) > 6:
        raise ValueError("at most 6 steps")
    return out


def validate_icp(icp: dict) -> dict:
    lists = ("industries", "countries", "technologies", "titles", "departments")
    clean: dict[str, Any] = {k: [str(x).strip() for x in (icp.get(k) or []) if str(x).strip()][:30] for k in lists}
    for k in ("min_employees", "max_employees"):
        v = icp.get(k)
        clean[k] = int(v) if v not in (None, "") else None
    if clean["min_employees"] and clean["max_employees"] and clean["min_employees"] > clean["max_employees"]:
        raise ValueError("min_employees cannot exceed max_employees")
    clean["min_score"] = max(0, min(100, int(icp.get("min_score", 60))))
    if icp.get("weights"):
        clean["weights"] = {k: max(0, min(100, int(v))) for k, v in icp["weights"].items()}
    return clean


async def create(db: AsyncSession, data: dict, actor: str) -> Campaign:
    c = Campaign(name=data["name"].strip()[:255], description=data.get("description"), status="draft",
                 icp=validate_icp(data.get("icp") or {}), offer=data.get("offer"),
                 value_points=data.get("value_points") or [],
                 sequence=validate_sequence(data.get("sequence") or [{"delay_days": 0}, {"delay_days": 3}]),
                 created_by=actor)
    db.add(c)
    await db.flush()
    _event(db, c.tenant_id, "campaign_created", {"campaign_id": str(c.id), "name": c.name, "by": actor})
    return c


async def update_campaign(db: AsyncSession, c: Campaign, data: dict, actor: str) -> Campaign:
    if c.status == "stopped":
        raise ValueError("a stopped campaign cannot be edited")
    for k in ("name", "description", "offer", "value_points"):
        if k in data and data[k] is not None:
            setattr(c, k, data[k])
    if data.get("icp") is not None:
        c.icp = validate_icp(data["icp"])
    if data.get("sequence") is not None:
        c.sequence = validate_sequence(data["sequence"])
    _event(db, c.tenant_id, "campaign_updated", {"campaign_id": str(c.id), "by": actor,
                                                 "fields": sorted(k for k in data if data[k] is not None)})
    await db.flush()
    return c


async def set_status(db: AsyncSession, c: Campaign, status: str, actor: str) -> dict:
    """active | paused | stopped, with their effects on the campaign's leads."""
    allowed = {"draft": {"active", "stopped"}, "active": {"paused", "stopped"}, "paused": {"active", "stopped"},
               "stopped": set()}
    if status not in allowed[c.status]:
        raise ValueError(f"cannot change a {c.status} campaign to {status}")
    effects: dict[str, Any] = {}
    previous, c.status = c.status, status
    if status == "active" and c.started_at is None:
        c.started_at = _now()
    if status == "active" and previous == "paused":
        # wake the campaign's leads so nothing waits for its old schedule
        from app.memory.records import schedule_work
        leads = (await db.execute(select(Lead).where(Lead.campaign_id == c.id, Lead.is_open.is_(True)))).scalars().all()
        for lead in leads:
            await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate",
                                reason="Campaign resumed", created_by="campaigns")
        effects["leads_woken"] = len(leads)
    if status == "stopped":
        effects.update(await _stop_effects(db, c, actor))
    _event(db, c.tenant_id, f"campaign_{'resumed' if previous == 'paused' and status == 'active' else status}",
           {"campaign_id": str(c.id), "by": actor, "previous": previous, **effects})
    await db.flush()
    return effects


async def _stop_effects(db: AsyncSession, c: Campaign, actor: str) -> dict:
    from app.compliance.approvals import ApprovalError, reject
    from app.memory.records import complete_work, open_work_for_lead
    from app.models.memory import Approval
    from app.models.pipeline import FollowUp
    lead_ids = (await db.execute(select(Lead.id).where(Lead.campaign_id == c.id))).scalars().all()
    if not lead_ids:
        return {"follow_ups_cancelled": 0, "approvals_rejected": 0}
    res = await db.execute(update(FollowUp).where(FollowUp.lead_id.in_(lead_ids),
                                                  FollowUp.status.in_(("scheduled", "due", "drafted")))
                           .values(status="cancelled", reason="Campaign stopped", updated_at=_now()))
    pending = (await db.execute(select(Approval).where(Approval.lead_id.in_(lead_ids),
                                                       Approval.status == "pending"))).scalars().all()
    rejected = 0
    for a in pending:
        try:
            await reject(db, a.id, reviewer=f"{actor} (campaign stopped)", note="campaign stopped")
            rejected += 1
        except ApprovalError:
            pass
    for lid in lead_ids:
        for item in await open_work_for_lead(db, lid):
            await complete_work(db, item, "campaign_stopped", force=True)
    return {"follow_ups_cancelled": res.rowcount or 0, "approvals_rejected": rejected}


# ── Target accounts ───────────────────────────────────────────────────────────

async def _company(db: AsyncSession, data: dict) -> Company:
    domain = (data.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if not domain or "." not in domain:
        raise ValueError(f"a valid domain is required (got {data.get('domain')!r})")
    co = (await db.execute(select(Company).where(Company.domain == domain))).scalars().first()
    techs = data.get("technologies")
    if isinstance(techs, str):
        techs = [t.strip() for t in techs.replace(";", ",").split(",") if t.strip()]
    if co is None:
        co = Company(name=(data.get("name") or domain).strip()[:255], domain=domain)
        db.add(co)
    for field, value in (("industry", data.get("industry")), ("country", data.get("country")),
                         ("city", data.get("city")), ("funding_stage", data.get("funding_stage")),
                         ("description", data.get("description"))):
        if value and not getattr(co, field):
            setattr(co, field, value)
    if data.get("employees") not in (None, "") and co.employee_count is None:
        co.employee_count = int(data["employees"])
    if techs and not co.technologies:
        co.technologies = json.dumps(techs)
    if not co.website:
        co.website = f"https://{domain}"
    if data.get("enrichment_source") and co.enriched_at is None:   # firmographics supplied by a provider/dataset
        co.enriched_at, co.enrichment_source = _now(), data["enrichment_source"]
    await db.flush()
    return co


async def add_accounts(db: AsyncSession, c: Campaign, rows: list[dict], actor: str) -> list[CampaignAccount]:
    if c.status == "stopped":
        raise ValueError("cannot add accounts to a stopped campaign")
    out = []
    for row in rows:
        co = await _company(db, row)
        acct = (await db.execute(select(CampaignAccount).where(CampaignAccount.campaign_id == c.id,
                                                              CampaignAccount.company_id == co.id))).scalar_one_or_none()
        if acct is None:
            acct = CampaignAccount(campaign_id=c.id, company_id=co.id, added_by=actor)
            db.add(acct)
        pr = (row.get("priority") or "medium").lower()
        acct.priority = pr if pr in PRIORITIES else "medium"
        acct.selected = True
        out.append(acct)
    await db.flush()
    _event(db, c.tenant_id, "accounts_selected", {"campaign_id": str(c.id), "count": len(out), "by": actor})
    return out


def parse_csv(text: str) -> list[dict]:
    """Columns (header row, case-insensitive): name, domain, industry, employees, city, country,
    technologies (separated by ';'), priority."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames or "domain" not in [f.strip().lower() for f in reader.fieldnames]:
        raise ValueError("CSV needs a header row with at least a 'domain' column")
    rows = []
    for raw in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if r.get("domain"):
            rows.append(r)
        if len(rows) >= 500:
            break
    return rows


async def update_account(db: AsyncSession, acct: CampaignAccount, *, priority: Optional[str] = None,
                         selected: Optional[bool] = None) -> CampaignAccount:
    if priority is not None:
        if priority not in PRIORITIES:
            raise ValueError("priority must be high, medium or low")
        acct.priority = priority
    if selected is not None:
        acct.selected = selected
    await db.flush()
    return acct


# ── Discovery ─────────────────────────────────────────────────────────────────

def _lead_source():
    """The best available find_leads provider (demo or live: same interface)."""
    from app.providers import get_provider_registry
    cands = [r for r in get_provider_registry().candidates("find_leads") if r.available and r.config.enabled]
    if not cands:
        raise ValueError("no lead discovery provider is available")
    best = max(cands, key=lambda r: r.config.priority)
    return best.instance, best.name


async def discover(db: AsyncSession, c: Campaign, actor: str, account_ids: Optional[list[uuid.UUID]] = None) -> dict:
    from app.integrations.lead_source_record import LeadSearchCriteria
    from app.services.lead_ingestion import LeadIngestionService
    provider, pname = _lead_source()
    stmt = select(CampaignAccount, Company).join(Company, Company.id == CampaignAccount.company_id).where(
        CampaignAccount.campaign_id == c.id, CampaignAccount.selected.is_(True))
    if account_ids:
        stmt = stmt.where(CampaignAccount.id.in_(account_ids))
    else:
        stmt = stmt.where(CampaignAccount.discovery_status.in_(("pending", "failed")))
    rows = (await db.execute(stmt)).all()
    total = {"accounts": 0, "contacts": 0, "leads_created": 0, "provider": pname}
    service = LeadIngestionService()
    for acct, co in rows:
        acct.discovery_status = "discovering"
        try:
            records = await provider.search_leads(LeadSearchCriteria(company_domain=co.domain, company_name=co.name,
                                                                     limit=10))
            summary = await service.ingest_batch(records, db, pname)
            attached = 0
            for rec in records:
                person = (await db.execute(select(Person).where(func.lower(Person.email) == (rec.email or "").lower())
                                           )).scalars().first()
                if person is None:
                    continue
                raw = rec.raw_data or {}
                person.department = person.department or raw.get("department")
                person.seniority = person.seniority or raw.get("seniority")
                person.location = person.location or raw.get("location")
                person.verification_status = raw.get("verification_status") or person.verification_status
                person.enrichment_source = person.enrichment_source or pname
                lead = (await db.execute(select(Lead).where(Lead.person_id == person.id, Lead.is_open.is_(True))
                                         )).scalars().first()
                if lead is not None and lead.campaign_id is None:
                    lead.campaign_id = c.id
                    attached += 1
            acct.discovery_status, acct.discovery_source = "discovered", pname
            acct.prospects_found, acct.discovered_at = len(records), _now()
            total["accounts"] += 1
            total["contacts"] += len(records)
            total["leads_created"] += summary.leads_created
            _event(db, c.tenant_id, "account_discovered", {"campaign_id": str(c.id), "company": co.name,
                                                           "contacts": len(records), "attached": attached,
                                                           "provider": pname})
        except Exception as exc:  # noqa: BLE001 - one account's failure must not stop discovery
            logger.exception("Discovery failed for %s", co.domain)
            acct.discovery_status = "failed"
            _event(db, c.tenant_id, "account_discovery_failed", {"campaign_id": str(c.id), "company": co.name,
                                                                 "error": str(exc)[:200]})
    await db.flush()
    return total


async def start(db: AsyncSession, c: Campaign, actor: str) -> dict:
    """Start (or continue) the pipeline: activate, then discover leads for every selected account."""
    if c.status in ("draft", "paused"):
        await set_status(db, c, "active", actor)
    elif c.status == "stopped":
        raise ValueError("a stopped campaign cannot be started")
    found = await discover(db, c, actor)
    return {"status": c.status, **found}


# ── Views ─────────────────────────────────────────────────────────────────────

def view(c: Campaign) -> dict:
    return {"id": str(c.id), "name": c.name, "description": c.description, "status": c.status, "icp": c.icp,
            "offer": c.offer, "value_points": c.value_points, "sequence": c.sequence, "created_by": c.created_by,
            "started_at": c.started_at, "created_at": c.created_at, "updated_at": c.updated_at}


def account_view(a: CampaignAccount, co: Company) -> dict:
    techs: Any = co.technologies
    try:
        techs = json.loads(techs) if techs else []
    except ValueError:
        techs = [t.strip() for t in str(techs).split(",") if t.strip()]
    return {"id": str(a.id), "campaign_id": str(a.campaign_id), "company_id": str(co.id), "name": co.name,
            "domain": co.domain, "website": co.website, "industry": co.industry, "employees": co.employee_count,
            "city": co.city, "country": co.country, "technologies": techs, "funding_stage": co.funding_stage,
            "priority": a.priority, "selected": a.selected, "discovery_status": a.discovery_status,
            "discovery_source": a.discovery_source, "prospects_found": a.prospects_found,
            "discovered_at": a.discovered_at, "demo": (co.domain or "").endswith(".example")}
