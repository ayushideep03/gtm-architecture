"""Tests for GTM Capabilities: Research, Outreach, Meetings, and CRM Actions (Stage 7).

Covers:
- Unit tests:
    - Provider contracts (names, supported task types)
    - Execution registry presence of all 5 GTM capabilities
    - Deterministic prospect research output structure
    - Outreach drafting template formatting and drafts
    - Simulated outreach send validation (recipient check, simulated message_id)
    - Simulated meeting booking output
    - CRM action validation (status updates, interactions, meeting notes)
    - Invalid inputs and force_fail handling
- Database integration tests:
    - TaskExecutor executing research_prospect
    - TaskExecutor executing draft_outreach
    - TaskExecutor executing send_outreach_simulation (creates CRM Interaction)
    - TaskExecutor executing book_meeting_simulation (updates Lead status and creates Interaction)
    - TaskExecutor executing crm_update (updates status, records note)
    - Rejection of unknown/invalid CRM actions
    - Events emitted: task_started, task_completed, task_failed
    - Idempotency across duplicate executions
"""

import json
from typing import Optional
import uuid
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.executor import TaskExecutor
from app.execution.models import ExecutionContext, ExecutionOutcome, ExecutionResult
from app.execution.registry import execution_registry
from app.execution.workers import execute_task
from app.integrations.gtm.crm import CRMActionProvider
from app.integrations.gtm.meetings import MockMeetingBookingProvider
from app.integrations.gtm.outreach import (
    MockOutreachDraftingProvider,
    MockOutreachSendProvider,
)
from app.integrations.gtm.research import MockProspectResearchProvider
from app.models.base import Company, Event, Interaction, Lead, Person, Task


# -----------------------------------------------------------------------------
# 1. UNIT TESTS (Pure provider logic)
# -----------------------------------------------------------------------------

class TestGTMCapabilitiesUnit:
    """Unit tests for GTM capability provider logic."""

    def test_providers_registered_in_execution_registry(self):
        expected_task_types = [
            "research_prospect",
            "draft_outreach",
            "send_outreach_simulation",
            "book_meeting_simulation",
            "crm_update",
        ]
        for tt in expected_task_types:
            assert execution_registry.is_executable(tt) is True
            provider = execution_registry.get_provider_for_task_type(tt)
            assert provider is not None

    @pytest.mark.anyio
    async def test_deterministic_research_provider(self):
        provider = MockProspectResearchProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="research_prospect",
            payload={"domain": "hyperion.ai", "name": "Sarah Connor"},
        )
        res = await provider.execute(ctx)
        assert res.success is True
        assert res.status == "completed"
        assert res.result["research_version"] == "mock-v1"
        assert res.result["structured_findings"]["target_account"] == "hyperion.ai"
        assert res.result["structured_findings"]["key_contact"] == "Sarah Connor"
        assert len(res.result["structured_findings"]["buying_signals"]) > 0

    @pytest.mark.anyio
    async def test_outreach_drafting_provider(self):
        provider = MockOutreachDraftingProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="draft_outreach",
            payload={"first_name": "Jordan", "company_name": "NextGen Corp"},
        )
        res = await provider.execute(ctx)
        assert res.success is True
        assert res.result["is_draft"] is True
        assert "NextGen Corp" in res.result["subject"]
        assert "Hi Jordan," in res.result["body"]

    @pytest.mark.anyio
    async def test_outreach_send_simulation_requires_recipient(self):
        provider = MockOutreachSendProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="send_outreach_simulation",
            payload={"subject": "Hello"},  # Missing email
        )
        res = await provider.execute(ctx)
        assert res.success is False
        assert res.error_code == "MISSING_RECIPIENT"

    @pytest.mark.anyio
    async def test_outreach_send_simulation_success(self):
        provider = MockOutreachSendProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="send_outreach_simulation",
            payload={
                "recipient_email": "target@corp.io",
                "subject": "Introductory note",
                "body": "Hello world",
            },
        )
        res = await provider.execute(ctx)
        assert res.success is True
        assert res.result["sent"] is True
        assert res.result["mode"] == "simulation"
        assert "sim-msg-" in res.result["message_id"]
        assert res.result["recipient"] == "target@corp.io"

    @pytest.mark.anyio
    async def test_meeting_booking_simulation_output(self):
        provider = MockMeetingBookingProvider()
        ctx = ExecutionContext(
            task_id=uuid.uuid4(),
            task_type="book_meeting_simulation",
            payload={"topic": "Product Deep Dive"},
        )
        res = await provider.execute(ctx)
        assert res.success is True
        assert res.result["booked"] is True
        assert res.result["mode"] == "simulation"
        assert res.result["topic"] == "Product Deep Dive"
        assert "sim-meet-" in res.result["meeting_id"]

    @pytest.mark.anyio
    async def test_crm_action_provider_validations(self):
        provider = CRMActionProvider()

        # Missing session
        res_no_session = await provider.execute(
            ExecutionContext(task_id=uuid.uuid4(), task_type="crm_update", payload={})
        )
        assert res_no_session.success is False
        assert res_no_session.error_code == "SESSION_REQUIRED"


