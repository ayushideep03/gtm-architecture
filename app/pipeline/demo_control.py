"""
Demo Control Centre services (demo profile only).

- load / reset the demo dataset (two fictional organisations, users per role, target
  accounts, a campaign each);
- start the pipeline for the presenter's organisation;
- trigger prospect replies through the simulated inbox (the real inbound pipeline);
- run predefined scenarios end to end, step by step, through the real workflow engine.

Scenario approvals: a scenario only approves on the presenter's behalf if the presenter's
role may approve (admin / approver); otherwise it stops at the approval checkpoint and
says so. Every scenario approval is recorded with the presenter's name and a note.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import delete, func, select, text

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.tenancy import DEFAULT_TENANT_ID, tenant_scope
from app.pipeline import demo_data

logger = get_logger(__name__)

SCENARIOS = {
    "successful_conversion": {"title": "Successful conversion", "company": "northwind-analytics.example",
                              "summary": "First email approved and sent → prospect replies with interest → meeting "
                                         "proposed, approved and sent → prospect accepts → meeting booked."},
    "objection": {"title": "Prospect objection", "company": "quarry-labs.example",
                  "summary": "Prospect says they already use another tool → Oxygen drafts an answer from the "
                             "approved knowledge base → approved → sent."},
    "question": {"title": "Prospect asks a question", "company": "ledgerly.example",
                 "summary": "Prospect asks about CRM integration and setup time → grounded answer drafted → "
                            "approved → sent. (An unsupported question is escalated instead.)"},
    "unsupported_question": {"title": "Question Oxygen cannot answer", "company": "tessellate-ai.example",
                             "summary": "Prospect asks about something not in the knowledge base → Oxygen "
                                        "escalates to a person instead of inventing an answer."},
    "meeting_request": {"title": "Prospect requests a meeting", "company": "brightpath-hr.example",
                        "summary": "Prospect asks for times → meeting proposal approved → simulated invitation "
                                   "with three slots, ready to accept / reschedule in Meetings."},
    "no_reply": {"title": "Prospect does not reply", "company": "cobalt-payments.example",
                 "summary": "First email sent → demo clock advanced past the follow-up delay → follow-up "
                            "drafted, approved and sent → next step scheduled."},
    "unsubscribe": {"title": "Prospect unsubscribes", "company": "kestrel-security.example",
                    "summary": "Prospect asks to be removed → suppressed, consent withdrawn, follow-ups cancelled, "
                               "lead closed; nothing more is sent."},
}

# Tables cleared by a reset, children first. Only rows of the demo organisations are deleted.
RESET_ORDER = ["outcome_attributions", "approvals", "agent_decisions", "fact_evidence", "facts", "research_findings",
               "work_items", "follow_ups", "meetings", "drafts", "interactions", "events", "tasks", "contact_consents",
               "suppressions", "source_records", "campaign_accounts", "leads", "people", "companies", "campaigns",
               "demo_clock"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def org_id(key: str) -> uuid.UUID:
    return DEFAULT_TENANT_ID if key == "northstar" else demo_data.demo_uuid("org", key)


# ── Load / reset ──────────────────────────────────────────────────────────────

async def load() -> dict:
    """Idempotent: organisations, demo users, companies, campaign and target accounts per org."""
    from app.models.base import Organization
    from app.models.pipeline import AppUser, Campaign
    from app.pipeline import campaigns as camp
    summary = []
    for org in demo_data.ORGS:
        tid = org_id(org["key"])
        async with AsyncSessionLocal() as s:          # organisations table is not tenant-scoped
            o = await s.get(Organization, tid)
            if o is None:
                o = Organization(id=tid, name=org["name"], slug=org["slug"])
                s.add(o)
            else:
                o.name = org["name"]
            await s.commit()
        with tenant_scope(tid):
            async with AsyncSessionLocal() as s:
                for name, role in org["users"]:
                    uid = demo_data.demo_uuid("user", org["key"], role)
                    if await s.get(AppUser, uid) is None:
                        s.add(AppUser(id=uid, tenant_id=tid, name=name, role=role, is_demo=True,
                                      email=f"{name.split()[0].lower()}@{org['slug']}.example"))
                cdata = org["campaign"]
                cid = demo_data.demo_uuid("campaign", org["key"])
                c = await s.get(Campaign, cid)
                if c is None:
                    c = Campaign(id=cid, tenant_id=tid, name=cdata["name"], description=cdata["description"],
                                 status="draft", icp=camp.validate_icp(cdata["icp"]), offer=cdata["offer"],
                                 value_points=cdata["value_points"],
                                 sequence=camp.validate_sequence(cdata["sequence"]), created_by="demo dataset")
                    s.add(c)
                    await s.flush()
                rows = [{"name": co[0], "domain": co[1], "industry": co[2], "employees": co[3], "city": co[4],
                         "country": co[5], "technologies": co[6], "funding_stage": co[7],
                         "description": f"{demo_data.DEMO_LABEL}. Fictional company used for the Oxygen demo.",
                         "priority": "high" if i < 3 else "medium" if i < 8 else "low"}
                        for i, co in enumerate(c2 for c2 in demo_data.COMPANIES if c2[9] == org["key"])]
                accts = await camp.add_accounts(s, c, rows, "demo dataset")
                await s.commit()
                summary.append({"organisation": org["name"], "tenant_id": str(tid), "campaign": c.name,
                                "accounts": len(accts), "users": len(org["users"])})
    return {"loaded": summary}


async def reset() -> dict:
    """Delete everything belonging to the demo organisations, then load the dataset again."""
    deleted: dict[str, int] = {}
    for org in demo_data.ORGS:
        tid = org_id(org["key"])
        with tenant_scope(tid):
            async with AsyncSessionLocal() as s:
                for table in RESET_ORDER:
                    exists = (await s.execute(text(
                        "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = 'tenant_id'"),
                        {"t": table})).first()
                    if exists:
                        res = await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
                        deleted[table] = deleted.get(table, 0) + (res.rowcount or 0)
                await s.commit()
    # Redis runtime state (pause flag, health, rate limits) for the demo key prefix
    try:
        from app.core.gtm_config import get_gtm_config
        from app.core.redis import get_loop_redis
        r = get_loop_redis()
        prefix = get_gtm_config().runtime.key_prefix
        async for key in r.scan_iter(f"{prefix}:*"):
            await r.delete(key)
    except Exception:  # noqa: BLE001
        pass
    loaded = await load()
    return {"deleted": {k: v for k, v in deleted.items() if v}, **loaded}


# ── Presenter actions ─────────────────────────────────────────────────────────

async def tick_now(times: int = 1) -> list[dict]:
    """Run the autonomous loop now (in addition to the background worker), for this organisation."""
    from app.services.autonomy import tick
    out = []
    for _ in range(times):
        out.append(await tick(worker="demo-presenter"))
    return out


async def simulate_reply(lead_id: uuid.UUID, kind: str, body: Optional[str] = None) -> dict:
    from app.integrations.demo_providers import DemoInboxProvider
    from app.models.base import Lead, Person
    if kind not in demo_data.REPLY_TEMPLATES and not body:
        raise ValueError(f"unknown reply type {kind!r}")
    async with AsyncSessionLocal() as s:
        lead = await s.get(Lead, lead_id)
        if lead is None:
            raise ValueError("lead not found")
        person = await s.get(Person, lead.person_id) if lead.person_id else None
        if person is None or not person.email:
            raise ValueError("lead has no contact email")
        res = await DemoInboxProvider().inject(s, lead=lead, person=person, body=body or demo_data.REPLY_TEMPLATES[kind])
        await s.commit()
    return res.model_dump(mode="json")


# ── Scenarios ─────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, tenant_id, actor: str, role: str, auto_approve: bool):
        self.tenant_id, self.actor, self.role = tenant_id, actor, role
        self.can_approve = auto_approve and role in ("admin", "approver")
        self.log: list[dict] = []

    def step(self, title: str, ok: bool = True, detail: Any = None) -> bool:
        self.log.append({"at": _now().isoformat(), "step": title, "ok": ok, "detail": detail})
        return ok

    async def until(self, pred: Callable, ticks: int = 10) -> Any:
        for _ in range(ticks):
            v = await pred()
            if v:
                return v
            await tick_now()
        return await pred()

    async def lead_for(self, domain: str):
        from app.models.base import Company, Lead, Person
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(select(Lead).join(Person, Person.id == Lead.person_id)
                                    .join(Company, Company.id == Person.company_id)
                                    .where(Company.domain == domain).order_by(Lead.created_at))).scalars().all()
        for lead in rows:
            if lead.status not in ("disqualified",):
                return lead
        return rows[0] if rows else None

    async def pending(self, lead_id, capability: str):
        from app.models.memory import Approval
        async with AsyncSessionLocal() as s:
            return (await s.execute(select(Approval).where(Approval.lead_id == lead_id, Approval.status == "pending",
                                                           Approval.capability == capability))).scalars().first()

    async def approve(self, lead_id, capability: str) -> bool:
        from app.compliance import approvals
        a = await self.until(lambda: self.pending(lead_id, capability), ticks=25)
        if a is None:
            return self.step(f"Waiting for a {capability.replace('_', ' ')} proposal", False,
                             "Oxygen has not proposed it yet; run the worker a little longer")
        if not self.can_approve:
            return self.step(f"Approval checkpoint: {capability.replace('_', ' ')}", False,
                             f"Waiting for an approver in the Approval Centre (your role: {self.role})")
        async with AsyncSessionLocal() as s:
            await approvals.approve(s, a.id, reviewer=f"{self.actor}", note="approved during a demo scenario run")
            await s.commit()
        self.step(f"Approved {capability.replace('_', ' ')} (as {self.actor})", True, {"approval_id": str(a.id)})
        await tick_now()
        return True

    async def count(self, model, **where) -> int:
        async with AsyncSessionLocal() as s:
            stmt = select(func.count()).select_from(model)
            for k, v in where.items():
                stmt = stmt.where(getattr(model, k) == v)
            return (await s.execute(stmt)).scalar() or 0


async def run_scenario(key: str, *, tenant_id, actor: str, role: str, auto_approve: bool = True) -> dict:
    from app.models.base import Interaction, Lead
    from app.models.pipeline import Campaign, FollowUp, Meeting
    from app.pipeline import campaigns as camp, demo_clock, meetings as mt
    if key not in SCENARIOS:
        raise ValueError(f"unknown scenario {key!r}")
    spec = SCENARIOS[key]
    r = Runner(tenant_id, actor, role, auto_approve)
    r.step(f"Scenario: {spec['title']}", True, spec["summary"])

    # 1. the pipeline must be running for this organisation
    async with AsyncSessionLocal() as s:
        c = (await s.execute(select(Campaign).where(Campaign.status.in_(("draft", "active", "paused")))
                             .order_by(Campaign.created_at))).scalars().first()
        if c is None:
            r.step("No campaign to run", False, "Load the demo dataset first")
            return {"scenario": key, "log": r.log, "completed": False}
        if c.status != "active" or not (await s.execute(select(func.count(Lead.id)).where(Lead.campaign_id == c.id))).scalar():
            res = await camp.start(s, c, actor)
            await s.commit()
            r.step("Started the campaign pipeline (discovery)", True, res)
    lead = await r.lead_for(spec["company"])
    if lead is None:
        r.step("Target prospect not found", False, spec["company"])
        return {"scenario": key, "log": r.log, "completed": False}
    r.step(f"Prospect: lead {str(lead.id)[:8]} at {spec['company']}", True, {"lead_id": str(lead.id)})

    async def sent_count():
        return await r.count(Interaction, lead_id=lead.id, direction="outbound", interaction_type="email_sent")

    # 2. first email: research → qualify → draft → approval → send
    if await sent_count() == 0:
        if not await r.approve(lead.id, "send_email"):
            return {"scenario": key, "lead_id": str(lead.id), "log": r.log, "completed": False}
        ok = await r.until(sent_count, ticks=6)
        r.step("First email sent (simulated, nothing left the system)", bool(ok))

    async def lead_status():
        async with AsyncSessionLocal() as s:
            return (await s.get(Lead, lead.id)).status

    reply_kind = {"successful_conversion": "interested", "objection": "objection", "question": "question",
                  "unsupported_question": "unsupported_question", "meeting_request": "meeting_request",
                  "unsubscribe": "unsubscribe"}.get(key)
    if reply_kind:
        res = await simulate_reply(lead.id, reply_kind)
        r.step(f"Prospect replied ({reply_kind.replace('_', ' ')})", bool(res.get("matched")),
               {"classification": (res.get("classification") or {}).get("category"), "actions": res.get("actions")})
        await tick_now()

    if key in ("successful_conversion", "meeting_request"):
        if await r.approve(lead.id, "book_meeting"):
            async def invited():
                async with AsyncSessionLocal() as s:
                    return (await s.execute(select(Meeting).where(Meeting.lead_id == lead.id,
                                                                  Meeting.status == "invited"))).scalars().first()
            m = await r.until(invited, ticks=6)
            r.step("Simulated calendar invitation sent with proposed slots", m is not None,
                   {"slots": m.proposed_slots if m else None})
            if key == "successful_conversion" and m is not None:
                async with AsyncSessionLocal() as s:
                    meeting = await s.get(Meeting, m.id)
                    await mt.respond(s, meeting, "accept", slot_index=0, by="prospect (simulated)")
                    await s.commit()
                await tick_now()
                r.step("Prospect accepted the first slot → meeting booked", await lead_status() == "meeting_booked")
    elif key in ("objection", "question"):
        await r.approve(lead.id, "send_email")
        r.step("Knowledge-base answer sent (simulated)", await sent_count() >= 2)
    elif key == "unsupported_question":
        async def escalated():
            from app.models.pipeline import Draft
            async with AsyncSessionLocal() as s:
                return (await s.execute(select(Draft).where(Draft.lead_id == lead.id, Draft.kind == "reply",
                                                           Draft.status == "escalated"))).scalars().first()
        d = await r.until(escalated, ticks=6)
        r.step("Not answered: escalated to a person (question not in the knowledge base)", d is not None,
               d.explanation if d else None)
    elif key == "no_reply":
        async def next_follow_up():
            async with AsyncSessionLocal() as s:
                return (await s.execute(select(FollowUp).where(FollowUp.lead_id == lead.id,
                                                               FollowUp.status == "scheduled"))).scalars().first()
        f = await r.until(next_follow_up, ticks=4)
        if f is None:
            r.step("No follow-up scheduled", False)
        else:
            hours = max(1.0, (f.due_at - _now()).total_seconds() / 3600 + 1)
            async with AsyncSessionLocal() as s:
                res = await demo_clock.advance(s, tenant_id, hours)
                await s.commit()
            r.step(f"Demo clock advanced {hours:.0f}h (no reply)", True, {"total_offset_h": res["total_offset_hours"]})
            await tick_now(2)
            await r.approve(lead.id, "send_email")
            r.step("Follow-up step sent (simulated)", await sent_count() >= 2)
    elif key == "unsubscribe":
        cancelled = await r.count(FollowUp, lead_id=lead.id, status="cancelled")
        r.step("Suppressed, consent withdrawn, follow-ups cancelled, lead closed",
               await lead_status() == "closed_lost", {"follow_ups_cancelled": cancelled})

    completed = all(s["ok"] for s in r.log)
    async with AsyncSessionLocal() as s:
        from app.models.base import Event
        s.add(Event(tenant_id=tenant_id, lead_id=lead.id, event_type="demo_scenario_completed" if completed
                    else "demo_scenario_paused", source="demo",
                    payload=json.dumps({"scenario": key, "by": actor, "steps": len(r.log)})))
        await s.commit()
    return {"scenario": key, "title": spec["title"], "lead_id": str(lead.id), "completed": completed, "log": r.log}
