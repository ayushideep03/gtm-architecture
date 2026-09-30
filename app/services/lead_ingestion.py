"""
Lead Ingestion Service.

Converts normalized LeadSourceRecord objects into persistent database entities
following the flow:

    LeadSourceRecord
         ↓
    normalize()
         ↓
    find_or_create_company()   ← dedup by normalized domain
         ↓
    find_or_create_person()    ← dedup by normalized email
         ↓
    create_lead()              ← always creates a new Lead per ingestion
         ↓
    emit_event()               ← lead_ingested event (append-only)

Deduplication rules (deterministic, no LLM):
    Company: normalized domain (if present)
    Person:  normalized email  (if present)

If a company has no domain, a new Company is always created.
If a person has no email, a new Person is always created.

Lead identity rule:
    A Lead is created for each successfully ingested record.
    Multiple Leads may exist for the same Person (from different providers,
    different time periods, etc.). This is intentional — Lead represents
    a sales opportunity, not just a contact.

    Rationale: We cannot determine without LLM/business logic whether two
    ingestion events represent the same "opportunity". That is the job of
    the Oxygen orchestrator (Step 3+). The ingestion layer only persists
    raw data cleanly.

Event type used: "lead_ingested"
    (added via Alembic migration in this step)
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.lead_source_record import LeadSourceRecord
from app.models.base import Company, Event, Lead, Person
from app.services.normalization import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_url,
)

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class IngestionResult:
    """Summary of what happened during a single record ingestion."""
    lead_id: uuid.UUID
    person_id: Optional[uuid.UUID]
    company_id: Optional[uuid.UUID]
    company_created: bool = False
    person_created: bool = False
    lead_created: bool = True
    event_created: bool = False


@dataclass
class BatchIngestionSummary:
    """Aggregated summary for POST /leads/ingest response."""
    provider: str
    records_received: int = 0
    companies_created: int = 0
    companies_existing: int = 0
    people_created: int = 0
    people_existing: int = 0
    leads_created: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)

    def record(self, result: IngestionResult) -> None:
        if result.company_created:
            self.companies_created += 1
        else:
            self.companies_existing += (1 if result.company_id else 0)
        if result.person_created:
            self.people_created += 1
        else:
            self.people_existing += (1 if result.person_id else 0)
        if result.lead_created:
            self.leads_created += 1
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
            "events_created": self.events_created,
            "errors": self.errors,
        }


# ── Service ───────────────────────────────────────────────────────────────────

class LeadIngestionService:
    """
    Converts normalized provider records into database entities.

    Stateless — all state lives in the database session.
    One instance can be reused across requests.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    async def ingest_batch(
        self,
        records: list[LeadSourceRecord],
        db: AsyncSession,
        provider_name: str,
    ) -> BatchIngestionSummary:
        """
        Ingest a batch of normalized records from a single provider.

        Processes records sequentially to avoid race conditions during
        within-batch deduplication. Each record is either fully persisted
        or its error is recorded in the summary.

        Args:
            records:       Normalized records from a provider.
            db:            Active async session (caller manages commit/rollback).
            provider_name: The canonical provider name (for logging).

        Returns:
            BatchIngestionSummary with counts per entity type.
        """
        summary = BatchIngestionSummary(
            provider=provider_name,
            records_received=len(records),
        )

        for record in records:
            try:
                result = await self._ingest_one(record, db)
                summary.record(result)
            except Exception as exc:  # noqa: BLE001
                err_msg = f"Record {record.external_id!r}: {exc}"
                logger.error("Ingestion error — %s", err_msg)
                summary.errors.append(err_msg)

        logger.info(
            "Ingestion complete [%s]: %d records, %d companies, %d people, %d leads",
            provider_name,
            summary.records_received,
            summary.companies_created + summary.companies_existing,
            summary.people_created + summary.people_existing,
            summary.leads_created,
        )
        return summary

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _ingest_one(
        self,
        record: LeadSourceRecord,
        db: AsyncSession,
    ) -> IngestionResult:
        """Process a single LeadSourceRecord into the database."""

        # 1. Normalize all fields up front
        norm_email = normalize_email(record.email)
        norm_domain = normalize_domain(record.company_domain)
        norm_first = normalize_name(record.effective_first_name())
        norm_last = normalize_name(record.effective_last_name())
        norm_phone = normalize_phone(record.phone)
        norm_linkedin = normalize_url(record.linkedin_url)
        norm_co_linkedin = normalize_url(record.company_linkedin_url)
        norm_website = normalize_url(record.company_website)

        # 2. Find or create Company
        company, company_created = await self._find_or_create_company(
            db=db,
            name=normalize_name(record.company_name),
            domain=norm_domain,
            linkedin_url=norm_co_linkedin,
            website=norm_website,
            industry=record.industry,
            employee_count=record.employee_count,
        )

        # 3. Find or create Person
        person, person_created = await self._find_or_create_person(
            db=db,
            first_name=norm_first or "Unknown",
            last_name=norm_last,
            email=norm_email,
            phone=norm_phone,
            title=normalize_name(record.job_title),
            linkedin_url=norm_linkedin,
            company_id=company.id if company else None,
        )

        # 4. Create Lead (always — represents a new opportunity signal)
        lead = Lead(
            person_id=person.id if person else None,
            source=record.source,
            status="new",
        )
        db.add(lead)
        await db.flush()  # get the lead.id before creating the event

        # 5. Emit append-only event
        event = Event(
            lead_id=lead.id,
            event_type="lead_ingested",
            source=record.source,
            payload=json.dumps(
                {
                    "source": record.source,
                    "external_id": record.external_id,
                    "lead_id": str(lead.id),
                    "person_id": str(person.id) if person else None,
                    "company_id": str(company.id) if company else None,
                },
                default=str,
            ),
        )
        db.add(event)
        await db.flush()

        logger.debug(
            "Ingested record [%s/%s] -> lead=%s person=%s company=%s",
            record.source,
            record.external_id,
            lead.id,
            person.id if person else None,
            company.id if company else None,
        )

        return IngestionResult(
            lead_id=lead.id,
            person_id=person.id if person else None,
            company_id=company.id if company else None,
            company_created=company_created,
            person_created=person_created,
            lead_created=True,
            event_created=True,
        )

    async def _find_or_create_company(
        self,
        db: AsyncSession,
        name: Optional[str],
        domain: Optional[str],
        linkedin_url: Optional[str],
        website: Optional[str],
        industry: Optional[str],
        employee_count: Optional[int],
    ) -> tuple[Optional[Company], bool]:
        """
        Return (company, created).

        Deduplication key: normalized domain.
        If domain is None or blank, always creates a new Company.
        """
        if domain:
            stmt = select(Company).where(Company.domain == domain)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing:
                return existing, False

        if not name:
            # No name and no domain — cannot create a meaningful Company
            return None, False

        company = Company(
            name=name,
            domain=domain,
            linkedin_url=linkedin_url,
            website=website,
            industry=industry,
            employee_count=employee_count,
        )
        db.add(company)
        await db.flush()  # get company.id
        return company, True

    async def _find_or_create_person(
        self,
        db: AsyncSession,
        first_name: str,
        last_name: Optional[str],
        email: Optional[str],
        phone: Optional[str],
        title: Optional[str],
        linkedin_url: Optional[str],
        company_id: Optional[uuid.UUID],
    ) -> tuple[Optional[Person], bool]:
        """
        Return (person, created).

        Deduplication key: normalized email.
        If email is None or blank, always creates a new Person.
        """
        if email:
            stmt = select(Person).where(Person.email == email)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing:
                return existing, False

        person = Person(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            title=title,
            linkedin_url=linkedin_url,
            company_id=company_id,
        )
        db.add(person)
        await db.flush()  # get person.id
        return person, True
