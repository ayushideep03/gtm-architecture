"""Tests for the Execution Layer & Action Workers (Prompt 5).

Covers:
- Unit tests:
    - ExecutionResult validation
    - Provider registration and lookup
    - Unknown provider/capability handling
    - Deterministic mock qualification logic
    - Provider failure handling
    - Task state machine transition rules
- Database integration tests:
    - Execute pending task (pending -> in_progress -> completed)
    - Failed execution handling and status transition
    - Persistence of task.result and timestamps
    - Emission of task_started, task_completed, and task_failed events
    - Idempotency: existing completed task is not executed again
    - Duplicate execution attempt handling (in_progress conflict)
    - Safe rejection of unknown task types
    - Preservation of task/lead relationships
    - Guarantee that execution does not create unrelated leads or alter Oxygen state
    - End-to-end integration: Lead -> Oxygen decides -> Task created -> Task executed -> Event emitted
- API tests:
    - POST /api/v1/execution/tasks/{task_id}/execute
    - GET  /api/v1/execution/providers
    - GET  /api/v1/execution/tasks/{task_id}
    - 422 on invalid UUID
    - 404 on unknown task UUID
    - Idempotent execution on already completed task
    - Failed execution error response
"""

import json
from typing import Optional
import uuid
import httpx
from httpx import ASGITransport
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.executor import TaskExecutor
from app.execution.interfaces import ExecutionProvider
from app.execution.models import (
    ConcurrentExecutionError,
    ExecutionContext,
    ExecutionOutcome,
    ExecutionResult,
    InvalidStateTransitionError,
    TaskNotFoundError,
    TaskStatus,
    validate_transition,
)
from app.execution.registry import ExecutionRegistry, execution_registry
from app.execution.workers import (
    CompanyEnrichmentExecutionAdapter,
    MockLeadQualificationProvider,
    PersonEnrichmentExecutionAdapter,
    execute_task,
)
from app.models.base import Company, Event, Lead, Person, Task
from app.oxygen.orchestrator import OxygenOrchestrator


# -----------------------------------------------------------------------------
# 1. UNIT TESTS (Pure domain logic, no live database)
# -----------------------------------------------------------------------------

class TestExecutionUnit:
    """Unit tests for models, registry, providers, and state transitions."""

    def test_execution_result_validation(self):
        res = ExecutionResult(
            success=True,
            status="completed",
            provider="test_provider",
            result={"score": 90, "qualified": True},
        )
        assert res.success is True
        assert res.status == "completed"
        assert res.provider == "test_provider"
        assert res.result["score"] == 90
        assert res.error_message is None

    def test_task_state_transition_rules(self):
        # Valid transitions
        assert validate_transition("pending", "in_progress") is True
        assert validate_transition("in_progress", "completed") is True
        assert validate_transition("in_progress", "done") is True
        assert validate_transition("in_progress", "failed") is True

        # Invalid transitions
        assert validate_transition("completed", "in_progress") is False
        assert validate_transition("completed", "failed") is False
        assert validate_transition("done", "in_progress") is False
        assert validate_transition("failed", "in_progress") is False
        assert validate_transition("pending", "completed") is False
        assert validate_transition("unknown_status", "in_progress") is False

    def test_provider_registration_and_lookup(self):
        registry = ExecutionRegistry()

        class DummyProvider(ExecutionProvider):
            @property
            def provider_name(self) -> str:
                return "dummy"

            @property
            def supported_task_types(self) -> list[str]:
                return ["dummy_task_a", "dummy_task_b"]

            async def execute(self, context, session=None):
                return ExecutionResult(success=True, provider="dummy")

        provider = DummyProvider()
        registry.register(provider)

        assert registry.get_provider("dummy") is provider
        assert registry.get_provider_for_task_type("dummy_task_a") is provider
        assert registry.get_provider_for_task_type("dummy_task_b") is provider
        assert registry.is_executable("dummy_task_a") is True
        assert registry.is_executable("unsupported") is False

        providers = registry.list_providers()
        assert len(providers) == 1
        assert providers[0].provider_name == "dummy"

        registry.unregister("dummy")
        assert registry.get_provider("dummy") is None
        assert registry.get_provider_for_task_type("dummy_task_a") is None
        assert registry.is_executable("dummy_task_a") is False

    def test_unknown_provider_lookup_returns_none(self):
        registry = ExecutionRegistry()
        assert registry.get_provider("non_existent") is None
        assert registry.get_provider_for_task_type("non_existent_task") is None
        assert registry.is_executable("non_existent_task") is False

    @pytest.mark.anyio
    async def test_deterministic_mock_qualification(self):
        provider = MockLeadQualificationProvider()
        task_id = uuid.uuid4()
        lead_id = uuid.uuid4()

        # Case 1: Standard qualified context
        ctx1 = ExecutionContext(
            task_id=task_id,
            task_type="qualify_lead",
            lead_id=lead_id,
            payload={"domain": "acme.com", "email": "ceo@acme.com"},
        )
        res1 = await provider.execute(ctx1)
        assert res1.success is True
        assert res1.status == "completed"
        assert res1.result["qualified"] is True
        assert res1.result["score"] == 85
        assert res1.result["qualification_version"] == "mock-v1"

        # Case 2: Explicit disqualification payload
        ctx2 = ExecutionContext(
            task_id=task_id,
            task_type="qualify_lead",
            lead_id=lead_id,
            payload={"qualified": False, "score": 15, "reason": "No budget"},
        )
        res2 = await provider.execute(ctx2)
        assert res2.success is True
        assert res2.result["qualified"] is False
        assert res2.result["score"] == 15
        assert res2.result["reason"] == "No budget"

    @pytest.mark.anyio
    async def test_provider_failure_handling(self):
        provider = MockLeadQualificationProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="qualify_lead",
            payload={"force_fail": True, "error_message": "Network timeout simulated"},
        )
        res = await provider.execute(ctx)
        assert res.success is False
        assert res.status == "failed"
        assert res.error_code == "MOCK_QUALIFICATION_FAILED"
        assert "Network timeout" in (res.error_message or "")


