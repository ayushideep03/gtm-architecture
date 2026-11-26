"""
Lead Ingestion Service.

    LeadSourceRecord
         ↓ normalize
    IdentityResolver.resolve_company   ← configured match keys (source record, domain, website, LinkedIn…)
         ↓ create or merge (merge policy from config)
    IdentityResolver.resolve_person    ← configured match keys (source record, email, LinkedIn, name+company)
         ↓ create or merge
    lead                               ← reuse the person's open lead; otherwise attach/create per policy
         ↓
    source_records                     ← remember (source, external_id) → entities
         ↓
    event                              ← lead_ingested (new lead) | lead_resourced (existing lead)

Matching lives ONLY in IdentityResolver. The database backs it with
tenant-scoped unique indexes (companies.domain, people.email, one open lead per
person), and inserts run in savepoints so a concurrent import that wins the race
is resolved to the existing row instead of creating a duplicate.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.core.tenancy import DEFAULT_TENANT_ID, active_tenant  # noqa: F401
from app.integrations.lead_source_record import LeadSourceRecord
from app.models.base import Company, Event, Lead, Person, SourceRecord
from app.compliance.rules import record_consent
from app.memory.facts import EvidenceInput, FactLedger, score_evidence
from app.services.identity_resolution import IdentityResolver, MatchType
from app.services.normalization import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_url,
)

logger = get_logger(__name__)
T = TypeVar("T")



# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class IngestionResult:
    lead_id: uuid.UUID
    person_id: Optional[uuid.UUID]
    company_id: Optional[uuid.UUID]
    company_created: bool = False
    person_created: bool = False
    lead_created: bool = True
    event_created: bool = False
    company_match: str = MatchType.NO_MATCH.value
    person_match: str = MatchType.NO_MATCH.value
    fields_merged: list[str] = field(default_factory=list)


@dataclass
class BatchIngestionSummary:
    provider: str
    records_received: int = 0
    companies_created: int = 0
    companies_existing: int = 0
    people_created: int = 0
    people_existing: int = 0
    leads_created: int = 0
    leads_existing: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)

    def record(self, result: IngestionResult) -> None:
        if result.company_created:
            self.companies_created += 1
        elif result.company_id:
            self.companies_existing += 1
        if result.person_created:
            self.people_created += 1
        elif result.person_id:
            self.people_existing += 1
        if result.lead_created:
            self.leads_created += 1
        else:
            self.leads_existing += 1
        if result.event_created:
            self.events_created += 1

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "records_received": self.records_received,
            "companies_created": self.companies_created,
            "companies_existing": self.companies_existing,
            "people_created": self.people_created,
            "people_existing": self.people_existing,
            "leads_created": self.leads_created,
            "leads_existing": self.leads_existing,
            "events_created": self.events_created,
            "errors": self.errors,
        }


# ── Service ───────────────────────────────────────────────────────────────────

class LeadIngestionService:
    """Stateless; all state lives in the database session."""

    def __init__(self, resolver: Optional[IdentityResolver] = None, ledger: Optional[FactLedger] = None) -> None:
        self.resolver = resolver or IdentityResolver()
        self.ledger = ledger or FactLedger()

    async def ingest_batch(
        self,
        records: list[LeadSourceRecord],
        db: AsyncSession,
        provider_name: str,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> BatchIngestionSummary:
        """Ingest records sequentially; each record succeeds fully or is reported as an error."""
        summary = BatchIngestionSummary(provider=provider_name, records_received=len(records))
        for record in records:
            try:
                async with db.begin_nested():
                    result = await self._ingest_one(record, db, tenant_id or active_tenant())
                summary.record(result)
            except Exception as exc:  # noqa: BLE001 - one bad record must not sink the batch
                err_msg = f"Record {record.external_id!r}: {exc}"
                logger.error("Ingestion error — %s", err_msg)
                summary.errors.append(err_msg)

        logger.info(
            "Ingestion complete [%s]: %d records, companies %d new/%d existing, people %d new/%d existing, "
            "leads %d new/%d existing",
            provider_name, summary.records_received, summary.companies_created, summary.companies_existing,
            summary.people_created, summary.people_existing, summary.leads_created, summary.leads_existing,
        )
        return summary

    # ── one record ───────────────────────────────────────────────────────────

    async def _ingest_one(self, record: LeadSourceRecord, db: AsyncSession, tenant: uuid.UUID) -> IngestionResult:
        cfg = get_gtm_config().for_tenant(tenant)
        policy = cfg.identity
        ext_id = str(record.external_id) if record.external_id else None
        kind = next((k for prefix, k in cfg.memory.source_evidence_kinds.items()
                     if (record.source or "").startswith(prefix)), "lead_source")
        evidence = [EvidenceInput(kind=kind, detail=f"{record.source} record", source_ref=ext_id,
                                  source_url=record.source_url, observed_at=record.collected_at)]

        company_values = {
            "name": normalize_name(record.company_name),
            "domain": normalize_domain(record.company_domain),
            "linkedin_url": normalize_url(record.company_linkedin_url),
            "website": normalize_url(record.company_website),
            "industry": record.industry,
            "employee_count": record.employee_count,
        }
        person_values = {
            "first_name": normalize_name(record.effective_first_name()),
            "last_name": normalize_name(record.effective_last_name()),
            "email": normalize_email(record.email),
            "phone": normalize_phone(record.phone),
            "title": normalize_name(record.job_title),
            "linkedin_url": normalize_url(record.linkedin_url),
        }
        merged: list[str] = []
        # A weak source (e.g. a guessed email) must not drive identity matching or land in the
        # CRM column; it is recorded only as a proposed fact for review.
        weak_email = None
        if person_values["email"] and score_evidence(evidence, cfg.memory).band == "possible":
            weak_email = person_values["email"]
            person_values = {**person_values, "email": None}

        # 1. Company
        company_res = await self.resolver.resolve_company(
            db, company_values["domain"], record.company_website,
            linkedin_url=record.company_linkedin_url, name=record.company_name,
            source=record.source, external_id=ext_id, tenant_id=tenant,
        )
        company: Optional[Company] = None
        company_created = False
        if company_res.found:
            company = await db.get(Company, company_res.company_id)
        elif company_values["name"] or company_values["domain"]:
            company, company_created = await self._insert(
                db,
                Company(tenant_id=tenant, **{**company_values, "name": company_values["name"] or company_values["domain"]}),
                lambda: self._resolve_company_entity(db, company_values, record, ext_id, tenant),
            )
        if company is not None:
            merged += [f"company.{f}" for f in await self._record_facts(db, company, company_values, evidence, record.source)]

        # 2. Person
        person_res = await self.resolver.resolve_person(
            db, person_values["email"],
            linkedin_url=person_values["linkedin_url"],
            first_name=person_values["first_name"], last_name=person_values["last_name"],
            company_id=company.id if company else None,
            source=record.source, external_id=ext_id, tenant_id=tenant,
        )
        person_created = False
        if person_res.found:
            person = await db.get(Person, person_res.person_id)
            if person.company_id is None and company is not None:
                person.company_id = company.id
                merged.append("person.company_id")
        else:
            person, person_created = await self._insert(
                db,
                Person(tenant_id=tenant, company_id=company.id if company else None,
                       **{**person_values, "first_name": person_values["first_name"] or "Unknown"}),
                lambda: self._resolve_person_entity(db, person_values, company, record, ext_id, tenant),
            )
            if person_created and cfg.compliance.lawful_basis.default_on_ingest:
                await record_consent(db, tenant_id=tenant, person_id=person.id,
                                     basis=cfg.compliance.lawful_basis.default_on_ingest, source="default_policy",
                                     evidence=f"Recorded at ingestion from {record.source} per configured B2B policy")
        merged += [f"person.{f}" for f in await self._record_facts(db, person, person_values, evidence, record.source)]
        if weak_email:
            await self.ledger.record(db, person, "email", weak_email, evidence, method=record.source,
                                     rationale="unverified email from a weak source; not used for contact")

        # 3. Lead: one open lead per person; closed leads handled per policy
        lead, lead_created = await self._lead_for(db, person, record.source, tenant, policy.lead.on_closed_lead)

        # 4. Remember the source record
        await self._upsert_source_record(db, record.source, ext_id, company, person, lead, tenant)

        # 5. Event
        event_type = "lead_ingested" if lead_created else "lead_resourced"
        db.add(Event(
            lead_id=lead.id,
            tenant_id=tenant,
            event_type=event_type,
            source=record.source,
            payload=json.dumps({
                "source": record.source,
                "external_id": record.external_id,
                "lead_id": str(lead.id),
                "person_id": str(person.id) if person else None,
                "company_id": str(company.id) if company else None,
                "company_match": company_res.match_type.value,
                "person_match": person_res.match_type.value,
                "fields_merged": merged,
            }, default=str),
        ))
        await db.flush()

        return IngestionResult(
            lead_id=lead.id,
            person_id=person.id if person else None,
            company_id=company.id if company else None,
            company_created=company_created,
            person_created=person_created,
            lead_created=lead_created,
            event_created=True,
            company_match=company_res.match_type.value,
            person_match=person_res.match_type.value,
            fields_merged=merged,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _insert(
        self, db: AsyncSession, entity: T, resolve_existing: Callable[[], Awaitable[Optional[T]]]
    ) -> tuple[T, bool]:
        """Insert in a savepoint; if a unique index fires (concurrent import), use the winner's row."""
        try:
            async with db.begin_nested():
                db.add(entity)
                await db.flush()
            return entity, True
        except IntegrityError:
            existing = await resolve_existing()
            if existing is None:
                raise
            logger.info("Concurrent insert detected; using existing %s %s", type(existing).__name__, existing.id)
            return existing, False

    async def _resolve_company_entity(self, db, values, record, ext_id, tenant) -> Optional[Company]:
        res = await self.resolver.resolve_company(
            db, values["domain"], record.company_website, linkedin_url=record.company_linkedin_url,
            name=record.company_name, source=record.source, external_id=ext_id, tenant_id=tenant)
        return await db.get(Company, res.company_id) if res.found else None

    async def _resolve_person_entity(self, db, values, company, record, ext_id, tenant) -> Optional[Person]:
        res = await self.resolver.resolve_person(
            db, values["email"], linkedin_url=values["linkedin_url"], first_name=values["first_name"],
            last_name=values["last_name"], company_id=company.id if company else None,
            source=record.source, external_id=ext_id, tenant_id=tenant)
        return await db.get(Person, res.person_id) if res.found else None

    async def _record_facts(self, db, entity, values: dict[str, Any], evidence, source) -> list[str]:
        """Record every incoming value in the evidence ledger; the fact lifecycle decides what is applied."""
        changed: list[str] = []
        for name, value in values.items():
            if value in (None, "") or not self.ledger.writable(type(entity), name):
                continue
            outcome = await self.ledger.record(db, entity, name, value, evidence, method=source)
            if outcome.changed:
                changed.append(name)
        return changed

    async def _lead_for(
        self, db: AsyncSession, person: Person, source: Optional[str], tenant: uuid.UUID, on_closed: str
    ) -> tuple[Lead, bool]:
        async def open_lead() -> Optional[Lead]:
            stmt = (select(Lead).where(Lead.tenant_id == tenant, Lead.person_id == person.id, Lead.is_open)
                    .order_by(Lead.created_at).limit(1))
            return (await db.execute(stmt)).scalar_one_or_none()

        existing = await open_lead()
        if existing is not None:
            return existing, False
        if on_closed == "attach":
            stmt = (select(Lead).where(Lead.tenant_id == tenant, Lead.person_id == person.id)
                    .order_by(Lead.created_at.desc()).limit(1))
            latest = (await db.execute(stmt)).scalar_one_or_none()
            if latest is not None:
                return latest, False
        return await self._insert(db, Lead(tenant_id=tenant, person_id=person.id, source=source, status="new"),
                                  open_lead)

    async def _upsert_source_record(
        self, db: AsyncSession, source: Optional[str], ext_id: Optional[str],
        company: Optional[Company], person: Optional[Person], lead: Lead, tenant: uuid.UUID,
    ) -> None:
        if not source or not ext_id:
            return
        rec = await self.resolver.source_record(db, source, ext_id, tenant)
        now = datetime.now(timezone.utc)
        if rec is None:
            rec, created = await self._insert(
                db,
                SourceRecord(tenant_id=tenant, source=source, external_id=ext_id,
                             company_id=company.id if company else None,
                             person_id=person.id if person else None, lead_id=lead.id),
                lambda: self.resolver.source_record(db, source, ext_id, tenant),
            )
            if created:
                return
        rec.company_id = company.id if company else rec.company_id
        rec.person_id = person.id if person else rec.person_id
        rec.lead_id = lead.id
        rec.times_seen += 1
        rec.last_seen_at = now
