"""
Identity Resolution Service.

Deterministically matches incoming enrichment targets to existing
Company and Person records in the database.

Matching strategy (no fuzzy matching):
    Company:
        1. Exact match on normalized domain
        2. No match → caller decides whether to create

    Person:
        1. Exact match on normalized email
        2. No match → caller decides whether to create

The resolver returns an explicit ResolutionResult describing:
    - whether a match was found
    - the matched entity ID (if found)
    - the match type / reason
    - a confidence score (1.0 for exact, 0.0 for no match)

Design:
    - Pure read-only: never creates or modifies records.
    - Async: uses the caller's session.
    - No LLM, no fuzzy matching, no silent merges.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Company, Person
from app.services.normalization import normalize_domain, normalize_email


# ── Match types ───────────────────────────────────────────────────────────────

class MatchType(str, Enum):
    """How the match was determined."""
    EXACT_DOMAIN = "exact_domain"
    WEBSITE_DOMAIN = "website_domain"
    EXACT_EMAIL = "exact_email"
    NO_MATCH = "no_match"


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CompanyResolutionResult:
    """
    Result of attempting to resolve a Company by domain.

    Attributes:
        found:      True if an existing Company was matched.
        company_id: UUID of the matched Company (None if not found).
        match_type: How the match was determined.
        confidence: 1.0 for exact match, 0.0 for no match.
        normalized_domain: The normalized domain that was queried.
    """
    found: bool
    company_id: Optional[uuid.UUID]
    match_type: MatchType
    confidence: float
    normalized_domain: Optional[str]


@dataclass
class PersonResolutionResult:
    """
    Result of attempting to resolve a Person by email.

    Attributes:
        found:            True if an existing Person was matched.
        person_id:        UUID of the matched Person (None if not found).
        match_type:       How the match was determined.
        confidence:       1.0 for exact match, 0.0 for no match.
        normalized_email: The normalized email that was queried.
    """
    found: bool
    person_id: Optional[uuid.UUID]
    match_type: MatchType
    confidence: float
    normalized_email: Optional[str]


# ── Service ───────────────────────────────────────────────────────────────────

class IdentityResolver:
    """
    Resolve Company and Person identities against the database.

    Stateless — all state lives in the database session.
    One instance can be shared across requests.
    """

    async def resolve_company(
        self,
        db: AsyncSession,
        domain: Optional[str] = None,
        website: Optional[str] = None,
    ) -> CompanyResolutionResult:
        """
        Attempt to resolve a Company by its normalized domain or website.

        Matching priority:
            1. normalized domain
            2. normalized website domain if available
            3. otherwise no automatic company match

        Args:
            db:      Active async session.
            domain:  Raw or pre-normalized domain string.
            website: Optional raw or pre-normalized website URL / domain.

        Returns:
            CompanyResolutionResult with found=True and company_id set
            if a match exists; found=False otherwise.
        """
        # 1. Match on normalized domain
        normalized_domain = normalize_domain(domain)
        if normalized_domain:
            stmt = select(Company).where(Company.domain == normalized_domain)
            result = await db.execute(stmt)
            company = result.scalar_one_or_none()
            if company is not None:
                return CompanyResolutionResult(
                    found=True,
                    company_id=company.id,
                    match_type=MatchType.EXACT_DOMAIN,
                    confidence=1.0,
                    normalized_domain=normalized_domain,
                )

        # 2. Match on normalized website domain if available
        normalized_website = normalize_domain(website) if website else None
        if normalized_website and normalized_website != normalized_domain:
            stmt = select(Company).where(Company.domain == normalized_website)
            result = await db.execute(stmt)
            company = result.scalar_one_or_none()
            if company is not None:
                return CompanyResolutionResult(
                    found=True,
                    company_id=company.id,
                    match_type=MatchType.WEBSITE_DOMAIN,
                    confidence=1.0,
                    normalized_domain=normalized_website,
                )

        return CompanyResolutionResult(
            found=False,
            company_id=None,
            match_type=MatchType.NO_MATCH,
            confidence=0.0,
            normalized_domain=normalized_domain or normalized_website,
        )

    async def resolve_person(
        self,
        db: AsyncSession,
        email: Optional[str],
    ) -> PersonResolutionResult:
        """
        Attempt to resolve a Person by their normalized email.

        Args:
            db:    Active async session.
            email: Raw or pre-normalized email address.

        Returns:
            PersonResolutionResult with found=True and person_id set
            if a match exists; found=False otherwise.
        """
        normalized = normalize_email(email)

        if not normalized:
            return PersonResolutionResult(
                found=False,
                person_id=None,
                match_type=MatchType.NO_MATCH,
                confidence=0.0,
                normalized_email=None,
            )

        stmt = select(Person).where(Person.email == normalized)
        result = await db.execute(stmt)
        person = result.scalar_one_or_none()

        if person is not None:
            return PersonResolutionResult(
                found=True,
                person_id=person.id,
                match_type=MatchType.EXACT_EMAIL,
                confidence=1.0,
                normalized_email=normalized,
            )

        return PersonResolutionResult(
            found=False,
            person_id=None,
            match_type=MatchType.NO_MATCH,
            confidence=0.0,
            normalized_email=normalized,
        )
