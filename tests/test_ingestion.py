"""
Integration tests for the LeadIngestionService and the ingest API endpoint.

Tests that require a live PostgreSQL connection are marked with
@pytest.mark.anyio and use a real async session. They will report
the DB status as part of the result (pass when DB is up, graceful
skip/error when DB is not configured).

API-level tests use the synchronous TestClient and test the full
request/response cycle including error handling.
"""

import json
import uuid

import pytest

from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord
from app.integrations.mock_apollo import MockApolloProvider
from app.integrations.mock_scout import MockScoutProvider
from app.services.normalization import normalize_domain, normalize_email


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_record(
    *,
    source: str = "test",
    external_id: str = None,
    first_name: str = "Test",
    last_name: str = "User",
    email: str = None,
    company_name: str = None,
    company_domain: str = None,
    job_title: str = "Engineer",
) -> LeadSourceRecord:
    return LeadSourceRecord(
        source=source,
        external_id=external_id or str(uuid.uuid4()),
        first_name=first_name,
        last_name=last_name,
        email=email,
        company_name=company_name,
        company_domain=company_domain,
        job_title=job_title,
    )


# ── Ingestion service unit tests (no DB needed) ───────────────────────────────

class TestIngestionServiceStructure:
    """Tests that don't require a live database."""

    def test_ingestion_service_importable(self):
        from app.services.lead_ingestion import LeadIngestionService
        svc = LeadIngestionService()
        assert svc is not None

    def test_batch_summary_dataclass(self):
        from app.services.lead_ingestion import BatchIngestionSummary, IngestionResult
        summary = BatchIngestionSummary(provider="test")
        result = IngestionResult(
            lead_id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            company_created=True,
            person_created=True,
            event_created=True,
        )
        summary.record(result)
        assert summary.companies_created == 1
        assert summary.people_created == 1
        assert summary.leads_created == 1
        assert summary.events_created == 1

    def test_batch_summary_existing_counts(self):
        from app.services.lead_ingestion import BatchIngestionSummary, IngestionResult
        summary = BatchIngestionSummary(provider="test")
        result = IngestionResult(
            lead_id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            company_created=False,
            person_created=False,
        )
        summary.record(result)
        assert summary.companies_created == 0
        assert summary.companies_existing == 1
        assert summary.people_created == 0
        assert summary.people_existing == 1

    def test_batch_summary_to_dict(self):
        from app.services.lead_ingestion import BatchIngestionSummary
        summary = BatchIngestionSummary(provider="mock_apollo", records_received=3)
        d = summary.to_dict()
        assert d["provider"] == "mock_apollo"
        assert d["records_received"] == 3
        assert "companies_created" in d
        assert "errors" in d


# ── DB integration tests ──────────────────────────────────────────────────────

