"""
Inbound webhooks, lead imports and autonomy controls.

    POST /api/v1/webhooks/email          normalised email events (reply, bounce, delivered, opened, clicked,
                                         unsubscribe, complaint)  — signed
    POST /api/v1/webhooks/calendar       meeting booked / cancelled / rescheduled — signed
    POST /api/v1/webhooks/inbound-lead   website form submission (with consent) — signed
    POST /api/v1/leads/import            CSV import (JSON body: {source, csv})

    GET  /api/v1/autonomy/status
    POST /api/v1/autonomy/pause | /resume
    POST /api/v1/autonomy/tick           run one worker tick now (demo / ops)

Signatures: X-Oxygen-Timestamp + X-Oxygen-Signature = hex HMAC-SHA256(secret, "<timestamp>.<raw body>"),
secret from the env var named in config webhooks.secret_env. Unsigned requests are accepted only when
webhooks.allow_unsigned is true (development/test profiles).
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.rules import record_consent
from app.core.database import get_db
from app.core.gtm_config import get_gtm_config
from app.core.tenancy import DEFAULT_TENANT_ID
from app.events.inbound import (
    CalendarEvent,
    EmailEvent,
    SignatureError,
    process_calendar_event,
    process_email_event,
    verify_signature,
)
from app.integrations.lead_source_record import LeadSourceRecord
from app.services.lead_import import ImportError_, parse_csv
from app.services.lead_ingestion import LeadIngestionService

webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])
imports_router = APIRouter(prefix="/leads", tags=["leads"])
autonomy_router = APIRouter(prefix="/autonomy", tags=["autonomy"])


async def _verified_json(request: Request, signature: Optional[str], timestamp: Optional[str]) -> dict:
    body = await request.body()
    try:
        verify_signature(body, signature, timestamp, get_gtm_config())
    except SignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid webhook signature: {exc}")
    try:
        return json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must be JSON")


@webhooks_router.post("/email", summary="Inbound email event (normalised)")
async def email_webhook(request: Request, db: AsyncSession = Depends(get_db),
                        x_oxygen_signature: Optional[str] = Header(None),
                        x_oxygen_timestamp: Optional[str] = Header(None)) -> dict:
    data = await _verified_json(request, x_oxygen_signature, x_oxygen_timestamp)
    try:
        ev = EmailEvent.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, json.loads(exc.json()))
    result = await process_email_event(db, ev)
    await db.commit()
    return result.model_dump(mode="json")


@webhooks_router.post("/calendar", summary="Inbound calendar event (normalised)")
async def calendar_webhook(request: Request, db: AsyncSession = Depends(get_db),
                           x_oxygen_signature: Optional[str] = Header(None),
                           x_oxygen_timestamp: Optional[str] = Header(None)) -> dict:
    data = await _verified_json(request, x_oxygen_signature, x_oxygen_timestamp)
    try:
        ev = CalendarEvent.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, json.loads(exc.json()))
    result = await process_calendar_event(db, ev)
    await db.commit()
    return result.model_dump(mode="json")


class InboundLead(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    company_domain: Optional[str] = None
    message: Optional[str] = None
    source: str = "website_form"
    consent_basis: str = "consent"                 # what the form captured
    consent_text: Optional[str] = None             # the wording the person agreed to
    form_id: Optional[str] = None


@webhooks_router.post("/inbound-lead", summary="Inbound lead from a website form")
async def inbound_lead(request: Request, db: AsyncSession = Depends(get_db),
                       x_oxygen_signature: Optional[str] = Header(None),
                       x_oxygen_timestamp: Optional[str] = Header(None)) -> dict:
    data = await _verified_json(request, x_oxygen_signature, x_oxygen_timestamp)
    try:
        lead = InboundLead.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, json.loads(exc.json()))
    record = LeadSourceRecord(source=f"inbound:{lead.source}", external_id=lead.form_id, email=lead.email,
                              first_name=lead.first_name, last_name=lead.last_name, job_title=lead.job_title,
                              company_name=lead.company, company_domain=lead.company_domain,
                              raw_data={"message": lead.message})
    summary = await LeadIngestionService().ingest_batch([record], db, f"inbound:{lead.source}")
    if summary.errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, summary.errors)
    from app.services.identity_resolution import IdentityResolver
    person = await IdentityResolver().resolve_person(db, lead.email, tenant_id=DEFAULT_TENANT_ID)
    if person.found:
        await record_consent(db, tenant_id=DEFAULT_TENANT_ID, person_id=person.person_id, basis=lead.consent_basis,
                             source=f"form:{lead.form_id or lead.source}", evidence=lead.consent_text)
    await db.commit()
    return summary.to_dict()


class CSVImport(BaseModel):
    source: str = Field("csv_upload", pattern=r"^[a-z0-9_\-]{2,60}$")
    csv: str


@imports_router.post("/import", summary="Import leads from CSV text")
async def import_csv(body: CSVImport, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        records, warnings = parse_csv(body.csv, f"csv:{body.source}")
    except ImportError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    summary = await LeadIngestionService().ingest_batch(records, db, f"csv:{body.source}")
    await db.commit()
    return {**summary.to_dict(), "warnings": warnings}


# ── Autonomy ─────────────────────────────────────────────────────────────────

class Actor(BaseModel):
    actor: str


@autonomy_router.get("/status", summary="Is the autonomous loop running?")
async def autonomy_status() -> dict:
    from app.services.autonomy import is_paused
    cfg = get_gtm_config().autonomy
    return {"enabled": cfg.enabled, "paused": await is_paused(), "tick_interval_s": cfg.tick_interval_s,
            "max_cycles_per_lead_per_day": cfg.max_cycles_per_lead_per_day,
            "reactions": {k: v.model_dump() for k, v in cfg.reactions.items()}}


@autonomy_router.post("/pause", summary="Pause all autonomous activity (all workers)")
async def autonomy_pause(body: Actor) -> dict:
    from app.services.autonomy import set_paused
    await set_paused(True, body.actor)
    return {"paused": True}


@autonomy_router.post("/resume", summary="Resume autonomous activity")
async def autonomy_resume(body: Actor) -> dict:
    from app.services.autonomy import set_paused
    await set_paused(False, body.actor)
    return {"paused": False}


@autonomy_router.post("/tick", summary="Run one autonomous tick now")
async def autonomy_tick() -> dict:
    from app.services.autonomy import tick
    report = await tick(worker="api")
    return json.loads(json.dumps(report, default=str))
