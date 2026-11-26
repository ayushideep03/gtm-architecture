"""
Meeting management.

    requested ──(Oxygen proposes book_meeting)──► pending_approval ──(approved; calendar
    provider sends invite)──► invited ──(prospect accepts a slot)──► booked
                                                 ├──(prospect declines)──► declined
                                                 └──(prospect asks for another time)──► reschedule_requested
                                                         └──(new proposal, approved)──► invited

Prospect responses arrive through ``process_calendar_event`` (live: an iMIP reply read
from the inbox; demo: the presenter's Accept / Decline / Reschedule buttons), which is
the same path for both.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Meeting

OPEN = ("requested", "pending_approval", "invited", "reschedule_requested")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(m: Meeting, status: str, by: str, note: str = "") -> None:
    m.history = [*(m.history or []), {"at": _now().isoformat(), "status": status, "by": by, "note": note}]
    m.status = status


async def open_meeting(db: AsyncSession, lead_id: uuid.UUID) -> Optional[Meeting]:
    return (await db.execute(select(Meeting).where(Meeting.lead_id == lead_id, Meeting.status.in_(OPEN))
                             .order_by(Meeting.created_at.desc()).limit(1))).scalar_one_or_none()


async def request_meeting(db: AsyncSession, lead, *, by: str, note: str) -> Meeting:
    """The prospect asked to meet (or showed interest): record the request once."""
    m = await open_meeting(db, lead.id)
    if m is not None:
        return m
    m = Meeting(tenant_id=lead.tenant_id, lead_id=lead.id, campaign_id=getattr(lead, "campaign_id", None),
                uid=f"req-{uuid.uuid4().hex[:12]}", title="Discovery call", status="requested", history=[])
    _log(m, "requested", by, note)
    db.add(m)
    await db.flush()
    return m


async def mark_pending_approval(db: AsyncSession, lead, task_id) -> None:
    m = await open_meeting(db, lead.id) or await request_meeting(db, lead, by="oxygen", note="proposed by Oxygen")
    m.task_id = task_id
    _log(m, "pending_approval", "oxygen", "meeting proposal waiting for human approval")
    await db.flush()


async def invite_meeting(db: AsyncSession, lead, person, *, slots: list[datetime], provider: str, flavour: str,
                         topic: str, duration: int, task_id) -> Meeting:
    from app.pipeline.demo_data import DEMO_LABEL
    m = await open_meeting(db, lead.id)
    if m is None:
        m = Meeting(tenant_id=lead.tenant_id, lead_id=lead.id, campaign_id=getattr(lead, "campaign_id", None),
                    uid="", history=[])
        db.add(m)
    m.uid = f"oxygen-{task_id}@demo-calendar"
    m.provider, m.simulated, m.title, m.duration_minutes = provider, True, topic, duration
    m.proposed_slots = [s.isoformat() for s in slots]
    m.start_at = slots[0] if slots else None
    m.end_at = m.start_at + timedelta(minutes=duration) if m.start_at else None
    m.task_id = task_id
    pretty = "\n".join(f"  • {s.strftime('%a %d %b, %H:%M UTC')}" for s in slots)
    m.invite = {
        "simulated": True, "label": f"SIMULATED {flavour.replace('_', ' ').title()} invitation — {DEMO_LABEL}",
        "to": person.email, "subject": f"Invitation: {topic}",
        "body": f"Hi {person.first_name},\n\nHere are a few times for a {duration}-minute call:\n{pretty}\n\n"
                f"Pick whichever suits you.",
        "ics_preview": "\n".join(["BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VEVENT", f"UID:{m.uid}",
                                   f"SUMMARY:{topic}", f"DTSTART:{slots[0].strftime('%Y%m%dT%H%M%SZ') if slots else ''}",
                                   f"ATTENDEE;RSVP=TRUE:mailto:{person.email}", "END:VEVENT", "END:VCALENDAR"]),
    }
    _log(m, "invited", provider, f"simulated invitation with {len(slots)} proposed slot(s)")
    await db.flush()
    return m


async def respond(db: AsyncSession, meeting: Meeting, action: str, *, slot_index: int = 0, by: str) -> dict:
    """Presenter-triggered prospect response, delivered through the normal calendar event pipeline."""
    from app.events.inbound import CalendarEvent, process_calendar_event
    from app.models.base import Lead, Person
    lead = await db.get(Lead, meeting.lead_id)
    person = await db.get(Person, lead.person_id) if lead and lead.person_id else None
    if person is None:
        raise ValueError("meeting has no contact")
    if action == "accept":
        if meeting.status != "invited":
            raise ValueError(f"only an invited meeting can be accepted (it is {meeting.status})")
        slots = meeting.proposed_slots or []
        if not 0 <= slot_index < max(len(slots), 1):
            raise ValueError("no such slot")
        start = datetime.fromisoformat(slots[slot_index]) if slots else meeting.start_at
        ev = CalendarEvent(type="booked", provider=meeting.provider or "demo_calendar", meeting_id=meeting.uid,
                           attendee_email=person.email, start=start,
                           end=start + timedelta(minutes=meeting.duration_minutes) if start else None,
                           title=meeting.title, event_id=f"demo-accept-{uuid.uuid4().hex}")
    elif action == "decline":
        if meeting.status not in ("invited", "requested", "pending_approval"):
            raise ValueError(f"cannot decline a {meeting.status} meeting")
        ev = CalendarEvent(type="cancelled", provider=meeting.provider or "demo_calendar", meeting_id=meeting.uid,
                           attendee_email=person.email, title=meeting.title, notes="Prospect declined",
                           event_id=f"demo-decline-{uuid.uuid4().hex}")
    elif action == "reschedule":
        if meeting.status not in ("invited", "booked"):
            raise ValueError(f"cannot reschedule a {meeting.status} meeting")
        ev = CalendarEvent(type="rescheduled", provider=meeting.provider or "demo_calendar", meeting_id=meeting.uid,
                           attendee_email=person.email, title=meeting.title,
                           notes="Prospect asked for a different time", event_id=f"demo-resched-{uuid.uuid4().hex}")
    else:
        raise ValueError("action must be accept, decline or reschedule")
    result = await process_calendar_event(db, ev, tenant_id=lead.tenant_id)
    return result.model_dump(mode="json")


async def on_calendar_event(db: AsyncSession, tenant_id, uid: str, kind: str, start=None, end=None,
                            by: str = "prospect") -> Optional[Meeting]:
    """Called by the inbound calendar pipeline: keep the meeting record in step."""
    m = (await db.execute(select(Meeting).where(Meeting.tenant_id == tenant_id, Meeting.uid == uid))).scalar_one_or_none()
    if m is None:
        return None
    if kind == "booked":
        if start:
            m.start_at = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
            m.end_at = m.start_at + timedelta(minutes=m.duration_minutes)
        _log(m, "booked", by, f"accepted {m.start_at.isoformat() if m.start_at else ''}")
    elif kind == "cancelled":
        _log(m, "declined" if m.status != "booked" else "cancelled", by, "declined by the prospect")
    elif kind == "rescheduled":
        _log(m, "reschedule_requested", by, "prospect asked for another time")
    await db.flush()
    return m


def view(m: Meeting) -> dict:
    return {"id": str(m.id), "lead_id": str(m.lead_id), "campaign_id": str(m.campaign_id) if m.campaign_id else None,
            "uid": m.uid, "status": m.status, "provider": m.provider, "simulated": m.simulated, "title": m.title,
            "duration_minutes": m.duration_minutes, "proposed_slots": m.proposed_slots,
            "start_at": m.start_at, "end_at": m.end_at, "invite": m.invite, "history": m.history,
            "task_id": str(m.task_id) if m.task_id else None, "created_at": m.created_at, "updated_at": m.updated_at}
