"""
Demo scenarios — four clearly labelled SAMPLE prospects (fictional people and
companies on reserved ``.example`` domains) and a reply simulator.

    1. High intent        → research/intent/qualify → draft → approval → send →
                            positive reply → meeting invite → accepted
    2. Insufficient info  → enrichment finds nothing → Oxygen escalates instead of guessing
    3. No response        → first email went out 4 days ago → follow-up → then stops
    4. Negative response  → contacted yesterday → "remove me" reply → suppressed → stops

Only available in non-production profiles (development / demo / test).
Simulated replies go through the real inbound pipeline (opt-out rules,
classification, suppression, reactions) exactly like an IMAP or webhook reply.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.rules import record_consent
from app.core.gtm_config import get_gtm_config
from app.core.tenancy import DEFAULT_TENANT_ID
from app.memory.records import record_finding
from app.models.base import Company, Event, Interaction, Lead, Person, Task

DEMO_PROFILES = {"development", "demo", "test"}

SCENARIOS: list[dict[str, Any]] = [
    {
        "key": "high_intent", "title": "High intent",
        "story": "Hiring SDRs and expanding into Europe; VP Sales. Expect: research → qualify → draft → approval → send; "
                 "simulate a positive reply → meeting invite → accept.",
        "company": {"name": "Northwind Analytics", "domain": "northwind-analytics.example", "industry": "Software",
                    "employee_count": 240, "country": "United Kingdom",
                    "description": "Revenue analytics platform for mid-market B2B teams."},
        "person": {"first_name": "Priya", "last_name": "Shah", "title": "VP Sales",
                   "email": "priya.shah@northwind-analytics.example"},
        "findings": [
            ("intent_signal", "Northwind Analytics is hiring six SDRs for a new outbound team in London."),
            ("news", "Northwind Analytics announced its expansion into Germany and the Nordics this quarter."),
        ],
    },
    {
        "key": "insufficient_info", "title": "Insufficient information",
        "story": "Only a name and an email; no industry, size or role can be verified. Expect: enrichment attempt, "
                 "then escalation to a human instead of a guess.",
        "company": {"name": "Bluepeak", "domain": "bluepeak.example"},
        "person": {"first_name": "Sam", "last_name": "Taylor", "email": "sam@bluepeak.example"},
        "findings": [],
    },
    {
        "key": "no_response", "title": "No response",
        "story": "First email went out 4 days ago with no reply. Expect: a follow-up that adds something new "
                 "(needs approval), then Oxygen stops after the sequence.",
        "company": {"name": "Ledgerly", "domain": "ledgerly.example", "industry": "Software", "employee_count": 120,
                    "country": "Ireland", "description": "Accounts-payable automation for SMEs."},
        "person": {"first_name": "Marco", "last_name": "Rossi", "title": "Head of Revenue Operations",
                   "email": "marco.rossi@ledgerly.example"},
        "findings": [("intent_signal", "Ledgerly is consolidating its sales tooling ahead of a Series B.")],
        "contacted_days_ago": 4,
    },
    {
        "key": "negative_response", "title": "Negative response",
        "story": "Contacted yesterday. Simulate the reply 'Please remove me from your list' → deterministic opt-out "
                 "→ suppression → Oxygen stops.",
        "company": {"name": "Quarry Labs", "domain": "quarry-labs.example", "industry": "Software",
                    "employee_count": 410, "country": "Netherlands",
                    "description": "Developer platform for data pipelines."},
        "person": {"first_name": "Elena", "last_name": "Novak", "title": "Chief Revenue Officer",
                   "email": "elena.novak@quarry-labs.example"},
        "findings": [("news", "Quarry Labs opened a new sales office in Amsterdam.")],
        "contacted_days_ago": 1,
        "suggested_reply": "Please remove me from your list. We are not interested.",
    },
]

SUGGESTED_REPLIES = {
    "positive": "Thanks Alex, this is timely. We are building the SDR team now. Happy to talk, can you send times?",
    "negative": "Please remove me from your list. We are not interested.",
    "not_now": "Not right now, we are mid-budget cycle. Circle back next quarter.",
    "question": "How does the approval step work with our existing CRM?",
}


def demo_allowed() -> bool:
    return get_gtm_config().profile in DEMO_PROFILES


async def seed_scenarios(db: AsyncSession) -> list[dict]:
    """Create the sample prospects (idempotent by email). Returns lead ids by scenario."""
    out = []
    now = datetime.now(timezone.utc)
    for sc in SCENARIOS:
        existing = (await db.execute(select(Lead).join(Person, Person.id == Lead.person_id).where(
            Person.email == sc["person"]["email"], Lead.is_open.is_(True)))).scalar_one_or_none()
        if existing is not None:
            out.append({"scenario": sc["key"], "lead_id": str(existing.id), "created": False})
            continue
        company = (await db.execute(select(Company).where(Company.domain == sc["company"]["domain"]))).scalar_one_or_none()
        if company is None:
            company = Company(**sc["company"])
            db.add(company)
            await db.flush()
        person = Person(company_id=company.id, **sc["person"])
        db.add(person)
        await db.flush()
        lead = Lead(person_id=person.id, status="new", source="sample_data",
                    notes=f"[SAMPLE — {sc['title']}] {sc['story']}")
        db.add(lead)
        await db.flush()
        await record_consent(db, tenant_id=DEFAULT_TENANT_ID, person_id=person.id, basis="legitimate_interest",
                             source="sample_data", evidence="Fictional demo prospect")
        for kind, summary in sc["findings"]:
            await record_finding(db, tenant_id=DEFAULT_TENANT_ID, lead_id=lead.id, entity_type="company",
                                 entity_id=company.id, kind=kind, summary=summary, confidence=0.7,
                                 source="sample_data", citations=[{"note": "sample data for the demo"}])
        if sc.get("contacted_days_ago"):
            await _seed_first_touch(db, lead, person, company, now - timedelta(days=sc["contacted_days_ago"]))
        db.add(Event(lead_id=lead.id, event_type="lead_ingested", source="sample_data",
                     payload=json.dumps({"lead_id": str(lead.id), "scenario": sc["key"]})))
        await db.flush()
        out.append({"scenario": sc["key"], "lead_id": str(lead.id), "created": True})
    return out


async def _seed_first_touch(db, lead: Lead, person: Person, company: Company, when: datetime) -> None:
    subject = f"{company.name} and outbound research"
    body = (f"Hi {person.first_name},\n\nI noticed {company.name}'s recent news. We help sales teams research accounts "
            f"and prepare personalised outreach a person approves before sending.\n\nWorth a 20-minute call?\n\nAlex")
    draft = Task(lead_id=lead.id, task_type="draft_outreach", status="completed", created_at=when, completed_at=when,
                 payload=json.dumps({"channel": "email"}),
                 result=json.dumps({"channel": "email", "subject": subject, "body": body, "is_draft": True}))
    db.add(draft)
    await db.flush()
    send = Task(lead_id=lead.id, task_type="send_email", status="completed", created_at=when, completed_at=when,
                payload=json.dumps({"draft_task_id": str(draft.id), "subject": subject, "body": body}),
                result=json.dumps({"sample": True, "draft_task_id": str(draft.id)}))
    db.add(send)
    await db.flush()
    db.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound", subject=subject,
                       body=body, channel_message_id=f"<oxygen-{send.id}@demo.oxygen>", occurred_at=when,
                       task_id=send.id, channel="email", provider="sample_data", details={"sample": True}))
    lead.status = "contacted"
    await db.flush()


async def simulate_reply(db: AsyncSession, lead_id: uuid.UUID, body: str, kind: str = "reply") -> dict:
    """Feed a prospect reply (or a calendar acceptance) through the real inbound pipeline."""
    from app.events.inbound import CalendarEvent, EmailEvent, process_calendar_event, process_email_event
    lead = await db.get(Lead, lead_id)
    person = await db.get(Person, lead.person_id) if lead and lead.person_id else None
    if person is None or not person.email:
        raise ValueError("lead has no contact email")
    if kind == "accept":
        invite = (await db.execute(select(Interaction).where(
            Interaction.lead_id == lead.id, Interaction.channel == "meeting", Interaction.direction == "outbound")
            .order_by(Interaction.occurred_at.desc()).limit(1))).scalar_one_or_none()
        meeting_id = invite.channel_message_id if invite and invite.channel_message_id else f"demo-{uuid.uuid4().hex[:8]}"
        res = await process_calendar_event(db, CalendarEvent(
            type="booked", provider="demo_simulator", meeting_id=meeting_id, attendee_email=person.email,
            title="Discovery call (accepted)", event_id=uuid.uuid4().hex))
    else:
        last = (await db.execute(select(Interaction).where(
            Interaction.lead_id == lead.id, Interaction.direction == "outbound", Interaction.channel == "email")
            .order_by(Interaction.occurred_at.desc()).limit(1))).scalar_one_or_none()
        res = await process_email_event(db, EmailEvent(
            type="reply", provider="demo_simulator", from_email=person.email, body=body,
            subject=f"Re: {last.subject}" if last and last.subject else "Re: your email",
            in_reply_to=last.channel_message_id if last else None, event_id=uuid.uuid4().hex))
    await db.commit()
    return res.model_dump(mode="json")


def scenario_catalogue() -> list[dict]:
    return [{k: sc.get(k) for k in ("key", "title", "story", "suggested_reply")} | {
        "prospect": f"{sc['person']['first_name']} {sc['person']['last_name']}", "company": sc["company"]["name"]}
        for sc in SCENARIOS]
