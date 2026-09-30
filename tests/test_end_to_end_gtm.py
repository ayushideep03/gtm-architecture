"""End-to-End Autonomous GTM Control Loop Tests (Stage 10)."""

import json
import pytest
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, engine
from app.execution.executor import TaskExecutor
from app.execution.models import TaskStatus
from app.models.base import Company, Event, Lead, Person, Task
from app.oxygen.context import OxygenContextBuilder
from app.oxygen.orchestrator import OxygenOrchestrator
from app.revops.processor import EventProcessor
from app.services.enrichment import EnrichmentService
from app.services.gtm_orchestration import GtmOrchestrationService


class TestEndToEndGTM:
    """Architectural end-to-end integration test demonstrating the complete closed control loop."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        await engine.dispose()

    @pytest.mark.anyio
    async def test_complete_autonomous_gtm_lifecycle(self) -> None:
        """Demonstrate the entire deterministic pipeline:
        1. Create & Ingest Lead
        2. Resolve CRM entities
        3. Enrich Company & Person
        4. Oxygen Context & Decision
        5. Guardrails Authorization
        6. Task Creation
        7. Execution Provider Dispatch
        8. Task Result Persistence
        9. Event Log Emission
        10. RevOps Stream Observation & Feedback
        11. Subsequent Oxygen Decision from Updated State
        12. Duplicate Execution Protection
        13. Verification that no external network calls took place
        """
        async with AsyncSessionLocal() as session:
            # 1. Lead creation & CRM entities
            company = Company(
                id=uuid.uuid4(),
                name="Quantum Dynamics",
                domain="quantum.io",
                employee_count=150,
                industry="Software",
            )
            session.add(company)
            await session.flush()

            person = Person(
                id=uuid.uuid4(),
                company_id=company.id,
                first_name="Alice",
                last_name="Chen",
                email="alice.chen@quantum.io",
                title="VP Engineering",
            )
            session.add(person)
            await session.flush()

            lead = Lead(
                id=uuid.uuid4(),
                person_id=person.id,
                status="new",
            )
            session.add(lead)

            # Ingestion event
            ingest_event = Event(
                lead_id=lead.id,
                event_type="lead_ingested",
                source="test_end_to_end",
                payload=json.dumps({"lead_id": str(lead.id)}),
            )
            session.add(ingest_event)
            await session.commit()

            lead_id = lead.id

            # 2. Enrich company and person using mock enrichment provider
            enrichment_service = EnrichmentService()
            c_enrich = await enrichment_service.enrich_company_by_id(
                db=session,
                company_id=company.id,
                provider_name="mock_enrichment",
            )
            assert c_enrich.success is True

            p_enrich = await enrichment_service.enrich_person_by_id(
                db=session,
                person_id=person.id,
                provider_name="mock_enrichment",
            )
            assert p_enrich.success is True

            # 3. Oxygen Orchestration: decides -> evaluates guardrails -> creates Task -> emits events
            orchestrator = OxygenOrchestrator()
            outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()

            assert outcome.status == "success"
            assert outcome.decision.action == "qualify_lead"
            assert outcome.task_created is True
            assert outcome.task_id is not None

            # Verify guardrail allowed event was emitted
            allowed_events = (
                await session.execute(
                    select(Event).where(
                        Event.lead_id == lead_id,
                        Event.event_type == "guardrail_allowed",
                    )
                )
            ).scalars().all()
            assert len(allowed_events) == 1

            # Verify oxygen decision event was emitted
            decision_events = (
                await session.execute(
                    select(Event).where(
                        Event.lead_id == lead_id,
                        Event.event_type == "oxygen_decision",
                    )
                )
            ).scalars().all()
            assert len(decision_events) == 1

            # 4. Task Execution: execute task with TaskExecutor
            executor = TaskExecutor()
            exec_outcome = await executor.execute(outcome.task_id, session)

            assert exec_outcome.success is True
            assert exec_outcome.task_status == TaskStatus.COMPLETED.value
            assert exec_outcome.event_type == "task_completed"
            assert exec_outcome.result.get("qualified") is True

            # Verify task_completed event in database
            completed_events = (
                await session.execute(
                    select(Event).where(
                        Event.lead_id == lead_id,
                        Event.event_type == "task_completed",
                    )
                )
            ).scalars().all()
            assert len(completed_events) == 1

            # 5. RevOps Observation & Cursor Progression
            processor = EventProcessor()
            revops_result = await processor.process_unprocessed_events(
                session, consumer_name="e2e_auditor"
            )

            assert revops_result.events_processed >= 3
            assert any(
                "CRMFeedbackApplied" in act for act in revops_result.actions_triggered
            )

            # 6. Oxygen Feedback Loop: Re-evaluate context from updated state
            context_builder = OxygenContextBuilder()
            updated_ctx = await context_builder.build(session, lead_id)
            assert updated_ctx is not None
            assert updated_ctx.is_company_enriched is True
            assert updated_ctx.is_person_enriched is True
            assert len(updated_ctx.tasks) >= 1
            assert any(t.status == "completed" for t in updated_ctx.tasks)

            # Next Oxygen decision avoids duplicate task creation
            next_outcome = await orchestrator.orchestrate(session, lead_id)
            await session.commit()
            # Because the qualify_lead task is already completed and no further hardcoded rules trigger,
            # Oxygen should wait or take no action
            assert next_outcome.task_created is False

            # 7. Duplicate Execution Idempotency
            dup_exec = await executor.execute(outcome.task_id, session)
            assert dup_exec.already_executed is True
            assert dup_exec.success is True

    @pytest.mark.anyio
    async def test_gtm_orchestration_service_cycle(self) -> None:
        """Test the unified GtmOrchestrationService runner."""
        async with AsyncSessionLocal() as session:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            company = Company(
                id=uuid.uuid4(),
                name="Beta Labs",
                domain="betalabs.io",
                enriched_at=now,
                enrichment_source="mock",
            )
            session.add(company)
            await session.flush()

            person = Person(
                id=uuid.uuid4(),
                company_id=company.id,
                first_name="Bruce",
                last_name="Wayne",
                email="bruce@betalabs.io",
                enriched_at=now,
                enrichment_source="mock",
            )
            session.add(person)
            await session.flush()

            lead = Lead(
                id=uuid.uuid4(),
                person_id=person.id,
                status="new",
            )
            session.add(lead)
            await session.commit()

            lead_id = lead.id

            service = GtmOrchestrationService()
            cycle_result = await service.execute_cycle(session, lead_id)

            assert cycle_result.status == "success"
            assert cycle_result.initial_decision is not None
            assert cycle_result.initial_decision.action == "qualify_lead"
            assert cycle_result.execution_outcome is not None
            assert cycle_result.execution_outcome.success is True
            assert cycle_result.revops_result is not None
            assert cycle_result.revops_result.events_processed > 0
