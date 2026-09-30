"""
Mock Apollo lead source provider.

Returns deterministic, realistic-looking but entirely fake data.
No real API calls are made.

Design:
- Records are filtered by company_domain if supplied in criteria.
- At least one record overlaps with MockScoutProvider (same company,
  different person) to test deduplication in integration tests.
- At least one record has the SAME email as a MockScout record to
  verify that Person-level deduplication works correctly.

IMPORTANT: All data here is fictional. Do not use real people's information.
"""

from datetime import datetime, timezone

from app.integrations.lead_source import LeadSourceProvider, ProviderError
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord

# ── Static fixture data ───────────────────────────────────────────────────────
# These records are intentionally chosen so that:
#   - apollo_001 and scout_001 share the same company_domain ("quantum.io")
#   - apollo_001 and scout_001 have DIFFERENT emails → different people
#   - apollo_002 and scout_002 share same company_domain ("horizon.ai")
#   - apollo_003 is unique to Apollo

_MOCK_RECORDS: list[dict] = [
    {
        "external_id": "apollo_001",
        "first_name": "Alice",
        "last_name": "Chen",
        "email": "alice.chen@quantum.io",
        "phone": "+1-555-0101",
        "job_title": "VP of Engineering",
        "linkedin_url": "https://linkedin.com/in/alice-chen-fake",
        "company_name": "Quantum Dynamics",
        "company_domain": "quantum.io",
        "company_linkedin_url": "https://linkedin.com/company/quantum-dynamics-fake",
        "industry": "Software",
        "employee_count": 250,
        "location": "San Francisco, CA",
    },
    {
        "external_id": "apollo_002",
        "first_name": "Marcus",
        "last_name": "Rivera",
        "email": "m.rivera@horizon.ai",
        "phone": "+1-555-0202",
        "job_title": "CTO",
        "linkedin_url": "https://linkedin.com/in/marcus-rivera-fake",
        "company_name": "Horizon AI",
        "company_domain": "horizon.ai",
        "company_linkedin_url": "https://linkedin.com/company/horizon-ai-fake",
        "industry": "Artificial Intelligence",
        "employee_count": 80,
        "location": "Austin, TX",
    },
    {
        "external_id": "apollo_003",
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya.nair@synthwave.dev",
        "phone": "+1-555-0303",
        "job_title": "Head of Growth",
        "linkedin_url": "https://linkedin.com/in/priya-nair-fake",
        "company_name": "Synthwave Labs",
        "company_domain": "synthwave.dev",
        "company_linkedin_url": "https://linkedin.com/company/synthwave-labs-fake",
        "industry": "Developer Tools",
        "employee_count": 35,
        "location": "New York, NY",
    },
]


class MockApolloProvider(LeadSourceProvider):
    """
    Deterministic mock of an Apollo-style lead source.

    Supports filtering by company_domain. Returns all records when no
    filter is applied.
    """

    @property
    def provider_name(self) -> str:
        return "mock_apollo"

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