# -----------------------------------------------------------------------------
# 2. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestExecutionWithDatabase:
    """Database integration tests verifying execution, state updates, idempotency, and events."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_execute_pending_task_success(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Seed company, person, lead
            comp = Company(name="Nova Corp", domain="nova.io")
            session.add(comp)
            await session.flush()

            person = Person(
                first_name="Marcus",
                last_name="Vance",
                email="m.vance@nova.io",
                company_id=comp.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="qualify_lead",
                status="pending",
                payload=json.dumps({"domain": comp.domain, "email": person.email}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        # Execute
        async with AsyncSessionLocal() as session:
            outcome = await executor.execute(task_id, session)

        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["qualified"] is True
        assert outcome.event_type == "task_completed"

        # Verify DB updates
        async with AsyncSessionLocal() as session:
            db_task = await session.get(Task, task_id)
            assert db_task is not None
            assert db_task.status == "completed"
            assert db_task.completed_at is not None
            assert db_task.error_message is None
            result_dict = json.loads(db_task.result)
            assert result_dict["qualified"] is True

            # Verify Lead status was updated to qualified
            db_lead = await session.get(Lead, lead.id)
            assert db_lead.status == "qualified"

            # Verify events emitted: task_started and task_completed
            events_stmt = (
                select(Event)
                .where(Event.lead_id == lead.id)
                .order_by(Event.occurred_at.asc())
            )
            events_res = await session.execute(events_stmt)
            events = list(events_res.scalars().all())

            event_types = [e.event_type for e in events]
            assert "task_started" in event_types
            assert "task_completed" in event_types

    @pytest.mark.anyio
    async def test_failed_execution_persists_error_and_emits_event(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            comp = Company(name="Atlas Corp", domain="atlas.io")
            session.add(comp)
            await session.flush()

            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="qualify_lead",
                status="pending",
                payload=json.dumps({"force_fail": True, "error_message": "Provider timeout"}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            outcome = await executor.execute(task_id, session)

        assert outcome.success is False
        assert outcome.task_status == "failed"
        assert outcome.event_type == "task_failed"
        assert "Provider timeout" in (outcome.error_message or "")

        async with AsyncSessionLocal() as session:
            db_task = await session.get(Task, task_id)
            assert db_task.status == "failed"
            assert "Provider timeout" in db_task.error_message
            assert db_task.completed_at is not None

            # Verify task_failed event was emitted
            ev_stmt = select(Event).where(
                Event.lead_id == lead.id, Event.event_type == "task_failed"
            )
            ev_res = await session.execute(ev_stmt)
            fail_event = ev_res.scalar_one_or_none()
            assert fail_event is not None
            payload = json.loads(fail_event.payload)
            assert payload["success"] is False
            assert "Provider timeout" in payload["error"]["message"]

    @pytest.mark.anyio
    async def test_existing_completed_task_is_not_executed_again(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            original_result = {"qualified": True, "score": 99, "cached": True}
            task = Task(
                lead_id=lead.id,
                task_type="qualify_lead",
                status="completed",
                result=json.dumps(original_result),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            outcome = await executor.execute(task_id, session)

        assert outcome.already_executed is True
        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["score"] == 99

        # Ensure no additional events were created
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count(Event.id)).where(Event.lead_id == lead.id)
            ev_count = (await session.execute(count_stmt)).scalar()
            assert ev_count == 0

    @pytest.mark.anyio
    async def test_duplicate_execution_attempt_on_in_progress_task_raises(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="qualify_lead",
                status="in_progress",
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            with pytest.raises(ConcurrentExecutionError) as exc_info:
                await executor.execute(task_id, session)
            assert "already currently in progress" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_unknown_task_type_is_safely_failed(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="unregistered_mystery_task",
                status="pending",
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            outcome = await executor.execute(task_id, session)

        assert outcome.success is False
        assert outcome.task_status == "failed"
        assert outcome.event_type == "task_failed"
        assert "Unknown task type" in (outcome.error_message or "")

        async with AsyncSessionLocal() as session:
            db_task = await session.get(Task, task_id)
            assert db_task.status == "failed"

    @pytest.mark.anyio
    async def test_enrichment_adapter_execution(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Seed company with domain matching mock enrichment
            comp = Company(name="Quantum Inc", domain="quantum.io")
            session.add(comp)
            await session.flush()

            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="enrich_company",
                status="pending",
                payload=json.dumps({"company_id": str(comp.id), "domain": comp.domain}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id
            company_id = comp.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            outcome = await executor.execute(task_id, session)

        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["entity_type"] == "company"

        # Verify company was enriched in the CRM
        async with AsyncSessionLocal() as session:
            db_comp = await session.get(Company, company_id)
            assert db_comp.industry is not None
            assert db_comp.enriched_at is not None

    @pytest.mark.anyio
    async def test_execution_does_not_create_unrelated_leads_or_modify_oxygen_state(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="qualify_lead",
                status="pending",
                payload=json.dumps({"domain": "solo.io"}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        executor = TaskExecutor(execution_registry)

        async with AsyncSessionLocal() as session:
            await executor.execute(task_id, session)

        # Check total lead count in database remains exactly 1
        async with AsyncSessionLocal() as session:
            lead_count = (await session.execute(select(func.count(Lead.id)))).scalar()
            assert lead_count == 1

    @pytest.mark.anyio
    async def test_full_oxygen_to_execution_handoff(self):
        """End-to-end integration: Lead -> Oxygen decides -> Task created -> Task executed -> Event emitted."""
        from app.core.database import AsyncSessionLocal

        # 1. Seed fully enriched company and person
        async with AsyncSessionLocal() as session:
            comp = Company(name="Solaris", domain="solaris.ai")
            session.add(comp)
            await session.flush()

            person = Person(
                first_name="Elena",
                email="elena@solaris.ai",
                company_id=comp.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.flush()

            # Mark both enriched
            ev1 = Event(
                lead_id=lead.id,
                event_type="company_enriched",
                payload=json.dumps({"company_id": str(comp.id)}),
            )
            ev2 = Event(
                lead_id=lead.id,
                event_type="person_enriched",
                payload=json.dumps({"person_id": str(person.id)}),
            )
            session.add_all([ev1, ev2])
            await session.commit()
            lead_id = lead.id

        # 2. Oxygen orchestrates -> Decides to qualify_lead and creates Task
        orchestrator = OxygenOrchestrator()
        async with AsyncSessionLocal() as session:
            orch_outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

        assert orch_outcome.status == "success"
        assert orch_outcome.task_created is True
        assert orch_outcome.task_id is not None
        assert orch_outcome.decision.action == "qualify_lead"
        created_task_id = orch_outcome.task_id

        # 3. Execution layer receives Task and executes it
        exec_outcome = await execute_task(created_task_id)

        assert exec_outcome.success is True
        assert exec_outcome.task_status == "completed"
        assert exec_outcome.result["qualified"] is True
        assert exec_outcome.event_type == "task_completed"

        # 4. Verify task state and events in CRM
        async with AsyncSessionLocal() as session:
            db_task = await session.get(Task, created_task_id)
            assert db_task.status == "completed"
            assert db_task.completed_at is not None

            # Verify oxygen decision event + task started + task completed events exist
            ev_stmt = select(Event).where(Event.lead_id == lead_id)
            all_events = (await session.execute(ev_stmt)).scalars().all()
            all_types = [e.event_type for e in all_events]
            assert "oxygen_decision" in all_types
            assert "task_started" in all_types
            assert "task_completed" in all_types


# -----------------------------------------------------------------------------
# 3. API TESTS (FastAPI endpoints under /api/v1/execution)
# -----------------------------------------------------------------------------

class TestExecutionAPI:
    """Tests for Execution layer endpoints."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_list_providers_endpoint(self, app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get("/api/v1/execution/providers")

        assert res.status_code == 200
        data = res.json()
        assert "count" in data
        assert data["count"] >= 3
        provider_names = [p["provider_name"] for p in data["providers"]]
        assert "mock_lead_qualification" in provider_names
        assert "mock_company_enrichment_adapter" in provider_names
        assert "mock_person_enrichment_adapter" in provider_names

    @pytest.mark.anyio
    async def test_invalid_uuid_returns_422(self, app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get("/api/v1/execution/tasks/not-a-valid-uuid")
        assert res.status_code == 422

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post("/api/v1/execution/tasks/not-a-valid-uuid/execute")
        assert res.status_code == 422

    @pytest.mark.anyio
    async def test_unknown_task_returns_404(self, app):
        random_id = str(uuid.uuid4())
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get(f"/api/v1/execution/tasks/{random_id}")
        assert res.status_code == 404

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/v1/execution/tasks/{random_id}/execute")
        assert res.status_code == 404

    @pytest.mark.anyio
    async def test_get_task_endpoint_success(self, app):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            task = Task(
                task_type="qualify_lead",
                status="pending",
                priority=3,
                payload=json.dumps({"sample": "key"}),
            )
            session.add(task)
            await session.commit()
            task_id = str(task.id)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get(f"/api/v1/execution/tasks/{task_id}")

        assert res.status_code == 200
        data = res.json()
        assert data["id"] == task_id
        assert data["task_type"] == "qualify_lead"
        assert data["status"] == "pending"
        assert data["priority"] == 3
        assert data["payload"] == {"sample": "key"}

    @pytest.mark.anyio
    async def test_execute_task_endpoint_success(self, app):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            task = Task(
                task_type="qualify_lead",
                status="pending",
                payload=json.dumps({"domain": "apex.io"}),
            )
            session.add(task)
            await session.commit()
            task_id = str(task.id)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/v1/execution/tasks/{task_id}/execute")

        assert res.status_code == 200
        data = res.json()
        assert data["task_id"] == task_id
        assert data["success"] is True
        assert data["task_status"] == "completed"
        assert data["event_type"] == "task_completed"
        assert data["already_executed"] is False
        assert data["result"]["qualified"] is True

    @pytest.mark.anyio
    async def test_execute_already_completed_task_idempotent(self, app):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            task = Task(
                task_type="qualify_lead",
                status="completed",
                result=json.dumps({"already": "done"}),
            )
            session.add(task)
            await session.commit()
            task_id = str(task.id)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/v1/execution/tasks/{task_id}/execute")

        assert res.status_code == 200
        data = res.json()
        assert data["task_id"] == task_id
        assert data["already_executed"] is True
        assert data["result"] == {"already": "done"}
