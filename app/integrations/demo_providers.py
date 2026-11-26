"""
Demo-mode providers. Each implements the SAME interface as its live counterpart, so
the workflow engine cannot tell them apart and switching to the live provider is a
configuration change:

    DemoApolloProvider      (LeadSourceProvider.search_leads)  ->  ApolloProvider
    DemoResearchProvider    (research_prospect)                 ->  LLMResearchProvider (website + LLM)
    DemoCalendarProvider    (book_meeting)                      ->  SmtpMeetingProvider / Google Calendar / Calendly
    DemoInboxProvider       (receive_email: poll + inject)      ->  ImapInboxProvider
    (send_email in demo mode: MockOutreachSendProvider, mode dry_run -> SmtpEmailProvider)
    (LLM calls in demo mode:  ScriptedDemoClient vendor "demo"      -> OpenAI / Anthropic clients)

Nothing here performs network I/O. Every record they produce is labelled simulated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.integrations.lead_source import LeadSourceProvider
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord
from app.pipeline import demo_data


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _company_tuple(domain: str, name: Optional[str] = None, industry: Optional[str] = None,
                   employees: Optional[int] = None, city: str = "", country: str = "") -> tuple:
    for c in demo_data.COMPANIES:
        if c[1] == domain:
            return c
    return (name or domain.split(".")[0].replace("-", " ").title(), domain, industry or "Software",
            employees or 150, city or "London", country or "United Kingdom", [], "Private",
            "a new growth initiative", None)


# ── Lead discovery ────────────────────────────────────────────────────────────

class DemoApolloProvider(LeadSourceProvider):
    """Simulated Apollo people search: deterministic fictional contacts for a company domain."""

    def __init__(self, name: str = "demo_apollo", **_: Any) -> None:
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    async def search_leads(self, criteria: LeadSearchCriteria) -> list[LeadSourceRecord]:
        if not criteria.company_domain:
            return []
        company = _company_tuple(criteria.company_domain.lower().strip(), criteria.company_name)
        people = demo_data.contacts_for(company)
        titles = [t.lower() for t in (criteria.job_titles or [])]
        if titles:
            people = [p for p in people if any(t in p["title"].lower() for t in titles)] or people
        return [LeadSourceRecord(
            source=self._name, external_id=p["external_id"], first_name=p["first_name"], last_name=p["last_name"],
            email=p["email"], job_title=p["title"], company_name=company[0], company_domain=company[1],
            company_website=f"https://{company[1]}", industry=company[2], employee_count=company[3],
            location=p["location"],
            raw_data={**p, "demo": True, "label": demo_data.DEMO_LABEL, "simulated_by": self._name},
        ) for p in people[: criteria.limit]]


# ── Research ──────────────────────────────────────────────────────────────────

class DemoResearchProvider(ExecutionProvider):
    """Simulated research signals (news, hiring, technology, expansion, funding, ...), every one labelled."""

    def __init__(self, name: str = "demo_research", **_: Any) -> None:
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return ["research_prospect"]

    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult:
        from app.memory.records import record_finding
        from app.models.base import Company, Lead, Person
        if session is None or context.lead_id is None:
            return ExecutionResult(success=False, status="failed", provider=self._name, error_code="BAD_INPUT",
                                   error_message="research needs a lead and a session")
        lead = await session.get(Lead, context.lead_id)
        person = await session.get(Person, lead.person_id) if lead and lead.person_id else None
        company = await session.get(Company, person.company_id) if person and person.company_id else None
        if company is None or not company.domain:
            return ExecutionResult(success=False, status="failed", provider=self._name, error_code="NO_COMPANY",
                                   error_message="no company domain to research")
        tup = _company_tuple(company.domain, company.name, company.industry, company.employee_count,
                             company.city or "", company.country or "")
        ids = []
        for sig in demo_data.signals_for(tup, "this campaign"):
            f = await record_finding(
                session, tenant_id=lead.tenant_id, lead_id=lead.id, entity_type="company", entity_id=company.id,
                kind=sig["type"], summary=sig["statement"], body=sig,
                confidence={"strong": 0.8, "moderate": 0.6, "weak": 0.4}[sig["strength"]],
                source=f"{self._name} (simulated)", source_task_id=context.task_id,
                citations=[{"source": sig["source"], "url": sig["source_ref"], "quote": sig["statement"],
                            "simulated": True}])
            ids.append(str(f.id))
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name, result={
            "signals": len(ids), "finding_ids": ids, "simulated": True,
            "note": "Simulated research signals for the demo; not verified real-world facts."})


# ── Calendar ──────────────────────────────────────────────────────────────────

DEFAULT_AVAILABILITY = {"timezone": "Europe/London", "weekdays": [0, 1, 2, 3, 4], "hours": [10, 14, 16],
                        "min_notice_hours": 24}


def demo_slots(availability: Optional[dict] = None, count: int = 3, now: Optional[datetime] = None) -> list[datetime]:
    """The next free slots in the demo calendar (business days, fixed hours, minimum notice)."""
    a = {**DEFAULT_AVAILABILITY, **(availability or {})}
    tz = ZoneInfo(a["timezone"])
    cursor = (now or _now()).astimezone(tz) + timedelta(hours=a["min_notice_hours"])
    slots: list[datetime] = []
    day = cursor.replace(minute=0, second=0, microsecond=0)
    for _ in range(30):
        if day.weekday() in a["weekdays"]:
            for h in a["hours"]:
                s = day.replace(hour=h)
                if s >= cursor and len(slots) < count:
                    slots.append(s.astimezone(timezone.utc))
        if len(slots) >= count:
            break
        day = (day + timedelta(days=1)).replace(hour=0)
    return slots


class DemoCalendarProvider(ExecutionProvider):
    """Simulated calendar (Google Calendar or Calendly flavour): proposes slots and 'sends' an invitation.
    Nothing leaves the system; acceptance is triggered by the presenter (or, live, arrives via the inbox)."""

    def __init__(self, name: str = "demo_google_calendar", flavour: str = "google_calendar",
                 availability: Optional[dict] = None, **_: Any) -> None:
        self._name, self.flavour, self.availability = name, flavour, availability or {}

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return ["book_meeting"]

    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult:
        from app.models.base import Interaction, Lead, Person
        from app.pipeline.meetings import invite_meeting
        if session is None or context.lead_id is None:
            return ExecutionResult(success=False, status="failed", provider=self._name, error_code="BAD_INPUT",
                                   error_message="book_meeting needs a lead and a session")
        lead = await session.get(Lead, context.lead_id)
        person = await session.get(Person, lead.person_id) if lead and lead.person_id else None
        if person is None or not person.email:
            return ExecutionResult(success=False, status="failed", provider=self._name,
                                   error_code="MISSING_RECIPIENT", error_message="no contact email")
        slots = demo_slots(self.availability)
        meeting = await invite_meeting(session, lead, person, slots=slots, provider=self._name,
                                       flavour=self.flavour, topic=context.payload.get("topic") or "Discovery call",
                                       duration=int(context.payload.get("duration_minutes") or 30),
                                       task_id=context.task_id)
        session.add(Interaction(lead_id=lead.id, interaction_type="meeting", direction="outbound",
                                subject=f"Invitation sent (simulated): {meeting.title}",
                                body="Proposed: " + ", ".join(s.isoformat() for s in slots),
                                channel_message_id=meeting.uid, occurred_at=_now(), task_id=context.task_id,
                                channel="meeting", provider=self._name,
                                details={"mode": "simulation", "meeting_id": str(meeting.id)}))
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name, result={
            "invited": True, "mode": "simulation", "simulated": True, "meeting_uid": meeting.uid,
            "meeting_id": str(meeting.id), "proposed_slots": [s.isoformat() for s in slots],
            "start": slots[0].isoformat() if slots else None, "interaction_recorded": True})


# ── Inbox ─────────────────────────────────────────────────────────────────────

class DemoInboxProvider(ExecutionProvider):
    """Simulated inbox. ``poll`` reads nothing (there is no mailbox); ``inject`` delivers a presenter-triggered
    reply through the SAME inbound pipeline a real IMAP message goes through (matching, classification,
    suppression, follow-up cancellation, next action)."""

    def __init__(self, name: str = "demo_inbox", **_: Any) -> None:
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return ["receive_email"]

    async def execute(self, context, session=None) -> ExecutionResult:  # not used as a task
        return ExecutionResult(success=True, status="completed", provider=self._name, result={"messages": 0})

    async def poll(self, session) -> dict:
        return {"provider": self._name, "messages": 0, "events": 0, "simulated": True}

    async def inject(self, session, *, lead, person, body: str, subject: Optional[str] = None):
        from sqlalchemy import select
        from app.events.inbound import EmailEvent, process_email_event
        from app.models.base import Interaction
        last_out = (await session.execute(
            select(Interaction).where(Interaction.lead_id == lead.id, Interaction.direction == "outbound",
                                      Interaction.interaction_type == "email_sent")
            .order_by(Interaction.occurred_at.desc()).limit(1))).scalar_one_or_none()
        ev = EmailEvent(type="reply", provider=self._name, from_email=person.email,
                        in_reply_to=last_out.channel_message_id if last_out else None,
                        subject=subject or (f"Re: {last_out.subject}" if last_out and last_out.subject else "Re:"),
                        body=body, event_id=f"demo-{uuid.uuid4().hex}", message_id=f"<demo-{uuid.uuid4().hex}@demo>")
        return await process_email_event(session, ev, tenant_id=lead.tenant_id)