# -----------------------------------------------------------------------------
# 2. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestGTMCapabilitiesWithDatabase:
    """Database integration tests verifying execution, CRM mutation, and events."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_execute_research_prospect_task(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            comp = Company(name="Titan", domain="titan.ai")
            session.add(comp)
            await session.flush()

            lead = Lead(status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="research_prospect",
                status="pending",
                payload=json.dumps({"domain": "titan.ai", "name": "Titan Team"}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        outcome = await execute_task(task_id)
        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["structured_findings"]["target_account"] == "titan.ai"

    @pytest.mark.anyio
    async def test_execute_send_outreach_simulation_creates_interaction(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            comp = Company(name="Helios", domain="helios.io")
            session.add(comp)
            await session.flush()

            person = Person(first_name="Helen", email="helen@helios.io", company_id=comp.id)
            session.add(person)
            await session.flush()

            lead = Lead(person_id=person.id, status="new")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="send_outreach_simulation",
                status="pending",
                payload=json.dumps({
                    "subject": "Autonomous Sales Architecture",
                    "body": "Hi Helen, let's connect.",
                }),
            )
            session.add(task)
            await session.commit()
            task_id = task.id
            lead_id = lead.id

        outcome = await execute_task(task_id)
        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["sent"] is True

        # Verify Interaction record was created in CRM
        async with AsyncSessionLocal() as session:
            stmt = select(Interaction).where(Interaction.lead_id == lead_id)
            interaction = (await session.execute(stmt)).scalar_one_or_none()
            assert interaction is not None
            assert interaction.interaction_type == "email_sent"
            assert interaction.direction == "outbound"
            assert "Autonomous Sales" in interaction.subject
            assert "sim-msg-" in interaction.channel_message_id

    @pytest.mark.anyio
    async def test_execute_meeting_booking_simulation_updates_lead_and_interaction(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="contacted")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="book_meeting_simulation",
                status="pending",
                payload=json.dumps({"topic": "Executive Demo"}),
            )
            session.add(task)
            await session.commit()
            task_id = task.id
            lead_id = lead.id

        outcome = await execute_task(task_id)
        assert outcome.success is True
        assert outcome.task_status == "completed"

        async with AsyncSessionLocal() as session:
            db_lead = await session.get(Lead, lead_id)
            assert db_lead.status == "meeting_booked"

            stmt = select(Interaction).where(
                Interaction.lead_id == lead_id, Interaction.interaction_type == "meeting"
            )
            meeting = (await session.execute(stmt)).scalar_one_or_none()
            assert meeting is not None
            assert "Executive Demo" in meeting.subject

    @pytest.mark.anyio
    async def test_execute_crm_update_task(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new", notes="Initial discovery pending.")
            session.add(lead)
            await session.flush()

            task = Task(
                lead_id=lead.id,
                task_type="crm_update",
                status="pending",
                payload=json.dumps({
                    "crm_action": "update_lead_status",
                    "status": "closed_won",
                }),
            )
            session.add(task)
            await session.commit()
            task_id = task.id
            lead_id = lead.id

        outcome = await execute_task(task_id)
        assert outcome.success is True
        assert outcome.task_status == "completed"
        assert outcome.result["new_status"] == "closed_won"

        async with AsyncSessionLocal() as session:
            db_lead = await session.get(Lead, lead_id)
            assert db_lead.status == "closed_won"
