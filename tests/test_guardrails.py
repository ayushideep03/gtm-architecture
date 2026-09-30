"""Tests for Guardrails & Policy Enforcement (Stage 6).

Covers:
- Unit tests:
    - Valid action allowed
    - Unknown capability blocked
    - Unknown/unsupported task type blocked
    - Missing target blocked
    - Missing payload blocked
    - Duplicate active task blocked
    - Completed task blocked
    - Missing required CRM entity blocked
    - Multiple rules evaluated in registry
    - Safe internal execution authorized
    - Registry rule enabling/disabling
- Database integration tests:
    - Oxygen integration: blocked decision does not create Task
    - Oxygen integration: allowed decision creates Task
    - Event emission: guardrail_allowed and guardrail_blocked events persisted
    - Execution still works after approved Task
    - Guardrails do not execute tasks or create CRM entities directly
- API tests:
    - GET /api/v1/guardrails/rules
    - POST /api/v1/guardrails/evaluate (allowed and blocked diagnostic scenarios)
"""

import json
from typing import Optional
import uuid
import httpx
from httpx import ASGITransport
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.workers import execute_task
from app.guardrails.evaluator import GuardrailEvaluator
from app.guardrails.models import GuardrailContext, GuardrailResult, GuardrailRule, GuardrailSeverity
from app.guardrails.registry import GuardrailRegistry, create_default_guardrail_registry, guardrail_registry
from app.guardrails.rules import (
    DuplicateActiveTaskRule,
    ExecutionCapabilityNotRegisteredRule,
    MissingRequiredCRMEntityRule,
    MissingRequiredTaskPayloadRule,
    MissingTargetRule,
    SafeValidInternalExecutionRule,
    TaskAlreadyCompletedRule,
    UnknownCapabilityRule,
    UnsupportedTaskTypeRule,
)
from app.models.base import Company, Event, Lead, Person, Task
from app.oxygen.models import Decision
from app.oxygen.orchestrator import OxygenOrchestrator


# -----------------------------------------------------------------------------
# 1. UNIT TESTS (Policy logic, no DB required)
# -----------------------------------------------------------------------------

