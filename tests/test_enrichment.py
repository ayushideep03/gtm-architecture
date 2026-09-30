"""
Tests for Data Processing & Enrichment (Prompt 3).

Covers:
- Unit tests: normalization, identity resolution logic, mock providers, unknown identifiers
- Database integration tests: persistence of company/person enrichment, idempotency / no duplicates,
  enrichment events emission, no additional leads created, existing ingestion compatibility
- API tests: provider listing, manual enrichment triggers, 404 on unknown entity, 422 on invalid UUID/provider
"""

import json
import uuid
import pytest
from sqlalchemy import select, func

from app.integrations.enrichment_provider import EnrichmentProviderError
from app.integrations.enrichment_record import (
    CompanyEnrichmentData,
    CompanyEnrichmentResult,
    PersonEnrichmentData,
    PersonEnrichmentResult,
)
from app.integrations.enrichment_registry import EnrichmentRegistry, enrichment_registry
from app.integrations.mock_enrichment import (
    MockCompanyEnrichmentProvider,
    MockPersonEnrichmentProvider,
)
from app.models.base import Company, Event, Lead, Person
from app.services.enrichment import EnrichmentOutcome, EnrichmentService
from app.services.identity_resolution import (
    CompanyResolutionResult,
    IdentityResolver,
    MatchType,
    PersonResolutionResult,
)
from app.services.normalization import normalize_domain, normalize_email


# -----------------------------------------------------------------------------
# 1. UNIT TESTS (no live database required)
# -----------------------------------------------------------------------------

class TestEnrichmentUnit:
    """Unit tests for enrichment domain models, providers, and normalization."""

    def test_domain_normalization(self):
        assert normalize_domain("  HTTP://WWW.Quantum.IO/about/  ") == "quantum.io"
        assert normalize_domain("https://example.com") == "example.com"
        assert normalize_domain("") is None
        assert normalize_domain(None) is None

    def test_email_normalization(self):
        assert normalize_email("  Alice.Chen@Quantum.IO  ") == "alice.chen@quantum.io"
        assert normalize_email("") is None
        assert normalize_email(None) is None

    def test_no_fuzzy_matching_domain(self):
        assert normalize_domain("quantum.org") != normalize_domain("quantum.io")
        assert normalize_domain("quantumm.io") != normalize_domain("quantum.io")

    def test_no_fuzzy_matching_email(self):
        assert normalize_email("alice@quantum.io") != normalize_email("alice.chen@quantum.io")

    @pytest.mark.anyio
    async def test_mock_company_provider_known_fixture(self):
        provider = MockCompanyEnrichmentProvider()
        result = await provider.enrich_company("quantum.io")
        assert result.found is True
        assert result.domain == "quantum.io"
        assert result.data is not None
        assert result.data.industry == "Software"
        assert result.data.funding_stage == "Series B"
        assert result.data.revenue_range == "$10M-$50M"
        assert "Python" in result.data.technologies

    @pytest.mark.anyio
    async def test_mock_company_provider_unknown(self):
        provider = MockCompanyEnrichmentProvider()
        result = await provider.enrich_company("unknown-domain-xyz.com")
        assert result.found is False
        assert result.data is None

    @pytest.mark.anyio
    async def test_mock_person_provider_known_fixture(self):
        provider = MockPersonEnrichmentProvider()
        result = await provider.enrich_person("alice.chen@quantum.io")
        assert result.found is True
        assert result.email == "alice.chen@quantum.io"
        assert result.data is not None
        assert result.data.first_name == "Alice"
        assert result.data.job_title == "VP of Engineering"
        assert result.data.seniority == "vp"
        assert result.data.department == "Engineering"

    @pytest.mark.anyio
    async def test_mock_person_provider_unknown(self):
        provider = MockPersonEnrichmentProvider()
        result = await provider.enrich_person("unknown.user@doesnotexist.xyz")
        assert result.found is False
        assert result.data is None

    def test_enrichment_registry_lookup(self):
        co_p = enrichment_registry.get_company_provider("mock_enrichment")
        assert co_p.provider_name == "mock_enrichment"
        p_p = enrichment_registry.get_person_provider("mock_enrichment")
        assert p_p.provider_name == "mock_enrichment"

    def test_enrichment_registry_unknown_raises(self):
        with pytest.raises(EnrichmentProviderError) as exc_info:
            enrichment_registry.get_company_provider("nonexistent_enricher")
        assert "nonexistent_enricher" in str(exc_info.value)

    def test_enrichment_registry_list_providers(self):
        providers = enrichment_registry.list_providers()
        assert "company" in providers
        assert "person" in providers
        assert "mock_enrichment" in providers["company"]
        assert "mock_enrichment" in providers["person"]


