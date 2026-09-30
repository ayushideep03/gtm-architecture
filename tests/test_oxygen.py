"""
Tests for Oxygen Orchestration & Decision Layer (Prompt 4).

Covers:
- Unit tests: OxygenContext, event retrieval, deterministic decision rules,
  missing company/person, incomplete enrichment, completed enrichment,
  existing pending task, no_action decision, capability lookup, unknown capability,
  decision output structure.
- Database integration tests: CRM state retrieval, Task creation with structured payload,
  decision event persistence, idempotency (no duplicate tasks), respecting existing tasks,
  no new Leads created, no direct enrichment modification.
- API tests: decide endpoint, orchestrate endpoint, capabilities endpoint,
  invalid UUID, unknown lead, error handling.
"""

from datetime import datetime, timezone
import json
import uuid
import pytest
from sqlalchemy import select, func

from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.oxygen import (
    CapabilityContract,
    CapabilityRegistry,
    Decision,
    DecisionOutcome,
    DecisionReason,
    DeterministicDecisionEngine,
    OxygenContext,
    OxygenContextBuilder,
    OxygenOrchestrator,
    capability_registry,
)


# -----------------------------------------------------------------------------
# 1. UNIT TESTS (no live database required)
# -----------------------------------------------------------------------------

class TestOxygenUnit:
    """Unit tests for context model, decision rules, and capability registry."""

    def test_oxygen_context_construction(self):
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Acme", domain="acme.com")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@acme.com")
        lead = Lead(id=lead_id, person_id=person.id)
        event = Event(id=uuid.uuid4(), lead_id=lead_id, event_type="lead_ingested")

        ctx = OxygenContext(
            lead_id=lead_id,
            lead=lead,
            person=person,
            company=comp,
            events=[event],
            is_company_enriched=False,
            is_person_enriched=False,
        )

        assert ctx.lead_id == lead_id
        assert ctx.has_company is True
        assert ctx.has_person is True
        assert ctx.is_company_enriched is False
        assert ctx.is_person_enriched is False
        assert ctx.latest_event("lead_ingested") == event
        assert ctx.latest_event("nonexistent_event") is None

    def test_decision_output_structure(self):
        lead_id = uuid.uuid4()
        decision = Decision(
            action="enrich_company",
            reason=DecisionReason.COMPANY_ENRICHMENT_NEEDED.value,
            target_id=lead_id,
            target_type="company",
            confidence=1.0,
            capability="company_enrichment",
            parameters={"company_id": str(lead_id)},
            source_event_ids=[uuid.uuid4()],
        )

        assert decision.action == "enrich_company"
        assert decision.confidence == 1.0
        assert decision.capability == "company_enrichment"
        assert len(decision.source_event_ids) == 1

    def test_capability_lookup_and_unknown(self):
        reg = CapabilityRegistry()
        reg.register(
            CapabilityContract(
                name="test_tool",
                description="A test tool",
                input_contract={"arg": "str"},
                is_available=True,
            )
        )

        assert reg.is_available("test_tool") is True
        assert reg.get("test_tool").description == "A test tool"
        assert reg.is_available("nonexistent") is False
        assert reg.get("nonexistent") is None

    def test_rule_missing_person_and_company(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        ctx = OxygenContext(lead_id=lead_id)

        decision = engine.decide(ctx)
        assert decision.action == "no_action"
        assert decision.reason == DecisionReason.MISSING_DATA.value

    def test_rule_company_enrichment_needed(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            is_company_enriched=False,
        )

        decision = engine.decide(ctx)
        assert decision.action == "enrich_company"
        assert decision.target_id == comp.id
        assert decision.target_type == "company"
        assert decision.capability == "company_enrichment"
        assert decision.parameters["domain"] == "quantum.io"

    def test_rule_company_enrichment_already_pending_waits(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        pending_task = Task(
            id=uuid.uuid4(),
            lead_id=lead_id,
            task_type="enrich_company",
            status="pending",
        )
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            is_company_enriched=False,
            tasks=[pending_task],
        )

        decision = engine.decide(ctx)
        assert decision.action == "wait"
        assert decision.reason == DecisionReason.TASK_ALREADY_EXISTS.value

    def test_rule_person_enrichment_needed(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@quantum.io")
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            person=person,
            is_company_enriched=True,
            is_person_enriched=False,
        )

        decision = engine.decide(ctx)
        assert decision.action == "enrich_person"
        assert decision.target_id == person.id
        assert decision.target_type == "person"
        assert decision.capability == "person_enrichment"
        assert decision.parameters["email"] == "alice@quantum.io"

    def test_rule_person_enrichment_already_pending_waits(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@quantum.io")
        pending_task = Task(
            id=uuid.uuid4(),
            lead_id=lead_id,
            task_type="enrich_person",
            status="pending",
        )
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            person=person,
            is_company_enriched=True,
            is_person_enriched=False,
            tasks=[pending_task],
        )

        decision = engine.decide(ctx)
        assert decision.action == "wait"
        assert decision.reason == DecisionReason.TASK_ALREADY_EXISTS.value

    def test_rule_enrichment_completed_recommends_execution(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@quantum.io")
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            person=person,
            is_company_enriched=True,
            is_person_enriched=True,
        )

        decision = engine.decide(ctx)
        assert decision.action == "qualify_lead"
        assert decision.target_id == lead_id
        assert decision.capability == "lead_qualification"
        assert decision.reason == DecisionReason.READY_FOR_EXECUTION.value

    def test_rule_execution_task_pending_waits(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@quantum.io")
        pending_task = Task(
            id=uuid.uuid4(),
            lead_id=lead_id,
            task_type="qualify_lead",
            status="pending",
        )
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            person=person,
            is_company_enriched=True,
            is_person_enriched=True,
            tasks=[pending_task],
        )

        decision = engine.decide(ctx)
        assert decision.action == "wait"
        assert decision.reason == DecisionReason.TASK_ALREADY_EXISTS.value

    def test_rule_execution_completed_no_action(self):
        engine = DeterministicDecisionEngine()
        lead_id = uuid.uuid4()
        comp = Company(id=uuid.uuid4(), name="Quantum", domain="quantum.io")
        person = Person(id=uuid.uuid4(), first_name="Alice", email="alice@quantum.io")
        completed_task = Task(
            id=uuid.uuid4(),
            lead_id=lead_id,
            task_type="qualify_lead",
            status="done",
        )
        ctx = OxygenContext(
            lead_id=lead_id,
            company=comp,
            person=person,
            is_company_enriched=True,
            is_person_enriched=True,
            tasks=[completed_task],
        )

        decision = engine.decide(ctx)
        assert decision.action == "no_action"
        assert decision.reason == DecisionReason.EXECUTION_COMPLETED.value


# -----------------------------------------------------------------------------
# 2. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestOxygenWithDatabase:
    """
    Database tests verifying CRM state gathering, Task creation,
    event emission, idempotency, and non-destructive behavior.
    """

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_context_builder_reads_correct_crm_state(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        builder = OxygenContextBuilder()

        async with AsyncSessionLocal() as session:
            # Seed company, person, lead, event, task
            company = Company(name="Horizon AI", domain="horizon.ai")
            session.add(company)
            await session.flush()

            person = Person(
                first_name="Marcus",
                email="m.rivera@horizon.ai",
                company_id=company.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="mock_apollo", status="new")
            session.add(lead)
            await session.flush()

            event = Event(
                lead_id=lead.id,
                event_type="lead_ingested",
                source="mock_apollo",
            )
            session.add(event)

            task = Task(
                lead_id=lead.id,
                task_type="custom_task",
                status="pending",
            )
            session.add(task)
            await session.commit()
            lead_id = lead.id

            # Build context
            ctx = await builder.build(session, lead_id)
            assert ctx is not None
            assert ctx.lead.id == lead_id
            assert ctx.person.email == "m.rivera@horizon.ai"
            assert ctx.company.domain == "horizon.ai"
            assert len(ctx.events) == 1
            assert ctx.events[0].event_type == "lead_ingested"
            assert len(ctx.tasks) == 1
            assert ctx.tasks[0].task_type == "custom_task"
            assert ctx.is_company_enriched is False
            assert ctx.is_person_enriched is False

    @pytest.mark.anyio
    async def test_oxygen_creates_expected_task_with_payload(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        orchestrator = OxygenOrchestrator()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.flush()

            person = Person(first_name="Alice", email="alice@quantum.io", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            # Orchestrate: should recommend and create enrich_company Task
            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

            assert outcome.status == "success"
            assert outcome.decision.action == "enrich_company"
            assert outcome.task_created is True
            assert outcome.task_id is not None

            # Verify persisted Task
            task = await session.get(Task, outcome.task_id)
            assert task is not None
            assert task.lead_id == lead_id
            assert task.task_type == "enrich_company"
            assert task.status == "pending"

            payload = json.loads(task.payload)
            assert payload["domain"] == "quantum.io"
            assert payload["company_id"] == str(company.id)

    @pytest.mark.anyio
    async def test_oxygen_decision_event_is_persisted(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        orchestrator = OxygenOrchestrator()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.flush()

            person = Person(first_name="Alice", email="alice@quantum.io", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

            assert outcome.event_id is not None

            event = await session.get(Event, outcome.event_id)
            assert event is not None
            assert event.event_type == "oxygen_decision"
            assert event.source == "oxygen"
            assert event.lead_id == lead_id

            payload = json.loads(event.payload)
            assert payload["action"] == "enrich_company"
            assert payload["capability"] == "company_enrichment"
            assert payload["task_created"] is True

    @pytest.mark.anyio
    async def test_duplicate_orchestration_does_not_create_duplicate_tasks(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        orchestrator = OxygenOrchestrator()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.flush()

            person = Person(first_name="Alice", email="alice@quantum.io", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            # First orchestration -> creates task
            res1 = await orchestrator.orchestrate(session, lead_id)
            await session.commit()
            assert res1.task_created is True
            first_task_id = res1.task_id

            # Second orchestration -> detects pending task, does NOT create another
            res2 = await orchestrator.orchestrate(session, lead_id)
            await session.commit()
            assert res2.task_created is False
            assert res2.status == "waiting"
            assert res2.decision.action == "wait"

            # Verify total tasks in DB for this lead is still exactly 1
            task_count = (
                await session.execute(
                    select(func.count()).select_from(Task).where(Task.lead_id == lead_id)
                )
            ).scalar()
            assert task_count == 1

    @pytest.mark.anyio
    async def test_oxygen_does_not_create_leads_or_modify_enrichment_data(self):
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip(f"PostgreSQL not available: {db_status['detail']}")

        orchestrator = OxygenOrchestrator()

        async with AsyncSessionLocal() as session:
            company = Company(name="Quantum", domain="quantum.io")
            session.add(company)
            await session.flush()

            person = Person(first_name="Alice", email="alice@quantum.io", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            leads_before = (await session.execute(select(func.count()).select_from(Lead))).scalar()

            # Orchestrate
            await orchestrator.orchestrate(session, lead_id)
            await session.commit()

            # Verify no new leads created
            leads_after = (await session.execute(select(func.count()).select_from(Lead))).scalar()
            assert leads_before == leads_after == 1

            # Verify company enrichment data wasn't modified directly by Oxygen
            refetched_co = await session.get(Company, company.id)
            assert refetched_co.enriched_at is None
            assert refetched_co.enrichment_source is None


# -----------------------------------------------------------------------------
# 3. API TESTS
# -----------------------------------------------------------------------------

class TestOxygenAPI:
    """Tests for /api/v1/oxygen endpoints."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    def test_capabilities_endpoint(self, client):
        response = client.get("/api/v1/oxygen/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert "capabilities" in body
        names = [c["name"] for c in body["capabilities"]]
        assert "company_enrichment" in names
        assert "person_enrichment" in names
        assert "lead_qualification" in names

    def test_invalid_uuid_returns_422(self, client):
        response = client.post("/api/v1/oxygen/leads/not-a-uuid/decide")
        assert response.status_code == 422

        response = client.post("/api/v1/oxygen/leads/not-a-uuid/orchestrate")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_unknown_lead_returns_404(self, app):
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            random_id = str(uuid.uuid4())
            res1 = await ac.post(f"/api/v1/oxygen/leads/{random_id}/decide")
            assert res1.status_code == 404

            res2 = await ac.post(f"/api/v1/oxygen/leads/{random_id}/orchestrate")
            assert res2.status_code == 404

    @pytest.mark.anyio
    async def test_decide_endpoint_success(self, app):
        from httpx import AsyncClient, ASGITransport
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip("PostgreSQL not available")

        async with AsyncSessionLocal() as session:
            company = Company(name="Horizon", domain="horizon.ai")
            session.add(company)
            await session.flush()

            person = Person(first_name="Marcus", email="m.rivera@horizon.ai", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(f"/api/v1/oxygen/leads/{lead_id}/decide")
            assert response.status_code == 200
            body = response.json()
            assert body["action"] == "enrich_company"
            assert body["capability"] == "company_enrichment"
            assert "domain" in body["parameters"]

    @pytest.mark.anyio
    async def test_orchestrate_endpoint_success(self, app):
        from httpx import AsyncClient, ASGITransport
        from app.core.database import AsyncSessionLocal, check_db_health
        db_status = await check_db_health()
        if db_status["status"] != "ok":
            pytest.skip("PostgreSQL not available")

        async with AsyncSessionLocal() as session:
            company = Company(name="Horizon", domain="horizon.ai")
            session.add(company)
            await session.flush()

            person = Person(first_name="Marcus", email="m.rivera@horizon.ai", company_id=company.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, source="test", status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(f"/api/v1/oxygen/leads/{lead_id}/orchestrate")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "success"
            assert body["task_created"] is True
            assert body["task_id"] is not None
            assert body["event_id"] is not None
            assert body["decision"]["action"] == "enrich_company"