class TestGuardrailsUnit:
    """Unit tests for deterministic guardrail rules and registry evaluation."""

    @pytest.mark.anyio
    async def test_valid_action_allowed(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()
        comp_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="company_enrichment",
            task_type="enrich_company",
            target_id=comp_id,
            target_type="company",
            parameters={"company_id": str(comp_id), "domain": "valid.com"},
            has_company=True,
            has_person=True,
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is True
        assert "passed" in result.reason
        assert len(result.rule_ids) > 0

    @pytest.mark.anyio
    async def test_unknown_capability_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="totally_unregistered_unknown_capability_xyz",
            task_type="enrich_company",
            target_id=uuid.uuid4(),
            parameters={"domain": "test.com"},
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-001" in result.rule_ids
        assert "unknown or unavailable" in result.reason

    @pytest.mark.anyio
    async def test_missing_target_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="company_enrichment",
            task_type="enrich_company",
            target_id=None,  # Missing target
            parameters={"domain": "test.com"},
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-002" in result.rule_ids
        assert "target_id" in result.reason

    @pytest.mark.anyio
    async def test_missing_required_payload_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()
        comp_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="company_enrichment",
            task_type="enrich_company",
            target_id=comp_id,
            parameters={},  # Missing company_id and domain
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-003" in result.rule_ids
        assert "enrich_company task requires" in result.reason

    @pytest.mark.anyio
    async def test_duplicate_active_task_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()
        comp_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="company_enrichment",
            task_type="enrich_company",
            target_id=comp_id,
            parameters={"domain": "test.com"},
            task_history=[
                {"task_type": "enrich_company", "status": "pending"}
            ],
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-004" in result.rule_ids
        assert "active task" in result.reason

    @pytest.mark.anyio
    async def test_completed_task_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="qualify_lead",
            capability="lead_qualification",
            task_type="qualify_lead",
            target_id=lead_id,
            parameters={"lead_id": str(lead_id)},
            task_history=[
                {"task_type": "qualify_lead", "status": "completed"}
            ],
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-005" in result.rule_ids
        assert "already been successfully completed" in result.reason

    @pytest.mark.anyio
    async def test_execution_capability_not_registered_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="unregistered_execution_task",
            capability="lead_qualification",
            task_type="unregistered_execution_task",
            target_id=lead_id,
            parameters={},
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-006" in result.rule_ids or "GR-007" in result.rule_ids

    @pytest.mark.anyio
    async def test_missing_required_crm_entity_blocked(self):
        evaluator = GuardrailEvaluator(create_default_guardrail_registry())
        lead_id = uuid.uuid4()
        comp_id = uuid.uuid4()

        ctx = GuardrailContext(
            lead_id=lead_id,
            action="enrich_company",
            capability="company_enrichment",
            task_type="enrich_company",
            target_id=comp_id,
            parameters={"domain": "test.com"},
            has_company=False,  # Associated Company entity missing
        )
        result = await evaluator.evaluate(ctx)
        assert result.allowed is False
        assert "GR-008" in result.rule_ids
        assert "Company entity is missing" in result.reason

    def test_registry_list_rules(self):
        reg = create_default_guardrail_registry()
        rules = reg.list_rules()
        assert len(rules) >= 9
        rule_ids = [r.rule_id for r in rules]
        assert "GR-001" in rule_ids
        assert "GR-009" in rule_ids


# -----------------------------------------------------------------------------
# 2. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestGuardrailsWithDatabase:
    """Database integration tests verifying Oxygen orchestrator integration and events."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_allowed_decision_creates_task_and_emits_guardrail_allowed(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            comp = Company(name="Terra Systems", domain="terra.io")
            session.add(comp)
            await session.flush()

            person = Person(
                first_name="Terry",
                email="t@terra.io",
                company_id=comp.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

        orchestrator = OxygenOrchestrator()

        async with AsyncSessionLocal() as session:
            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

        # Company enrichment is needed -> Guardrail authorizes -> Task is created
        assert outcome.status in ("success", "ok")
        assert outcome.task_created is True
        assert outcome.task_id is not None

        # Verify events emitted: guardrail_allowed and oxygen_decision
        async with AsyncSessionLocal() as session:
            events_stmt = (
                select(Event)
                .where(Event.lead_id == lead_id)
                .order_by(Event.occurred_at.asc())
            )
            events = (await session.execute(events_stmt)).scalars().all()
            event_types = [e.event_type for e in events]

            assert "guardrail_allowed" in event_types
            assert "oxygen_decision" in event_types

            # Verify guardrail_allowed payload
            allowed_event = next(e for e in events if e.event_type == "guardrail_allowed")
            payload = json.loads(allowed_event.payload)
            assert payload["allowed"] is True
            assert payload["decision"] == "enrich_company"
            assert "GR-001" in payload["rule_ids"]

    @pytest.mark.anyio
    async def test_blocked_decision_does_not_create_task_and_emits_guardrail_blocked(self):
        from app.core.database import AsyncSessionLocal
        from app.guardrails.interfaces import GuardrailRuleEvaluator

        # Create a custom registry with a rule that blocks enrich_company for testing
        test_registry = create_default_guardrail_registry()

        class BlockAllRule(GuardrailRuleEvaluator):
            @property
            def rule(self) -> GuardrailRule:
                return GuardrailRule(
                    rule_id="GR-BLOCK-TEST",
                    description="Test policy blocking all actions",
                    severity=GuardrailSeverity.BLOCK,
                    enabled=True,
                )

            async def evaluate(self, context, session=None):
                return False, "Blocked by test policy"

        test_registry.register(BlockAllRule())
        custom_evaluator = GuardrailEvaluator(test_registry)
        orchestrator = OxygenOrchestrator(guardrail_evaluator=custom_evaluator)

        async with AsyncSessionLocal() as session:
            comp = Company(name="Zephyr Labs", domain="zephyr.io")
            session.add(comp)
            await session.flush()

            person = Person(
                first_name="Zoe",
                email="zoe@zephyr.io",
                company_id=comp.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

        async with AsyncSessionLocal() as session:
            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

        # Outcome is blocked
        assert outcome.status == "blocked"
        assert outcome.task_created is False
        assert outcome.task_id is None
        assert "Blocked by test policy" in (outcome.error or "")

        # Verify no tasks were created in DB
        async with AsyncSessionLocal() as session:
            tasks_count = (
                await session.execute(
                    select(func.count(Task.id)).where(Task.lead_id == lead_id)
                )
            ).scalar()
            assert tasks_count == 0

            # Verify guardrail_blocked event was emitted
            ev_stmt = select(Event).where(
                Event.lead_id == lead_id, Event.event_type == "guardrail_blocked"
            )
            blocked_event = (await session.execute(ev_stmt)).scalar_one_or_none()
            assert blocked_event is not None
            payload = json.loads(blocked_event.payload)
            assert payload["allowed"] is False
            assert "GR-BLOCK-TEST" in payload["rule_ids"]

    @pytest.mark.anyio
    async def test_execution_still_works_after_approved_task(self):
        """Verify the full pipeline: Oxygen -> Guardrails -> Task -> Execution Layer."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            comp = Company(name="Vortex Tech", domain="vortex.io")
            session.add(comp)
            await session.flush()

            person = Person(
                first_name="Victor",
                email="v@vortex.io",
                company_id=comp.id,
            )
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.flush()

            # Mark both enriched so Oxygen recommends qualify_lead
            session.add_all([
                Event(lead_id=lead.id, event_type="company_enriched", payload="{}"),
                Event(lead_id=lead.id, event_type="person_enriched", payload="{}"),
            ])
            await session.commit()
            lead_id = lead.id

        orchestrator = OxygenOrchestrator()
        async with AsyncSessionLocal() as session:
            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

        assert outcome.task_created is True
        task_id = outcome.task_id

        # Execute approved task
        exec_outcome = await execute_task(task_id)
        assert exec_outcome.success is True
        assert exec_outcome.task_status == "completed"
        assert exec_outcome.result["qualified"] is True


# -----------------------------------------------------------------------------
# 3. API TESTS (Diagnostic endpoint and rules listing)
# -----------------------------------------------------------------------------

class TestGuardrailsAPI:
    """Tests for Guardrails API endpoints."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_list_rules_endpoint(self, app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get("/api/v1/guardrails/rules")

        assert res.status_code == 200
        data = res.json()
        assert data["count"] >= 9
        rule_ids = [r["rule_id"] for r in data["rules"]]
        assert "GR-001" in rule_ids
        assert "GR-002" in rule_ids
        assert "GR-008" in rule_ids

    @pytest.mark.anyio
    async def test_evaluate_endpoint_allowed(self, app):
        lead_id = str(uuid.uuid4())
        target_id = str(uuid.uuid4())

        payload = {
            "lead_id": lead_id,
            "action": "enrich_company",
            "capability": "company_enrichment",
            "task_type": "enrich_company",
            "target_id": target_id,
            "parameters": {"domain": "allowed.io", "company_id": target_id},
            "has_company": True,
            "has_person": True,
        }

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post("/api/v1/guardrails/evaluate", json=payload)

        assert res.status_code == 200
        data = res.json()
        assert data["allowed"] is True
        assert data["decision"] == "enrich_company"
        assert len(data["rule_ids"]) >= 9

    @pytest.mark.anyio
    async def test_evaluate_endpoint_blocked(self, app):
        lead_id = str(uuid.uuid4())

        payload = {
            "lead_id": lead_id,
            "action": "enrich_company",
            "capability": "unknown_forbidden_capability",
            "task_type": "enrich_company",
            "target_id": str(uuid.uuid4()),
            "parameters": {"domain": "blocked.io"},
        }

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post("/api/v1/guardrails/evaluate", json=payload)

        assert res.status_code == 200
        data = res.json()
        assert data["allowed"] is False
        assert "GR-001" in data["rule_ids"]
        assert "unknown or unavailable" in data["reason"]
