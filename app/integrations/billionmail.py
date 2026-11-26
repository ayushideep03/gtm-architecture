"""
BillionMail sender (optional email infrastructure, capability send_email).

BillionMail is AGPL-3.0 and runs as a separate, unmodified container; Oxygen
talks to it over HTTP only. Its send API queues a mail built from a template
created in BillionMail beforehand:

    POST {base_url}/api/batch_mail/api/send      header x-api-key
    {"addresser": ..., "recipient": ..., "attribs": {...}}

The template must render ``{{subject}}`` and ``{{body}}`` from attribs. The API
returns no message id and offers no idempotency, so replies are matched by the
sender's address (IMAP poller) and ``supports_idempotency`` stays false.

Built from BillionMail's source; NOT verified against a live instance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Interaction, Lead, Person


class BillionMailProvider(ExecutionProvider):
    def __init__(self, base_url: str, api_key: str, addresser: str, name: str = "billionmail",
                 mode: str = "live", timeout_s: float = 30.0, transport: Optional[httpx.AsyncBaseTransport] = None,
                 **_: Any) -> None:
        self._name, self.base_url, self.api_key, self.addresser = name, base_url.rstrip("/"), api_key, addresser
        self.mode, self.timeout, self.transport = mode, timeout_s, transport

    @classmethod
    def from_config(cls, pc, credentials: dict) -> "BillionMailProvider":
        from app.providers.loader import ProviderUnavailable
        merged = {**pc.settings, **credentials}
        if not merged.get("base_url") or not merged.get("addresser"):
            raise ProviderUnavailable("base_url and addresser are required")
        return cls(name=pc.name, mode=pc.mode, timeout_s=pc.resilience.timeout_s, **merged)

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return ["send_email"]

    def _fail(self, code: str, msg: str) -> ExecutionResult:
        return ExecutionResult(success=False, status="failed", provider=self._name, error_code=code, error_message=msg)

    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult:
        if session is None:
            return self._fail("SESSION_REQUIRED", "send_email needs a database session")
        lead = await session.get(Lead, context.lead_id) if context.lead_id else None
        person = await session.get(Person, lead.person_id) if lead and lead.person_id else None
        if person is None or not person.email:
            return self._fail("MISSING_RECIPIENT", "prospect has no email address")
        requested = context.payload.get("recipient_email")
        if requested and requested.strip().lower() != person.email.lower():
            return self._fail("RECIPIENT_MISMATCH", "payload recipient differs from the prospect's stored email")
        subject, body = context.payload.get("subject"), context.payload.get("body")
        if not subject or not body:
            return self._fail("MISSING_CONTENT", "approved subject and body are required")
        request = {"addresser": self.addresser, "recipient": person.email,
                   "attribs": {"subject": subject, "body": body, "oxygen_key": f"oxygen-{context.task_id}"}}
        if self.mode != "dry_run":
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as c:
                resp = await c.post("/api/batch_mail/api/send", json=request, headers={"x-api-key": self.api_key})
            if resp.status_code in (401, 403):
                return self._fail("UNAVAILABLE", f"BillionMail rejected the API key ({resp.status_code})")
            if resp.status_code >= 400:
                return self._fail("PROVIDER_ERROR", f"BillionMail HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json() if resp.content else {}
            if isinstance(data, dict) and data.get("success") is False:
                return self._fail("PROVIDER_ERROR", f"BillionMail: {data.get('msg') or data}")
        session.add(Interaction(lead_id=context.lead_id, interaction_type="email_sent", direction="outbound",
                                subject=subject, body=body, channel_message_id=f"oxygen-{context.task_id}",
                                occurred_at=datetime.now(timezone.utc), task_id=context.task_id, channel="email",
                                provider=self._name, details={"mode": self.mode, "to": person.email, "queued": True}))
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name, result={
            "queued": self.mode != "dry_run", "mode": self.mode, "recipient": person.email, "subject": subject,
            "interaction_recorded": True, "note": "BillionMail queues mail; no message id is returned."})
