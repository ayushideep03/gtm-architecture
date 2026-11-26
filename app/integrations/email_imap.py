"""
IMAP inbox poller — turns the sales mailbox into normalised inbound events.

Reads UNSEEN messages and classifies each one deterministically:
  - delivery status notifications (multipart/report; delivery-status) -> bounce
    (5.x.x = hard, 4.x.x = soft), matched by the original Message-ID
  - iCalendar replies (METHOD:REPLY) -> meeting booked / cancelled by PARTSTAT
  - everything else from a person -> reply (In-Reply-To / References / sender)
Then hands them to the same inbound pipeline as webhooks (opt-out rules, AI
classification, suppression, reactions). Messages are marked \\Seen after
processing. Works with any IMAP server (incl. BillionMail's Dovecot).

The worker calls ``poll`` on every available ``receive_email`` provider each tick.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import re
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Optional

from app.events.inbound import CalendarEvent, EmailEvent
from app.capabilities.web import html_to_text


class InboxProvider:
    """Marker interface for the receive_email capability."""

    async def poll(self, session) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


def _text(msg: Message) -> str:
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain.strip()
    return html_to_text(html)[1] if html else ""


def _ids(value: Optional[str]) -> list[str]:
    return re.findall(r"<[^>]+>", value or "")


def parse_message(raw: bytes, provider: str = "imap") -> list:
    """Pure parser: raw RFC822 bytes -> normalised EmailEvent / CalendarEvent list."""
    msg = email.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip() or None
    sender = parseaddr(msg.get("From", ""))[1].lower() or None
    try:
        when = parsedate_to_datetime(msg["Date"]) if msg.get("Date") else None
    except (TypeError, ValueError):
        when = None

    # Bounce (DSN)
    if msg.get_content_type() == "multipart/report" and msg.get_param("report-type") == "delivery-status":
        status, original = None, None
        for part in msg.walk():
            if part.get_content_type() == "message/delivery-status":
                found = re.search(r"Status:\s*([245])\.\d+\.\d+", part.as_string())
                status = found.group(1) if found else status
            if part.get_content_type() in ("message/rfc822", "text/rfc822-headers"):
                headers = part.as_string()
                ids = re.findall(r"Message-ID:\s*(<[^>]+>)", headers, flags=re.IGNORECASE)
                original = ids[0] if ids else original
        if original:
            return [EmailEvent(type="bounce", provider=provider, message_id=original,
                               bounce_type="hard" if status == "5" else "soft", occurred_at=when,
                               event_id=message_id)]
        return []

    # Calendar reply (iMIP)
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            ics = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
            if "METHOD:REPLY" in ics.upper():
                uid = re.search(r"^UID:(.+)$", ics, flags=re.MULTILINE)
                stat = re.search(r"PARTSTAT=([A-Z-]+)", ics)
                attendee = re.search(r"ATTENDEE[^:]*:mailto:([^\s\r\n]+)", ics, flags=re.IGNORECASE)
                if uid and stat and stat.group(1) in ("ACCEPTED", "DECLINED"):
                    return [CalendarEvent(type="booked" if stat.group(1) == "ACCEPTED" else "cancelled",
                                          provider=provider, meeting_id=uid.group(1).strip(),
                                          attendee_email=(attendee.group(1) if attendee else sender or ""),
                                          event_id=message_id, title=msg.get("Subject"))]

    # Ordinary reply
    refs = _ids(msg.get("In-Reply-To")) + _ids(msg.get("References"))
    auto = (msg.get("Auto-Submitted", "no").lower() != "no") and not re.search(
        r"out of (the )?office|automatic reply|autoreply", msg.get("Subject", ""), flags=re.IGNORECASE)
    if auto:
        return []  # machine-generated mail other than OOO notices is ignored
    return [EmailEvent(type="reply", provider=provider, in_reply_to=refs[0] if refs else None,
                       message_id=refs[-1] if len(refs) > 1 else None, from_email=sender,
                       to_email=(getaddresses([msg.get("To", "")]) or [("", None)])[0][1],
                       subject=msg.get("Subject"), body=_text(msg), occurred_at=when, event_id=message_id)]


class ImapInboxProvider(InboxProvider):
    def __init__(self, host: str, username: str, password: str, name: str = "imap", port: int = 993,
                 mailbox: str = "INBOX", max_messages: int = 50, ssl: bool = True, **_: Any) -> None:
        self._name, self.host, self.port, self.ssl = name, host, int(port), ssl
        self.username, self.password, self.mailbox, self.max = username, password, mailbox, max_messages

    @classmethod
    def from_config(cls, pc, credentials: dict) -> "ImapInboxProvider":
        from app.providers.loader import ProviderUnavailable
        if not pc.settings.get("host"):
            raise ProviderUnavailable("settings.host is required")
        if "username" not in credentials or "password" not in credentials:
            raise ProviderUnavailable("username/password credentials are required")
        return cls(name=pc.name, **{**pc.settings, **credentials})

    @property
    def provider_name(self) -> str:
        return self._name

    def _fetch(self) -> list[tuple[bytes, bytes]]:
        conn = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl else imaplib.IMAP4(self.host, self.port)
        try:
            conn.login(self.username, self.password)
            conn.select(self.mailbox)
            _, data = conn.search(None, "UNSEEN")
            out = []
            for num in (data[0].split() if data and data[0] else [])[: self.max]:
                _, msg_data = conn.fetch(num, "(BODY.PEEK[])")
                if msg_data and isinstance(msg_data[0], tuple):
                    out.append((num, msg_data[0][1]))
            return out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _mark_seen(self, nums: list[bytes]) -> None:
        if not nums:
            return
        conn = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl else imaplib.IMAP4(self.host, self.port)
        try:
            conn.login(self.username, self.password)
            conn.select(self.mailbox)
            for num in nums:
                conn.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def poll(self, session) -> dict[str, Any]:
        from app.events.inbound import process_calendar_event, process_email_event
        messages = await asyncio.to_thread(self._fetch)
        processed, events = [], 0
        for num, raw in messages:
            for ev in parse_message(raw, provider=self._name):
                if isinstance(ev, CalendarEvent):
                    await process_calendar_event(session, ev)
                else:
                    await process_email_event(session, ev)
                events += 1
            processed.append(num)
        await session.commit()
        await asyncio.to_thread(self._mark_seen, processed)
        return {"provider": self._name, "messages": len(messages), "events": events}
