"""
Enrichment Service.

Orchestrates the full enrichment flow for Company and Person entities:

    1. Identity resolution  — find the existing DB record
    2. Provider call        — fetch structured enrichment data
    3. Apply changes        — update the ORM entity (idempotent)
    4. Record what changed  — diff old vs new values
    5. Emit event           — company_enriched / person_enriched / enrichment_failed

Safety guarantees:
    - Safe to run repeatedly: repeated enrichment updates existing entities,
      never creates duplicates.
    - Does NOT create new Leads when enrichment runs.
    - Events are append-only.

Usage::

    svc = EnrichmentService()
    # provider chosen by the ProviderSelector (config, health, reliability, cost)
    result = await svc.enrich_company_by_id(db=session, company_id=uuid)
    # or pin a configured provider explicitly
    result = await svc.enrich_company_by_id(db=session, company_id=uuid, provider_name="vendor_x")
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.enrichment_provider import EnrichmentProviderError
from app.integrations.enrichment_record import (
    CompanyEnrichmentData,
    CompanyEnrichmentResult,
    PersonEnrichmentData,
    PersonEnrichmentResult,
)
from app.integrations.enrichment_registry import enrichment_registry
from app.models.base import Company, Event, Person
from app.services.normalization import normalize_domain, normalize_email

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class EnrichmentOutcome:
    """Result of a single enrichment run."""

    entity_type: str              # "company" or "person"
    entity_id: uuid.UUID
    provider: str
    found: bool                   # provider had data
    fields_updated: list[str] = field(default_factory=list)
    event_id: Optional[uuid.UUID] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


def _provider_label(provider: Union[str, Any, None]) -> str:
    if provider is None:
        return "auto"
    if isinstance(provider, str):
        return provider
    return getattr(provider, "provider_name", type(provider).__name__)


# ── Service ───────────────────────────────────────────────────────────────────

class EnrichmentService:
    """
    Coordinates identity resolution, provider calls, and persistence
    for Company and Person enrichment.

    Stateless — all state lives in the database session.
    """

    # ── Provider resolution ───────────────────────────────────────────────────

    async def _resolve_provider(
        self,
        capability: str,
        provider: Union[str, Any, None],
        db: AsyncSession,
        entity: Any,
    ) -> tuple[Any, str]:
        """
        Resolve which enrichment provider to call.

        - instance  -> used as-is (e.g. chosen by the TaskExecutor's selector)
        - name      -> looked up in the unified provider registry
        - None      -> ProviderSelector picks the best available provider
        """
        if provider is not None and not isinstance(provider, str):
            return provider, _provider_label(provider)
        if isinstance(provider, str):
            if capability == "enrich_company":
                return enrichment_registry.get_company_provider(provider), provider
            return enrichment_registry.get_person_provider(provider), provider

        from app.core.tenancy import tenant_of
        from app.providers import get_provider_selector
        from app.providers.selection import SelectionRequest

        selector = get_provider_selector()
        candidate = await selector.select(
            SelectionRequest(capability=capability, tenant_id=tenant_of(entity)), session=db
        )
        if candidate is None:
            raise EnrichmentProviderError(
                "auto", f"No available provider for capability {capability!r}"
            )
        return candidate.record.instance, candidate.record.name

    # ── Company enrichment ────────────────────────────────────────────────────

    async def enrich_company_by_id(
        self,
        db: AsyncSession,
        company_id: uuid.UUID,
        provider_name: Union[str, Any, None] = None,
    ) -> EnrichmentOutcome:
        """
        Enrich a Company record identified by its database UUID.

        Args:
            db:            Active async session (caller manages commit).
            company_id:    UUID of the Company to enrich.
            provider_name: Configured provider name, a provider instance, or None
                           to let the ProviderSelector choose.

        Returns:
            EnrichmentOutcome describing what happened.
        """
        # 1. Load the company
        result = await db.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()

        if company is None:
            return EnrichmentOutcome(
                entity_type="company",
                entity_id=company_id,
                provider=_provider_label(provider_name),
                found=False,
                error=f"Company {company_id} not found in database",
            )

        if not company.domain:
            return EnrichmentOutcome(
                entity_type="company",
                entity_id=company_id,
                provider=_provider_label(provider_name),
                found=False,
                error="Company has no domain — cannot enrich without a domain key",
            )

        return await self._enrich_company(db, company, provider_name)

    async def _enrich_company(
        self,
        db: AsyncSession,
        company: Company,
        provider_name: Union[str, Any, None],
    ) -> EnrichmentOutcome:
        """Internal: call provider, apply data, emit event."""
        domain = normalize_domain(company.domain) or company.domain

        try:
            provider, provider_name = await self._resolve_provider(
                "enrich_company", provider_name, db, company
            )
            enrichment: CompanyEnrichmentResult = await provider.enrich_company(domain)
        except EnrichmentProviderError as exc:
            logger.error("Enrichment provider error [%s]: %s", provider_name, exc)
            event = await self._emit_event(
                db,
                event_type="enrichment_failed",
                source=_provider_label(provider_name),
                payload={
                    "entity_type": "company",
                    "entity_id": str(company.id),
                    "domain": domain,
                    "error": str(exc),
                },
            )
            return EnrichmentOutcome(
                entity_type="company",
                entity_id=company.id,
                provider=_provider_label(provider_name),
                found=False,
                event_id=event.id,
                error=str(exc),
            )

        if not enrichment.found or enrichment.data is None:
            logger.info(
                "Enrichment provider %r returned no data for domain %r",
                provider_name,
                domain,
            )
            event = await self._emit_event(
                db,
                event_type="enrichment_failed",
                source=_provider_label(provider_name),
                payload={
                    "entity_type": "company",
                    "entity_id": str(company.id),
                    "domain": domain,
                    "reason": "provider returned no data",
                },
            )
            return EnrichmentOutcome(
                entity_type="company",
                entity_id=company.id,
                provider=_provider_label(provider_name),
                found=False,
                event_id=event.id,
            )

        # 2. Apply changes and track what changed
        fields_updated = await self._apply_company_data(db, company, enrichment.data, provider_name)

        # 3. Emit event
        event = await self._emit_event(
            db,
            event_type="company_enriched",
            source=provider_name,
            payload={
                "entity_type": "company",
                "entity_id": str(company.id),
                "domain": domain,
                "provider": provider_name,
                "external_id": enrichment.external_id,
                "fields_updated": fields_updated,
                "enriched_at": company.enriched_at.isoformat() if company.enriched_at else None,
            },
        )

        logger.info(
            "Company enriched [%s] domain=%r provider=%r fields=%s",
            company.id,
            domain,
            provider_name,
            fields_updated,
        )

        return EnrichmentOutcome(
            entity_type="company",
            entity_id=company.id,
            provider=provider_name,
            found=True,
            fields_updated=fields_updated,
            event_id=event.id,
        )

    async def _apply_company_data(
        self,
        db: AsyncSession,
        company: Company,
        data: CompanyEnrichmentData,
        provider_name: str,
    ) -> list[str]:
        """Record vendor values in the evidence ledger; the fact lifecycle applies them. Returns changed fields."""
        values = {
            "industry": data.industry, "employee_count": data.employee_count, "country": data.country,
            "city": data.city, "website": data.website, "linkedin_url": data.linkedin_url,
            "description": data.description, "funding_stage": data.funding_stage,
            "revenue_range": data.revenue_range, "technologies": data.technologies or None,
        }
        updated = await self._record_vendor_facts(db, company, values, provider_name)
        company.enriched_at = datetime.now(timezone.utc)
        company.enrichment_source = provider_name
        return updated

    async def _record_vendor_facts(self, db: AsyncSession, entity, values: dict, provider_name: str) -> list[str]:
        from app.memory.facts import EvidenceInput, FactLedger
        ledger = FactLedger()
        evidence = [EvidenceInput(kind="vendor_enrichment", detail=f"{provider_name} enrichment",
                                  source_ref=provider_name)]
        updated: list[str] = []
        for name, value in values.items():
            if value in (None, "", []):
                continue
            outcome = await ledger.record(db, entity, name, value, evidence, method=provider_name)
            if outcome.changed:
                updated.append(name)
        return updated

    # ── Person enrichment ─────────────────────────────────────────────────────

    async def enrich_person_by_id(
        self,
        db: AsyncSession,
        person_id: uuid.UUID,
        provider_name: Union[str, Any, None] = None,
    ) -> EnrichmentOutcome:
        """
        Enrich a Person record identified by its database UUID.

        Args:
            db:            Active async session (caller manages commit).
            person_id:     UUID of the Person to enrich.
            provider_name: Configured provider name, a provider instance, or None
                           to let the ProviderSelector choose.

        Returns:
            EnrichmentOutcome describing what happened.
        """
        result = await db.execute(select(Person).where(Person.id == person_id))
        person = result.scalar_one_or_none()

        if person is None:
            return EnrichmentOutcome(
                entity_type="person",
                entity_id=person_id,
                provider=_provider_label(provider_name),
                found=False,
                error=f"Person {person_id} not found in database",
            )

        if not person.email:
            return EnrichmentOutcome(
                entity_type="person",
                entity_id=person_id,
                provider=_provider_label(provider_name),
                found=False,
                error="Person has no email — cannot enrich without an email key",
            )

        return await self._enrich_person(db, person, provider_name)

    async def _enrich_person(
        self,
        db: AsyncSession,
        person: Person,
        provider_name: Union[str, Any, None],
    ) -> EnrichmentOutcome:
        """Internal: call provider, apply data, emit event."""
        email = normalize_email(person.email) or person.email

        try:
            provider, provider_name = await self._resolve_provider(
                "enrich_person", provider_name, db, person
            )
            enrichment: PersonEnrichmentResult = await provider.enrich_person(email)
        except EnrichmentProviderError as exc:
            logger.error("Enrichment provider error [%s]: %s", provider_name, exc)
            event = await self._emit_event(
                db,
                event_type="enrichment_failed",
                source=_provider_label(provider_name),
                payload={
                    "entity_type": "person",
                    "entity_id": str(person.id),
                    "email": email,
                    "error": str(exc),
                },
            )
            return EnrichmentOutcome(
                entity_type="person",
                entity_id=person.id,
                provider=_provider_label(provider_name),
                found=False,
                event_id=event.id,
                error=str(exc),
            )

        if not enrichment.found or enrichment.data is None:
            logger.info(
                "Enrichment provider %r returned no data for email %r",
                provider_name,
                email,
            )
            event = await self._emit_event(
                db,
                event_type="enrichment_failed",
                source=_provider_label(provider_name),
                payload={
                    "entity_type": "person",
                    "entity_id": str(person.id),
                    "email": email,
                    "reason": "provider returned no data",
                },
            )
            return EnrichmentOutcome(
                entity_type="person",
                entity_id=person.id,
                provider=_provider_label(provider_name),
                found=False,
                event_id=event.id,
            )

        # Apply changes
        fields_updated = await self._apply_person_data(db, person, enrichment.data, provider_name)

        # Emit event
        event = await self._emit_event(
            db,
            event_type="person_enriched",
            source=provider_name,
            payload={
                "entity_type": "person",
                "entity_id": str(person.id),
                "email": email,
                "provider": provider_name,
                "external_id": enrichment.external_id,
                "fields_updated": fields_updated,
                "enriched_at": person.enriched_at.isoformat() if person.enriched_at else None,
            },
        )

        logger.info(
            "Person enriched [%s] email=%r provider=%r fields=%s",
            person.id,
            email,
            provider_name,
            fields_updated,
        )

        return EnrichmentOutcome(
            entity_type="person",
            entity_id=person.id,
            provider=provider_name,
            found=True,
            fields_updated=fields_updated,
            event_id=event.id,
        )

    async def _apply_person_data(
        self,
        db: AsyncSession,
        person: Person,
        data: PersonEnrichmentData,
        provider_name: str,
    ) -> list[str]:
        """Record vendor values in the evidence ledger; the fact lifecycle applies them. Returns changed fields."""
        values = {
            "phone": data.phone, "title": data.job_title, "linkedin_url": data.linkedin_url,
            "seniority": data.seniority, "department": data.department, "location": data.location,
        }
        updated = await self._record_vendor_facts(db, person, values, provider_name)
        person.enriched_at = datetime.now(timezone.utc)
        person.enrichment_source = provider_name
        return updated

    # ── Lead-level enrichment ─────────────────────────────────────────────────

    async def enrich_lead(
        self,
        db: AsyncSession,
        lead_id: uuid.UUID,
        provider_name: Union[str, Any, None] = None,
    ) -> dict[str, EnrichmentOutcome]:
        """
        Enrich the Company and Person associated with a Lead.

        Triggers both company and person enrichment. Does NOT create
        a new Lead. Returns a dict with "company" and "person" outcomes.

        Args:
            db:            Active async session (caller manages commit).
            lead_id:       UUID of the Lead whose entities to enrich.
            provider_name: Configured provider name, a provider instance, or None
                           to let the ProviderSelector choose.
        """
        from app.models.base import Lead  # local import avoids circular

        result = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()

        outcomes: dict[str, Optional[EnrichmentOutcome]] = {
            "company": None,
            "person": None,
        }

        if lead is None:
            return outcomes  # type: ignore[return-value]

        if lead.person_id:
            person_result = await db.execute(
                select(Person).where(Person.id == lead.person_id)
            )
            person = person_result.scalar_one_or_none()
            if person is not None:
                outcomes["person"] = await self._enrich_person(db, person, provider_name)
                # Enrich person's company too if linked
                if person.company_id:
                    co_result = await db.execute(
                        select(Company).where(Company.id == person.company_id)
                    )
                    company = co_result.scalar_one_or_none()
                    if company is not None:
                        outcomes["company"] = await self._enrich_company(
                            db, company, provider_name
                        )

        # Emit enrichment_completed event
        await self._emit_event(
            db,
            event_type="enrichment_completed",
            source=provider_name,
            payload={
                "lead_id": str(lead.id),
                "provider": provider_name,
                "company_enriched": outcomes["company"].found if outcomes["company"] else False,
                "person_enriched": outcomes["person"].found if outcomes["person"] else False,
            },
            lead_id=lead.id,
        )

        return outcomes  # type: ignore[return-value]

    # ── Event helpers ─────────────────────────────────────────────────────────

    async def _emit_event(
        self,
        db: AsyncSession,
        event_type: str,
        source: str,
        payload: dict,
        lead_id: Optional[uuid.UUID] = None,
    ) -> Event:
        """Append an enrichment event. Always committed with the parent session."""
        event = Event(
            lead_id=lead_id,
            event_type=event_type,
            source=source,
            payload=json.dumps(payload, default=str),
        )
        db.add(event)
        await db.flush()  # get event.id
        return event
