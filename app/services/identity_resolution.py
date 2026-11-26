"""
Identity Resolution Service — the single source of matching logic.

Deterministically matches incoming records to existing Company and Person rows
of the same tenant. Used by ingestion, enrichment and the capability invokers;
no other module implements its own matching.

Match keys (exact, normalised; order and selection come from config
``identity.company_match`` / ``identity.person_match``):

    Company: source_record | domain | website | linkedin_url | name
    Person:  source_record | email  | linkedin_url | name_and_company

``source_record`` resolves a provider's own record id via the source_records
table, so a re-import maps to the same entities even if the email or domain
changed at the source. There is deliberately no fuzzy matching: a wrong merge
would contact the wrong person.

The resolver is read-only; callers decide whether to create.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import DEFAULT_TENANT_ID
from app.models.base import Company, Person, SourceRecord
from app.services.normalization import normalize_domain, normalize_email, normalize_name, normalize_url


class MatchType(str, Enum):
    """How the match was determined."""
    SOURCE_RECORD = "source_record"
    EXACT_DOMAIN = "exact_domain"
    WEBSITE_DOMAIN = "website_domain"
    LINKEDIN_URL = "linkedin_url"
    COMPANY_NAME = "company_name"
    EXACT_EMAIL = "exact_email"
    NAME_AND_COMPANY = "name_and_company"
    NO_MATCH = "no_match"


@dataclass
class CompanyResolutionResult:
    found: bool
    company_id: Optional[uuid.UUID]
    match_type: MatchType
    confidence: float
    normalized_domain: Optional[str]


@dataclass
class PersonResolutionResult:
    found: bool
    person_id: Optional[uuid.UUID]
    match_type: MatchType
    confidence: float
    normalized_email: Optional[str]


def _configured(kind: str, tenant_id: Optional[uuid.UUID]) -> list[str]:
    from app.core.gtm_config import get_gtm_config
    identity = get_gtm_config().for_tenant(tenant_id).identity
    return list(identity.company_match if kind == "company" else identity.person_match)


class IdentityResolver:
    """Stateless; all state lives in the database session."""

    # ── helpers ──────────────────────────────────────────────────────────────
    # Oldest first, so lookups are stable even on non-unique keys (LinkedIn, names).
    @staticmethod
    async def _first_company(db: AsyncSession, *where) -> Optional[Company]:
        stmt = select(Company).where(*where).order_by(Company.created_at, Company.id).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _first_person(db: AsyncSession, *where) -> Optional[Person]:
        stmt = select(Person).where(*where).order_by(Person.created_at, Person.id).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def source_record(
        self, db: AsyncSession, source: Optional[str], external_id: Optional[str],
        tenant_id: Optional[uuid.UUID] = None,
    ) -> Optional[SourceRecord]:
        if not source or not external_id:
            return None
        stmt = select(SourceRecord).where(
            SourceRecord.tenant_id == (tenant_id or DEFAULT_TENANT_ID),
            SourceRecord.source == source,
            SourceRecord.external_id == str(external_id),
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # ── companies ────────────────────────────────────────────────────────────
    async def resolve_company(
        self,
        db: AsyncSession,
        domain: Optional[str] = None,
        website: Optional[str] = None,
        *,
        linkedin_url: Optional[str] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        external_id: Optional[str] = None,
        tenant_id: Optional[uuid.UUID] = None,
        strategy: Optional[Sequence[str]] = None,
    ) -> CompanyResolutionResult:
        tenant = tenant_id or DEFAULT_TENANT_ID
        norm_domain = normalize_domain(domain)
        norm_website = normalize_domain(website) if website else None
        norm_linkedin = normalize_url(linkedin_url)
        norm_name = normalize_name(name)

        def hit(company: Company, match: MatchType, nd: Optional[str]) -> CompanyResolutionResult:
            return CompanyResolutionResult(True, company.id, match, 1.0, nd)

        for key in strategy or _configured("company", tenant_id):
            company: Optional[Company] = None
            if key == "source_record":
                rec = await self.source_record(db, source, external_id, tenant)
                if rec and rec.company_id:
                    company = await db.get(Company, rec.company_id)
                    if company:
                        return hit(company, MatchType.SOURCE_RECORD, norm_domain)
            elif key == "domain" and norm_domain:
                company = await self._first_company(
                    db, Company.tenant_id == tenant, Company.domain == norm_domain)
                if company:
                    return hit(company, MatchType.EXACT_DOMAIN, norm_domain)
            elif key == "website" and norm_website and norm_website != norm_domain:
                company = await self._first_company(
                    db, Company.tenant_id == tenant, Company.domain == norm_website)
                if company:
                    return hit(company, MatchType.WEBSITE_DOMAIN, norm_website)
            elif key == "linkedin_url" and norm_linkedin:
                company = await self._first_company(
                    db, Company.tenant_id == tenant, Company.linkedin_url == norm_linkedin)
                if company:
                    return hit(company, MatchType.LINKEDIN_URL, norm_domain)
            elif key == "name" and norm_name:
                company = await self._first_company(
                    db, Company.tenant_id == tenant, func.lower(Company.name) == norm_name.lower())
                if company:
                    return hit(company, MatchType.COMPANY_NAME, norm_domain)

        return CompanyResolutionResult(False, None, MatchType.NO_MATCH, 0.0, norm_domain or norm_website)

    # ── people ───────────────────────────────────────────────────────────────
    async def resolve_person(
        self,
        db: AsyncSession,
        email: Optional[str],
        *,
        linkedin_url: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        company_id: Optional[uuid.UUID] = None,
        source: Optional[str] = None,
        external_id: Optional[str] = None,
        tenant_id: Optional[uuid.UUID] = None,
        strategy: Optional[Sequence[str]] = None,
    ) -> PersonResolutionResult:
        tenant = tenant_id or DEFAULT_TENANT_ID
        norm_email = normalize_email(email)
        norm_linkedin = normalize_url(linkedin_url)
        first, last = normalize_name(first_name), normalize_name(last_name)

        def hit(person: Person, match: MatchType) -> PersonResolutionResult:
            return PersonResolutionResult(True, person.id, match, 1.0, norm_email)

        for key in strategy or _configured("person", tenant_id):
            person: Optional[Person] = None
            if key == "source_record":
                rec = await self.source_record(db, source, external_id, tenant)
                if rec and rec.person_id:
                    person = await db.get(Person, rec.person_id)
                    if person:
                        return hit(person, MatchType.SOURCE_RECORD)
            elif key == "email" and norm_email:
                person = await self._first_person(
                    db, Person.tenant_id == tenant, Person.email == norm_email)
                if person:
                    return hit(person, MatchType.EXACT_EMAIL)
            elif key == "linkedin_url" and norm_linkedin:
                person = await self._first_person(
                    db, Person.tenant_id == tenant, Person.linkedin_url == norm_linkedin)
                if person:
                    return hit(person, MatchType.LINKEDIN_URL)
            elif key == "name_and_company" and first and last and company_id:
                # Only when both names and the company are known; an email that
                # conflicts with the stored one is not this person.
                person = await self._first_person(
                    db,
                    Person.tenant_id == tenant,
                    Person.company_id == company_id,
                    func.lower(Person.first_name) == first.lower(),
                    func.lower(Person.last_name) == last.lower(),
                )
                if person and (not norm_email or not person.email or person.email == norm_email):
                    return hit(person, MatchType.NAME_AND_COMPANY)

        return PersonResolutionResult(False, None, MatchType.NO_MATCH, 0.0, norm_email)