# -----------------------------------------------------------------------------
# 2. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestEnrichmentWithDatabase:
    """
    Database tests for identity resolution and enrichment persistence.
    Isolated before every test via conftest's _db_isolation fixture.
    """

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_identity_resolution_by_domain_and_website(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        resolver = IdentityResolver()

        async with AsyncSessionLocal() as session:
            company = Company(
                name="Quantum Dynamics",
                domain="quantum.io",
                website="https://quantum.io",
            )
            session.add(company)
            await session.commit()
            company_id = company.id

            # Priority 1: Match by exact domain
            res1 = await resolver.resolve_company(session, domain="quantum.io")
            assert res1.found is True
            assert res1.company_id == company_id
            assert res1.match_type == MatchType.EXACT_DOMAIN
            assert res1.confidence == 1.0

            # Priority 2: Match by website domain when domain differs/is missing
            res2 = await resolver.resolve_company(
                session,
                domain="nomatch.org",
                website="https://quantum.io/overview",
            )
            assert res2.found is True
            assert res2.company_id == company_id
            assert res2.match_type == MatchType.WEBSITE_DOMAIN
            assert res2.confidence == 1.0

            # Priority 3: No match
            res3 = await resolver.resolve_company(session, domain="unrelated.org")
            assert res3.found is False
            assert res3.company_id is None
            assert res3.match_type == MatchType.NO_MATCH
            assert res3.confidence == 0.0

    @pytest.mark.anyio
    async def test_identity_resolution_person(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        resolver = IdentityResolver()

        async with AsyncSessionLocal() as session:
            person = Person(
                first_name="Alice",
                last_name="Chen",
                email="alice.chen@quantum.io",
            )
            session.add(person)
            await session.commit()
            person_id = person.id

            # Priority 1: Match by email
            res1 = await resolver.resolve_person(session, email="ALICE.CHEN@quantum.io")
            assert res1.found is True
            assert res1.person_id == person_id
            assert res1.match_type == MatchType.EXACT_EMAIL
            assert res1.confidence == 1.0

            # No match
            res2 = await resolver.resolve_person(session, email="bob@other.com")
            assert res2.found is False
            assert res2.person_id is None
            assert res2.match_type == MatchType.NO_MATCH

    @pytest.mark.anyio
    async def test_company_enrichment_persists_and_emits_event(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = EnrichmentService()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.commit()
            company_id = company.id

            outcome = await svc.enrich_company_by_id(session, company_id, "mock_enrichment")
            await session.commit()

            assert outcome.found is True
            assert outcome.success is True
            assert "description" in outcome.fields_updated
            assert "funding_stage" in outcome.fields_updated

            refetched = await session.get(Company, company_id)
            assert refetched.funding_stage == "Series B"
            assert refetched.revenue_range == "$10M-$50M"
            assert refetched.description == "Quantum-safe cryptography platform for enterprise."
            assert refetched.enrichment_source == "mock_enrichment"
            assert refetched.enriched_at is not None
            assert json.loads(refetched.technologies) == ["Python", "Rust", "PostgreSQL", "Kubernetes"]

            ev_result = await session.execute(
                select(Event).where(
                    Event.event_type == "company_enriched",
                    Event.source == "mock_enrichment",
                )
            )
            event = ev_result.scalar_one_or_none()
            assert event is not None
            payload = json.loads(event.payload)
            assert payload["entity_type"] == "company"
            assert payload["domain"] == "quantum.io"
            assert payload["provider"] == "mock_enrichment"

    @pytest.mark.anyio
    async def test_person_enrichment_persists_and_emits_event(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = EnrichmentService()

        async with AsyncSessionLocal() as session:
            person = Person(
                first_name="Alice",
                last_name="Chen",
                email="alice.chen@quantum.io",
            )
            session.add(person)
            await session.commit()
            person_id = person.id

            outcome = await svc.enrich_person_by_id(session, person_id, "mock_enrichment")
            await session.commit()

            assert outcome.found is True
            assert outcome.success is True
            assert "seniority" in outcome.fields_updated
            assert "department" in outcome.fields_updated

            refetched = await session.get(Person, person_id)
            assert refetched.seniority == "vp"
            assert refetched.department == "Engineering"
            assert refetched.location == "San Francisco, CA"
            assert refetched.phone == "+1-555-0101"
            assert refetched.enrichment_source == "mock_enrichment"
            assert refetched.enriched_at is not None

            ev_result = await session.execute(
                select(Event).where(
                    Event.event_type == "person_enriched",
                    Event.source == "mock_enrichment",
                )
            )
            event = ev_result.scalar_one_or_none()
            assert event is not None
            payload = json.loads(event.payload)
            assert payload["entity_type"] == "person"
            assert payload["email"] == "alice.chen@quantum.io"

    @pytest.mark.anyio
    async def test_repeated_enrichment_does_not_duplicate_entities(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = EnrichmentService()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.commit()
            cid = company.id

            res1 = await svc.enrich_company_by_id(session, cid, "mock_enrichment")
            await session.commit()
            assert res1.found is True
            assert len(res1.fields_updated) > 0

            res2 = await svc.enrich_company_by_id(session, cid, "mock_enrichment")
            await session.commit()
            assert res2.found is True
            assert res2.fields_updated == []

            count_result = await session.execute(select(func.count()).select_from(Company))
            assert count_result.scalar() == 1

    @pytest.mark.anyio
    async def test_enrichment_does_not_create_additional_leads(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = EnrichmentService()

        async with AsyncSessionLocal() as session:
            company = Company(name="Horizon AI", domain="horizon.ai")
            session.add(company)
            await session.flush()

            person = Person(
                first_name="Marcus",
                last_name="Rivera",
                email="m.rivera@horizon.ai",
                company_id=company.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            leads_before = (await session.execute(select(func.count()).select_from(Lead))).scalar()
            assert leads_before == 1

            outcomes = await svc.enrich_lead(session, lead_id, "mock_enrichment")
            await session.commit()

            assert outcomes["company"] is not None and outcomes["company"].found is True
            assert outcomes["person"] is not None and outcomes["person"].found is True

            leads_after = (await session.execute(select(func.count()).select_from(Lead))).scalar()
            assert leads_after == 1

            ev_result = await session.execute(
                select(Event).where(
                    Event.event_type == "enrichment_completed",
                    Event.lead_id == lead_id,
                )
            )
            event = ev_result.scalar_one_or_none()
            assert event is not None

    @pytest.mark.anyio
    async def test_enrichment_failed_event_on_unknown_identifier(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = EnrichmentService()

        async with AsyncSessionLocal() as session:
            company = Company(name="Ghost Co", domain="ghost-nonexistent-123.com")
            session.add(company)
            await session.commit()

            outcome = await svc.enrich_company_by_id(session, company.id, "mock_enrichment")
            await session.commit()

            assert outcome.found is False

            ev_result = await session.execute(
                select(Event).where(
                    Event.event_type == "enrichment_failed",
                    Event.source == "mock_enrichment",
                )
            )
            event = ev_result.scalar_one_or_none()
            assert event is not None


# -----------------------------------------------------------------------------
# 3. API TESTS
# -----------------------------------------------------------------------------

class TestEnrichmentAPI:
    """Tests for /api/v1/enrichment endpoints."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    def test_list_enrichment_providers(self, client):
        response = client.get("/api/v1/enrichment/providers")
        assert response.status_code == 200
        body = response.json()
        assert "company" in body
        assert "person" in body
        assert "mock_enrichment" in body["company"]
        assert "mock_enrichment" in body["person"]

    def test_invalid_uuid_returns_422(self, client):
        response = client.post("/api/v1/enrichment/company/not-a-valid-uuid")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_unknown_entity_returns_404(self, app):
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            random_id = str(uuid.uuid4())
            response = await ac.post(f"/api/v1/enrichment/company/{random_id}")
            assert response.status_code == 404

            response = await ac.post(f"/api/v1/enrichment/person/{random_id}")
            assert response.status_code == 404

            response = await ac.post(f"/api/v1/enrichment/lead/{random_id}")
            assert response.status_code == 404

    def test_provider_error_handling_unknown_provider(self, client):
        random_id = str(uuid.uuid4())
        response = client.post(
            f"/api/v1/enrichment/company/{random_id}",
            json={"provider": "unknown_vendor_xyz"},
        )
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_successful_company_enrichment_api(self, app):
        from httpx import AsyncClient, ASGITransport
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip("PostgreSQL not available")

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.commit()
            company_id = company.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(f"/api/v1/enrichment/company/{company_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["entity_type"] == "company"
            assert body["found"] is True
            assert body["success"] is True
            assert body["provider"] == "mock_enrichment"

    @pytest.mark.anyio
    async def test_successful_person_enrichment_api(self, app):
        from httpx import AsyncClient, ASGITransport
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip("PostgreSQL not available")

        async with AsyncSessionLocal() as session:
            person = Person(
                first_name="Alice",
                last_name="Chen",
                email="alice.chen@quantum.io",
            )
            session.add(person)
            await session.commit()
            person_id = person.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(f"/api/v1/enrichment/person/{person_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["entity_type"] == "person"
            assert body["found"] is True
            assert body["success"] is True

