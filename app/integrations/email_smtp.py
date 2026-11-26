"""
SMTP email execution (works with any SMTP server — Google Workspace, Microsoft 365,
SES, Postmark, or a self-hosted BillionMail/Postfix).

Two providers:
  SmtpEmailProvider    capability send_email   — sends the approved draft
  SmtpMeetingProvider  capability book_meeting — sends a standards-based calendar
                       invitation (iCalendar METHOD:REQUEST); acceptances come
                       back as iMIP replies and are read by the IMAP inbox poller

Safety:
  - the recipient is ALWAYS the prospect's stored email; a payload that tries to
    send elsewhere is refused (RECIPIENT_MISMATCH);
  - Message-ID / iCalendar UID derive from the task id, so replies map back to the
    exact touch and a retried send keeps the same id;
  - SMTP offers no server-side de-duplication, so ``supports_idempotency`` stays
    false: an ambiguous failure (timeout mid-send) is flagged for human review by
    the executor instead of being retried;
  - provider ``mode: dry_run`` records the touch without connecting to SMTP.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Interaction, Lead, Person


class _SmtpBase(ExecutionProvider):
    capability = ""

    def __init__(self, host: str, from_address: str, name: str = "smtp", port: int = 587,
                 security: str = "starttls", from_name: str = "", reply_to: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None, timeout_s: float = 30.0,
                 mode: str = "live", **settings: Any) -> None:
        self._name, self.host, self.port, self.security = name, host, int(port), security
        self.from_address, self.from_name, self.reply_to = from_address, from_name, reply_to
        self.username, self.password, self.timeout = username, password, timeout_s
        self.mode = mode
        self.settings = settings

    @classmethod
    def from_config(cls, pc, credentials: dict):
        from app.providers.loader import ProviderUnavailable
        if not pc.settings.get("host") or not pc.settings.get("from_address"):
            raise ProviderUnavailable("settings.host and settings.from_address are required")
        return cls(name=pc.name, mode=pc.mode, timeout_s=pc.resilience.timeout_s, **{**pc.settings, **credentials})

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return [self.capability]

    @property
    def domain(self) -> str:
        return self.from_address.rsplit("@", 1)[-1]

    def message_id(self, task_id: uuid.UUID) -> str:
        return f"<oxygen-{task_id}@{self.domain}>"

    def _fail(self, code: str, msg: str) -> ExecutionResult:
        return ExecutionResult(success=False, status="failed", provider=self._name, error_code=code, error_message=msg)

    async def _recipient(self, context: ExecutionContext, session: AsyncSession) -> tuple[Optional[Person], Optional[str]]:
        lead = await session.get(Lead, context.lead_id) if context.lead_id else None
        person = await session.get(Person, lead.person_id) if lead and lead.person_id else None
        if person is None or not person.email:
            return None, "prospect has no email address"
        requested = context.payload.get("recipient_email")
        if requested and requested.strip().lower() != person.email.lower():
            return None, "payload recipient differs from the prospect's stored email"
        return person, None

    def _send(self, msg: EmailMessage) -> None:
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            server.ehlo()
            if self.security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if self.username:
                server.login(self.username, self.password or "")
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass

    def _base_message(self, to: str, subject: str, task_id: uuid.UUID) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.from_address)) if self.from_name else self.from_address
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = self.message_id(task_id)
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        msg["List-Unsubscribe"] = f"<mailto:{self.reply_to or self.from_address}?subject=unsubscribe>"
        return msg

    async def _deliver(self, msg: EmailMessage) -> None:
        if self.mode == "dry_run":
            return
        await asyncio.to_thread(self._send, msg)


class SmtpEmailProvider(_SmtpBase):
    capability = "send_email"

    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult:
        if session is None:
            return self._fail("SESSION_REQUIRED", "send_email needs a database session")
        person, problem = await self._recipient(context, session)
        if problem:
            return self._fail("RECIPIENT_MISMATCH" if "differs" in problem else "MISSING_RECIPIENT", problem)
        subject, body = context.payload.get("subject"), context.payload.get("body")
        if not subject or not body:
            return self._fail("MISSING_CONTENT", "approved subject and body are required")
        msg = self._base_message(person.email, subject, context.task_id)
        msg.set_content(body)
        try:
            await self._deliver(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            return self._fail("RECIPIENT_REFUSED", f"SMTP refused recipient: {exc}")
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused) as exc:
            return self._fail("UNAVAILABLE", f"SMTP rejected sender/credentials: {exc}")
        session.add(Interaction(lead_id=context.lead_id, interaction_type="email_sent", direction="outbound",
                                subject=subject, body=body, channel_message_id=msg["Message-ID"],
                                occurred_at=datetime.now(timezone.utc), task_id=context.task_id, channel="email",
                                provider=self._name, details={"mode": self.mode, "to": person.email}))
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name, result={
            "sent": self.mode != "dry_run", "mode": self.mode, "message_id": msg["Message-ID"],
            "recipient": person.email, "subject": subject, "interaction_recorded": True})


def next_slot(cfg: dict, now: Optional[datetime] = None) -> datetime:
    """First slot from configured availability (weekday hours in a timezone) after the minimum notice."""
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    days = set(cfg.get("weekdays", [0, 1, 2, 3, 4]))
    hours = sorted(cfg.get("hours", [10, 14, 16]))
    earliest = (now or datetime.now(timezone.utc)) + timedelta(hours=float(cfg.get("min_notice_hours", 24)))
    day = earliest.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    for _ in range(14):
        if day.weekday() in days:
            for h in hours:
                slot = day.replace(hour=h)
                if slot >= earliest:
                    return slot.astimezone(timezone.utc)
        day = (day + timedelta(days=1)).replace(hour=0)
    raise ValueError("no available slot in the next 14 days")


def _ics_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class SmtpMeetingProvider(_SmtpBase):
    capability = "book_meeting"

    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult:
        if session is None:
            return self._fail("SESSION_REQUIRED", "book_meeting needs a database session")
        person, problem = await self._recipient(context, session)
        if problem:
            return self._fail("MISSING_RECIPIENT", problem)
        payload = context.payload
        if payload.get("scheduled_for"):
            start = datetime.fromisoformat(str(payload["scheduled_for"]))
            start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        else:
            start = next_slot(self.settings.get("availability", {}))
        duration = int(payload.get("duration_minutes") or 30)
        end = start + timedelta(minutes=duration)
        topic = payload.get("topic") or "Introductory call"
        uid = f"oxygen-{context.task_id}@{self.domain}"
        ics = "\r\n".join([
            "BEGIN:VCALENDAR", "PRODID:-//Oxygen//Sales OS//EN", "VERSION:2.0", "METHOD:REQUEST",
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_ics_time(datetime.now(timezone.utc))}",
            f"DTSTART:{_ics_time(start)}", f"DTEND:{_ics_time(end)}", f"SUMMARY:{topic}",
            f"ORGANIZER;CN={self.from_name or self.from_address}:mailto:{self.from_address}",
            f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{person.email}",
            f"DESCRIPTION:{(payload.get('agenda') or topic).replace(chr(10), ' ')}",
            "STATUS:CONFIRMED", "SEQUENCE:0", "END:VEVENT", "END:VCALENDAR", ""])
        msg = self._base_message(person.email, f"Invitation: {topic}", context.task_id)
        msg.set_content(f"{payload.get('message') or 'Sending a calendar invitation for our call.'}\n\n"
                        f"{topic} — {start.isoformat()} ({duration} min)")
        msg.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
        await self._deliver(msg)
        session.add(Interaction(lead_id=context.lead_id, interaction_type="meeting", direction="outbound",
                                subject=f"Invitation sent: {topic}", body=f"{start.isoformat()} ({duration} min)",
                                channel_message_id=uid, occurred_at=datetime.now(timezone.utc),
                                task_id=context.task_id, channel="meeting", provider=self._name,
                                details={"mode": self.mode, "start": start.isoformat(), "status": "invited"}))
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name, result={
            "invited": self.mode != "dry_run", "mode": self.mode, "meeting_uid": uid, "start": start.isoformat(),
            "duration_minutes": duration, "topic": topic, "interaction_recorded": True,
            "note": "Booked when the attendee accepts (iMIP reply) or a calendar webhook confirms."})
