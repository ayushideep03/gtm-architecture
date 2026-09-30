"""
Mock Scout lead source provider.

Returns deterministic, realistic-looking but entirely fake data.
No real API calls are made.

Intentional overlaps with MockApolloProvider:
- "quantum.io" appears in both providers (DIFFERENT person → tests company dedup)
- "horizon.ai" appears in both providers with the SAME email (m.rivera@horizon.ai)
  → this person should NOT be created twice regardless of which provider ran first.
- "nova.tech" is unique to Scout.

This setup exercises all deduplication paths:
  Case A: Same domain, different person  → new Person, existing Company
  Case B: Same domain, same email person → existing Company, existing Person
  Case C: Unique domain + person         → new Company, new Person
"""

from datetime import datetime, timezone

from app.integrations.lead_source import LeadSourceProvider
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord

_MOCK_RECORDS: list[dict] = [
    {
        # Case A: same company (quantum.io) as Apollo, but different person
        "external_id": "scout_001",
        "first_name": "Ben",
        "last_name": "Okafor",
        "email": "ben.okafor@quantum.io",
        "phone": "+1-555-0401",
        "job_title": "Director of Sales",
        "linkedin_url": "https://linkedin.com/in/ben-okafor-fake",
        "company_name": "Quantum Dynamics",
        "company_domain": "quantum.io",
        "company_linkedin_url": "https://linkedin.com/company/quantum-dynamics-fake",
        "industry": "Software",
        "employee_count": 250,
        "location": "San Francisco, CA",
    },
    {
        # Case B: same company AND same email as Apollo apollo_002
        # A second ingestion of this record must NOT create a new Person or Company.
        "external_id": "scout_002",
        "first_name": "Marcus",
        "last_name": "Rivera",
        "email": "m.rivera@horizon.ai",
        "phone": "+1-555-0202",  # same phone — confirming it's the same person
        "job_title": "CTO",
        "linkedin_url": "https://linkedin.com/in/marcus-rivera-fake",
        "company_name": "Horizon AI",
        "company_domain": "horizon.ai",
        "industry": "Artificial Intelligence",
        "employee_count": 80,
        "location": "Austin, TX",
    },
    {
        # Case C: completely unique to Scout
        "external_id": "scout_003",
        "first_name": "Sofia",
        "last_name": "Andersen",
        "email": "sofia@nova.tech",
        "phone": "+1-555-0501",
        "job_title": "CEO",
        "linkedin_url": "https://linkedin.com/in/sofia-andersen-fake",
        "company_name": "Nova Technologies",
        "company_domain": "nova.tech",
        "industry": "SaaS",
        "employee_count": 120,
        "location": "London, UK",
    },
]


class MockScoutProvider(LeadSourceProvider):
    """
    Deterministic mock of a Scout-style lead source.

    Overlaps with MockApolloProvider to test deduplication:
    - quantum.io appears in both (different person)
    - horizon.ai + m.rivera@horizon.ai appears in both (same person)
    """

    @property
    def provider_name(self) -> str:
        return "mock_scout"

    async def search_leads(self, criteria: LeadSearchCriteria) -> list[LeadSourceRecord]:
        results = list(_MOCK_RECORDS)

        # Filter by company_domain if supplied
        if criteria.company_domain:
            normalized = criteria.company_domain.lower().strip()
            results = [r for r in results if r.get("company_domain", "").lower() == normalized]

        # Apply limit
        results = results[: criteria.limit]

        return [
            LeadSourceRecord(
                source=self.provider_name,
                external_id=r["external_id"],
                first_name=r.get("first_name"),
                last_name=r.get("last_name"),
                email=r.get("email"),
                phone=r.get("phone"),
                job_title=r.get("job_title"),
                linkedin_url=r.get("linkedin_url"),
                company_name=r.get("company_name"),
                company_domain=r.get("company_domain"),
                company_linkedin_url=r.get("company_linkedin_url"),
                industry=r.get("industry"),
                employee_count=r.get("employee_count"),
                location=r.get("location"),
                raw_data=r,
                collected_at=datetime.now(timezone.utc),
            )
            for r in results
        ]