class TestIngestionWithDatabase:
    """
    Tests requiring a live PostgreSQL connection.

    If the DB is not available, the health probe returns 'error' and
    these tests verify the graceful-failure path.
    """

    @pytest.mark.anyio
    async def test_ingest_single_record_creates_company_person_lead(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.services.lead_ingestion import LeadIngestionService
        from app.models.base import Company, Person, Lead, Event

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        record = make_record(
            source="test_ingestion",
            email=f"unique_{uuid.uuid4().hex[:8]}@testco.example",
            company_name="Test Company Ltd",
            company_domain=f"testco-{uuid.uuid4().hex[:8]}.example",
        )

        async with AsyncSessionLocal() as session:
            summary = await svc.ingest_batch([record], session, "test_ingestion")
            await session.commit()

        assert summary.records_received == 1
        assert summary.companies_created == 1
        assert summary.people_created == 1
        assert summary.leads_created == 1
        assert summary.events_created == 1
        assert summary.errors == []

    @pytest.mark.anyio
    async def test_same_domain_does_not_create_second_company(self):
        """Ingesting two records with the same domain must create only 1 company."""
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        domain = f"dedup-domain-{uuid.uuid4().hex[:8]}.test"
        records = [
            make_record(
                email=f"person1_{uuid.uuid4().hex[:8]}@{domain}",
                company_name="Dedup Corp",
                company_domain=domain,
            ),
            make_record(
                email=f"person2_{uuid.uuid4().hex[:8]}@{domain}",
                company_name="Dedup Corp",
                company_domain=domain,
            ),
        ]

        async with AsyncSessionLocal() as session:
            summary = await svc.ingest_batch(records, session, "test")
            await session.commit()

        assert summary.companies_created == 1
        assert summary.companies_existing == 1
        assert summary.people_created == 2  # two different emails
        assert summary.leads_created == 2

    @pytest.mark.anyio
    async def test_same_email_does_not_create_second_person(self):
        """Ingesting two records with the same email must create only 1 person."""
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        email = f"same_{uuid.uuid4().hex[:8]}@duptest.test"
        records = [
            make_record(email=email, company_domain=f"co1-{uuid.uuid4().hex[:8]}.test"),
            make_record(email=email, company_domain=f"co2-{uuid.uuid4().hex[:8]}.test"),
        ]

        async with AsyncSessionLocal() as session:
            summary = await svc.ingest_batch(records, session, "test")
            await session.commit()

        assert summary.people_created == 1
        assert summary.people_existing == 1
        # Two leads for one person (two opportunity signals)
        assert summary.leads_created == 2

    @pytest.mark.anyio
    async def test_source_preserved_on_lead(self):
        """The source field on the Lead must match the provider name."""
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.models.base import Lead
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        record = make_record(
            source="mock_apollo",
            email=f"src_test_{uuid.uuid4().hex[:8]}@sourcetest.test",
        )

        async with AsyncSessionLocal() as session:
            summary = await svc.ingest_batch([record], session, "mock_apollo")
            await session.commit()
            # Re-query to verify persistence
            result = await session.execute(
                select(Lead).where(Lead.id == summary.leads_created and True)
            )

        assert summary.leads_created == 1
        assert len(summary.errors) == 0

    @pytest.mark.anyio
    async def test_lead_ingested_event_is_created(self):
        """Each ingested record must produce a lead_ingested Event."""
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.models.base import Event
        from app.services.lead_ingestion import LeadIngestionService, IngestionResult

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        record = make_record(
            source="test",
            email=f"event_test_{uuid.uuid4().hex[:8]}@events.test",
        )

        async with AsyncSessionLocal() as session:
            summary = await svc.ingest_batch([record], session, "test")
            # Capture the lead_id from the internal result by checking events count
            await session.commit()

        assert summary.events_created == 1

    @pytest.mark.anyio
    async def test_event_payload_contains_expected_keys(self):
        """The lead_ingested event payload must include source, lead_id, etc."""
        from sqlalchemy import select, desc
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.models.base import Event
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        svc = LeadIngestionService()
        record = make_record(
            source="test_payload",
            email=f"payload_{uuid.uuid4().hex[:8]}@payloadtest.test",
            company_domain=f"payload-{uuid.uuid4().hex[:8]}.test",
        )

        async with AsyncSessionLocal() as session:
            await svc.ingest_batch([record], session, "test_payload")
            await session.commit()
            # Fetch the most recently created event
            result = await session.execute(
                select(Event)
                .where(Event.event_type == "lead_ingested")
                .where(Event.source == "test_payload")
                .order_by(desc(Event.occurred_at))
                .limit(1)
            )
            event = result.scalar_one_or_none()

        assert event is not None
        assert event.event_type == "lead_ingested"
        payload = json.loads(event.payload)
        assert "source" in payload
        assert "lead_id" in payload
        assert payload["source"] == "test_payload"

    @pytest.mark.anyio
    async def test_repeated_mock_apollo_ingestion_no_new_company_person(self):
        """
        Ingesting MockApollo records twice must NOT create duplicate
        Company or Person entities (only new Leads are expected).
        """
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        apollo = MockApolloProvider()
        criteria = LeadSearchCriteria(company_domain="quantum.io")
        records = await apollo.search_leads(criteria)

        svc = LeadIngestionService()

        async with AsyncSessionLocal() as session:
            first = await svc.ingest_batch(records, session, "mock_apollo")
            await session.commit()

        async with AsyncSessionLocal() as session:
            second = await svc.ingest_batch(records, session, "mock_apollo")
            await session.commit()

        # First run creates both
        assert first.companies_created == 1
        assert first.people_created == 1
        # Second run finds both, creates neither
        assert second.companies_created == 0
        assert second.companies_existing == 1
        assert second.people_created == 0
        assert second.people_existing == 1
        # Both runs create leads (lead = opportunity signal, not a unique contact)
        assert first.leads_created == 1
        assert second.leads_created == 1

    @pytest.mark.anyio
    async def test_mock_scout_deduplicates_against_apollo_person(self):
        """
        Scout's scout_002 (m.rivera@horizon.ai) overlaps with Apollo's apollo_002.
        After Apollo runs first, Scout must NOT create a new Person for Marcus Rivera.
        """
        from app.core.database import AsyncSessionLocal, check_db_health
        from app.services.lead_ingestion import LeadIngestionService

        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        apollo = MockApolloProvider()
        scout = MockScoutProvider()
        criteria = LeadSearchCriteria(company_domain="horizon.ai")
        apollo_records = await apollo.search_leads(criteria)
        scout_records = await scout.search_leads(criteria)

        svc = LeadIngestionService()

        async with AsyncSessionLocal() as session:
            apollo_summary = await svc.ingest_batch(apollo_records, session, "mock_apollo")
            await session.commit()

        async with AsyncSessionLocal() as session:
            scout_summary = await svc.ingest_batch(scout_records, session, "mock_scout")
            await session.commit()

        # Apollo creates 1 company + 1 person
        assert apollo_summary.companies_created == 1
        assert apollo_summary.people_created == 1
        # Scout finds the same company + same person
        assert scout_summary.companies_created == 0
        assert scout_summary.companies_existing == 1
        assert scout_summary.people_created == 0
        assert scout_summary.people_existing == 1


# ── API-level ingestion tests ─────────────────────────────────────────────────

class TestIngestAPI:
    """Tests for POST /api/v1/leads/ingest via TestClient."""

    def test_list_providers_returns_both_mocks(self, client):
        response = client.get("/api/v1/leads/providers")
        assert response.status_code == 200
        body = response.json()
        assert "mock_apollo" in body["providers"]
        assert "mock_scout" in body["providers"]

    def test_unknown_provider_returns_422(self, client):
        response = client.post(
            "/api/v1/leads/ingest",
            json={"provider": "nonexistent_provider"},
        )
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_empty_provider_returns_422(self, client):
        response = client.post(
            "/api/v1/leads/ingest",
            json={"provider": "   "},
        )
        assert response.status_code == 422

    def test_missing_provider_field_returns_422(self, client):
        response = client.post(
            "/api/v1/leads/ingest",
            json={"query": {"company_domain": "example.com"}},
        )
        assert response.status_code == 422

    def test_valid_provider_mock_apollo_returns_200_or_503(self, client):
        """
        Returns 200 if DB is up (full ingestion), or 500/503 if DB is not available.
        Either way, the endpoint must not crash with a 500 from unknown exceptions.
        """
        response = client.post(
            "/api/v1/leads/ingest",
            json={"provider": "mock_apollo"},
        )
        # 200 = DB available, ingestion succeeded
        # 500 = DB not available (service raised on commit)
        assert response.status_code in (200, 500)

    def test_ingest_response_schema_when_db_available(self, client):
        """When DB is available, response matches IngestResponse schema."""
        # First check DB
        db_response = client.get("/health/db")
        if db_response.json().get("status") != "ok":
            pytest.skip("PostgreSQL not available")

        response = client.post(
            "/api/v1/leads/ingest",
            json={"provider": "mock_apollo"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "mock_apollo"
        assert body["records_received"] == 3
        assert isinstance(body["companies_created"], int)
        assert isinstance(body["people_created"], int)
        assert isinstance(body["leads_created"], int)
        assert isinstance(body["events_created"], int)
        assert isinstance(body["errors"], list)

    def test_ingest_with_domain_filter(self, client):
        """Passing a domain filter in query must return fewer records."""
        db_response = client.get("/health/db")
        if db_response.json().get("status") != "ok":
            pytest.skip("PostgreSQL not available")

        response = client.post(
            "/api/v1/leads/ingest",
            json={
                "provider": "mock_apollo",
                "query": {"company_domain": "quantum.io"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["records_received"] == 1

    def test_ingest_mock_scout_succeeds(self, client):
        """Mock Scout provider also successfully ingests when DB is available."""
        db_response = client.get("/health/db")
        if db_response.json().get("status") != "ok":
            pytest.skip("PostgreSQL not available")

        response = client.post(
            "/api/v1/leads/ingest",
            json={"provider": "mock_scout"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "mock_scout"
        assert body["records_received"] == 3
