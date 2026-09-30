"""
Mock enrichment providers for development and testing.

Returns deterministic, realistic-looking but entirely fake enrichment data.
No real API calls are made. No internet access required.

Fixtures are keyed by normalized domain (company) or email (person).
An unknown key returns found=False.

IMPORTANT: All data here is fictional. Do not use real people's information.
"""

from app.integrations.enrichment_provider import (
    CompanyEnrichmentProvider,
    PersonEnrichmentProvider,
)
from app.integrations.enrichment_record import (
    CompanyEnrichmentData,
    CompanyEnrichmentResult,
    PersonEnrichmentData,
    PersonEnrichmentResult,
)

# ── Company fixtures ──────────────────────────────────────────────────────────
# Keyed by normalized domain. Same domains as the lead-source mocks so that
# integration tests can enrich records that were previously ingested.

_COMPANY_FIXTURES: dict[str, dict] = {
    "quantum.io": {
        "external_id": "enrich_co_001",
        "name": "Quantum Dynamics",
        "domain": "quantum.io",
        "industry": "Software",
        "employee_count": 280,
        "country": "United States",
        "city": "San Francisco",
        "website": "https://quantum.io",
        "linkedin_url": "https://linkedin.com/company/quantum-dynamics-fake",
        "description": "Quantum-safe cryptography platform for enterprise.",
        "technologies": ["Python", "Rust", "PostgreSQL", "Kubernetes"],
        "funding_stage": "Series B",
        "revenue_range": "$10M-$50M",
    },
    "horizon.ai": {
        "external_id": "enrich_co_002",
        "name": "Horizon AI",
        "domain": "horizon.ai",
        "industry": "Artificial Intelligence",
        "employee_count": 95,
        "country": "United States",
        "city": "Austin",
        "website": "https://horizon.ai",
        "linkedin_url": "https://linkedin.com/company/horizon-ai-fake",
        "description": "AI-native revenue intelligence platform.",
        "technologies": ["Python", "PyTorch", "React", "AWS"],
        "funding_stage": "Series A",
        "revenue_range": "$1M-$10M",
    },
    "synthwave.dev": {
        "external_id": "enrich_co_003",
        "name": "Synthwave Labs",
        "domain": "synthwave.dev",
        "industry": "Developer Tools",
        "employee_count": 40,
        "country": "United States",
        "city": "New York",
        "website": "https://synthwave.dev",
        "linkedin_url": "https://linkedin.com/company/synthwave-labs-fake",
        "description": "Developer tooling for AI-native applications.",
        "technologies": ["TypeScript", "Go", "Redis", "Docker"],
        "funding_stage": "Seed",
        "revenue_range": "<$1M",
    },
}

# ── Person fixtures ───────────────────────────────────────────────────────────
# Keyed by normalized email.

_PERSON_FIXTURES: dict[str, dict] = {
    "alice.chen@quantum.io": {
        "external_id": "enrich_p_001",
        "first_name": "Alice",
        "last_name": "Chen",
        "email": "alice.chen@quantum.io",
        "phone": "+1-555-0101",
        "job_title": "VP of Engineering",
        "linkedin_url": "https://linkedin.com/in/alice-chen-fake",
        "seniority": "vp",
        "department": "Engineering",
        "location": "San Francisco, CA",
    },
    "m.rivera@horizon.ai": {
        "external_id": "enrich_p_002",
        "first_name": "Marcus",
        "last_name": "Rivera",
        "email": "m.rivera@horizon.ai",
        "phone": "+1-555-0202",
        "job_title": "CTO",
        "linkedin_url": "https://linkedin.com/in/marcus-rivera-fake",
        "seniority": "c_suite",
        "department": "Engineering",
        "location": "Austin, TX",
    },
    "priya.nair@synthwave.dev": {
        "external_id": "enrich_p_003",
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya.nair@synthwave.dev",
        "phone": "+1-555-0303",
        "job_title": "Head of Growth",
        "linkedin_url": "https://linkedin.com/in/priya-nair-fake",
        "seniority": "manager",
        "department": "Marketing",
        "location": "New York, NY",
    },
}


class MockCompanyEnrichmentProvider(CompanyEnrichmentProvider):
    """
    Deterministic mock company enrichment provider.

    Returns richer data for known fixture domains.
    Returns found=False for unknown domains.
    """

    @property
    def provider_name(self) -> str:
        return "mock_enrichment"

    async def enrich_company(self, domain: str) -> CompanyEnrichmentResult:
        fixture = _COMPANY_FIXTURES.get(domain.lower().strip())
        if fixture is None:
            return CompanyEnrichmentResult(
                found=False,
                domain=domain,
                source=self.provider_name,
            )
        data = CompanyEnrichmentData(
            name=fixture.get("name"),
            domain=fixture.get("domain"),
            industry=fixture.get("industry"),
            employee_count=fixture.get("employee_count"),
            country=fixture.get("country"),
            city=fixture.get("city"),
            website=fixture.get("website"),
            linkedin_url=fixture.get("linkedin_url"),
            description=fixture.get("description"),
            technologies=fixture.get("technologies", []),
            funding_stage=fixture.get("funding_stage"),
            revenue_range=fixture.get("revenue_range"),
        )
        return CompanyEnrichmentResult(
            found=True,
            domain=domain,
            source=self.provider_name,
            data=data,
            external_id=fixture.get("external_id"),
        )


class MockPersonEnrichmentProvider(PersonEnrichmentProvider):
    """
    Deterministic mock person enrichment provider.

    Returns richer data for known fixture emails.
    Returns found=False for unknown emails.
    """

    @property
    def provider_name(self) -> str:
        return "mock_enrichment"

    async def enrich_person(self, email: str) -> PersonEnrichmentResult:
        fixture = _PERSON_FIXTURES.get(email.lower().strip())
        if fixture is None:
            return PersonEnrichmentResult(
                found=False,
                email=email,
                source=self.provider_name,
            )
        data = PersonEnrichmentData(
            first_name=fixture.get("first_name"),
            last_name=fixture.get("last_name"),
            email=fixture.get("email"),
            phone=fixture.get("phone"),
            job_title=fixture.get("job_title"),
            linkedin_url=fixture.get("linkedin_url"),
            seniority=fixture.get("seniority"),
            department=fixture.get("department"),
            location=fixture.get("location"),
        )
        return PersonEnrichmentResult(
            found=True,
            email=email,
            source=self.provider_name,
            data=data,
            external_id=fixture.get("external_id"),
        )
