"""
Deterministic compliance checks for external communication.

Used twice: by guardrails when Oxygen proposes an action, and by the executor
immediately before a provider is called (a suppression or opt-out may arrive
between proposal, approval and execution). No AI is involved.

  suppression   email / email domain / company domain / person / company on an
                active do-not-contact list
  lawful basis  an un-withdrawn consent record with an allowed basis
  frequency     configured caps per person / domain / company within rolling
                windows, plus a minimum gap between touches
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.models.base import Company, Interaction, Lead, Person
from app.models.memory import ContactConsent, Suppression
from app.services.normalization import normalize_domain, normalize_email

logger = get_logger(__name__)

OUTBOUND_TOUCH_TYPES = ("email_sent", "linkedin_message", "linkedin_connection", "call", "meeting")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Suppression ───────────────────────────────────────────────────────────────

def _suppression_value(scope: str, value: str) -> str:
    if scope == "email":
        return normalize_email(value) or value.strip().lower()
    if scope == "domain":
        return normalize_domain(value) or value.strip().lower()
    return str(value)


async def add_suppression(
    db: AsyncSession, *, tenant_id: uuid.UUID, scope: str, value: str, reason: str,
    source: Optional[str] = None, created_by: Optional[str] = None, note: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> Suppression:
    if scope not in ("email", "domain", "person", "company"):
        raise ValueError(f"invalid suppression scope {scope!r}")
    norm = _suppression_value(scope, value)
    existing = (await db.execute(select(Suppression).where(
        Suppression.tenant_id == tenant_id, Suppression.scope == scope, Suppression.value == norm,
        Suppression.lifted_at.is_(None)))).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Suppression(tenant_id=tenant_id, scope=scope, value=norm, reason=reason, source=source,
                      created_by=created_by, note=note, expires_at=expires_at)
    db.add(row)
    await db.flush()
    logger.info("Suppression added: %s=%s (%s)", scope, norm, reason)
    return row


async def lift_suppression(db: AsyncSession, suppression_id: uuid.UUID, lifted_by: str) -> Suppression:
    row = await db.get(Suppression, suppression_id)
    if row is None or row.lifted_at is not None:
        raise ValueError("suppression not found or already lifted")
    row.lifted_at, row.lifted_by = _now(), lifted_by
    await db.flush()
    return row


async def active_suppressions(db: AsyncSession, tenant_id: uuid.UUID, person: Optional[Person],
                              company: Optional[Company]) -> list[Suppression]:
    keys: list[tuple[str, str]] = []
    if person is not None:
        keys.append(("person", str(person.id)))
        if person.email:
            keys.append(("email", normalize_email(person.email) or person.email))
            email_domain = person.email.rsplit("@", 1)[-1]
            keys.append(("domain", normalize_domain(email_domain) or email_domain))
    if company is not None:
        keys.append(("company", str(company.id)))
        if company.domain:
            keys.append(("domain", company.domain))
    if not keys:
        return []
    now = _now()
    stmt = select(Suppression).where(
        Suppression.tenant_id == tenant_id, Suppression.lifted_at.is_(None),
        or_(Suppression.expires_at.is_(None), Suppression.expires_at > now),
        or_(*[and_(Suppression.scope == s, Suppression.value == v) for s, v in keys]))
    return list((await db.execute(stmt)).scalars().all())


# ── Lawful basis ──────────────────────────────────────────────────────────────

async def record_consent(db: AsyncSession, *, tenant_id: uuid.UUID, person_id: uuid.UUID, basis: str,
                         source: str, channel: Optional[str] = None, evidence: Optional[str] = None) -> ContactConsent:
    row = ContactConsent(tenant_id=tenant_id, person_id=person_id, basis=basis, source=source,
                         channel=channel, evidence=evidence)
    db.add(row)
    await db.flush()
    return row


async def withdraw_consent(db: AsyncSession, *, tenant_id: uuid.UUID, person_id: uuid.UUID) -> int:
    rows = (await db.execute(select(ContactConsent).where(
        ContactConsent.tenant_id == tenant_id, ContactConsent.person_id == person_id,
        ContactConsent.withdrawn_at.is_(None)))).scalars().all()
    for r in rows:
        r.withdrawn_at = _now()
    await db.flush()
    return len(rows)


async def lawful_basis(db: AsyncSession, tenant_id: uuid.UUID, person: Optional[Person],
                       channel: Optional[str]) -> tuple[bool, Optional[str], str]:
    cfg = get_gtm_config().for_tenant(tenant_id).compliance.lawful_basis
    if not cfg.required:
        return True, None, "lawful basis not required by policy"
    if person is None:
        return False, None, "no contact person"
    rows = (await db.execute(select(ContactConsent).where(
        ContactConsent.tenant_id == tenant_id, ContactConsent.person_id == person.id,
        ContactConsent.withdrawn_at.is_(None),
        or_(ContactConsent.channel.is_(None), ContactConsent.channel == channel))
        .order_by(ContactConsent.recorded_at.desc()))).scalars().all()
    for r in rows:
        if r.basis in cfg.allowed:
            return True, r.basis, f"{r.basis} ({r.source})"
    return False, None, "no valid lawful basis recorded for this contact"


# ── Frequency ─────────────────────────────────────────────────────────────────

async def touch_counts(db: AsyncSession, tenant_id: uuid.UUID, person: Optional[Person],
                       company: Optional[Company]) -> dict:
    """Outbound touches per configured cap, plus the time of the last touch to this person."""
    cfg = get_gtm_config().for_tenant(tenant_id).compliance
    now = _now()
    out: dict = {"caps": [], "last_touch_at": None}
    base = (select(func.count(Interaction.id)).join(Lead, Lead.id == Interaction.lead_id)
            .join(Person, Person.id == Lead.person_id)
            .where(Interaction.tenant_id == tenant_id, Interaction.direction == "outbound",
                   Interaction.interaction_type.in_(OUTBOUND_TOUCH_TYPES)))
    domain = company.domain if company is not None and company.domain else (
        person.email.rsplit("@", 1)[-1] if person is not None and person.email else None)
    for cap in cfg.frequency_caps:
        since = now - timedelta(days=cap.window_days)
        stmt = base.where(Interaction.occurred_at >= since)
        if cap.scope == "person":
            if person is None:
                continue
            stmt = stmt.where(Person.id == person.id)
        elif cap.scope == "company":
            if company is None:
                continue
            stmt = stmt.where(Person.company_id == company.id)
        else:  # domain
            if not domain:
                continue
            stmt = stmt.where(or_(Person.email.ilike(f"%@{domain}"),
                                  Person.company_id.in_(select(Company.id).where(Company.domain == domain))))
        count = (await db.execute(stmt)).scalar() or 0
        out["caps"].append({"scope": cap.scope, "window_days": cap.window_days, "max": cap.max_touches,
                            "count": int(count)})
    if person is not None:
        last = (await db.execute(
            select(func.max(Interaction.occurred_at)).join(Lead, Lead.id == Interaction.lead_id)
            .where(Lead.person_id == person.id, Interaction.direction == "outbound",
                   Interaction.interaction_type.in_(OUTBOUND_TOUCH_TYPES)))).scalar()
        out["last_touch_at"] = last.isoformat() if last else None
        reply = (await db.execute(
            select(func.max(Interaction.occurred_at)).join(Lead, Lead.id == Interaction.lead_id)
            .where(Lead.person_id == person.id, Interaction.direction == "inbound",
                   Interaction.interaction_type == "email_received"))).scalar()
        out["last_reply_at"] = reply.isoformat() if reply else None
    return out


# ── Combined check ────────────────────────────────────────────────────────────

@dataclass
class ComplianceStatus:
    suppressed: bool = False
    suppression_reasons: list[str] = field(default_factory=list)
    lawful_basis_ok: bool = True
    lawful_basis: Optional[str] = None
    lawful_basis_reason: str = ""
    frequency_ok: bool = True
    frequency_reason: str = ""
    touches: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return not self.suppressed and self.lawful_basis_ok and self.frequency_ok

    def reasons(self) -> list[str]:
        out = list(self.suppression_reasons)
        if not self.lawful_basis_ok:
            out.append(self.lawful_basis_reason)
        if not self.frequency_ok:
            out.append(self.frequency_reason)
        return out

    def as_dict(self) -> dict:
        return {**asdict(self), "allowed": self.allowed}


async def check_contact_allowed(db: AsyncSession, lead: Lead, channel: Optional[str] = "email") -> ComplianceStatus:
    tenant = lead.tenant_id
    person = await db.get(Person, lead.person_id) if lead.person_id else None
    company = await db.get(Company, person.company_id) if person is not None and person.company_id else None
    status = ComplianceStatus()

    for s in await active_suppressions(db, tenant, person, company):
        status.suppressed = True
        status.suppression_reasons.append(f"{s.scope} {s.value} suppressed ({s.reason})")

    status.lawful_basis_ok, status.lawful_basis, status.lawful_basis_reason = await lawful_basis(db, tenant, person, channel)

    cfg = get_gtm_config().for_tenant(tenant).compliance
    status.touches = await touch_counts(db, tenant, person, company)
    for cap in status.touches["caps"]:
        if cap["count"] >= cap["max"]:
            status.frequency_ok = False
            status.frequency_reason = (f"{cap['scope']} already contacted {cap['count']} time(s) in "
                                       f"{cap['window_days']:g} days (limit {cap['max']})")
            break
    last = status.touches.get("last_touch_at")
    reply = status.touches.get("last_reply_at")
    answering = cfg.waive_gap_after_reply and reply and (not last or reply >= last)
    if status.frequency_ok and last and cfg.min_hours_between_touches > 0 and not answering:
        gap = (_now() - datetime.fromisoformat(last)).total_seconds() / 3600
        if gap < cfg.min_hours_between_touches:
            status.frequency_ok = False
            status.frequency_reason = (f"last touch was {gap:.1f}h ago; minimum gap is "
                                       f"{cfg.min_hours_between_touches:g}h")
    return status
