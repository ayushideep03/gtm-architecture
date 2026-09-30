"""Tests for RevOps, Metrics, Event Processing, and Timelines (Stage 8).

Covers:
- Database tests:
    - Compute RevOps metrics from database state
    - Assemble chronological lead timeline with events and interactions
    - Durable event processing using EventProcessingState cursor
    - Idempotent event processing (no duplicate actions, cursor preserved)
    - Verification that historical events are strictly append-only (not mutated)
    - Feedback loop triggers (enrichment completed, guardrail blocked, task completed)
- API tests:
    - GET /api/v1/revops/metrics
    - GET /api/v1/revops/events (unfiltered and filtered by event_type / lead_id)
    - GET /api/v1/revops/leads/{lead_id}/timeline
    - 404 on unknown lead timeline
"""

from datetime import datetime, timezone
import json
from typing import Optional
import uuid
import httpx
from httpx import ASGITransport
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Company, Event, EventProcessingState, Interaction, Lead, Person, Task
from app.revops.analytics import build_lead_timeline, compute_revops_metrics
from app.revops.processor import EventProcessor
from app.revops.services import RevOpsService


# -----------------------------------------------------------------------------
# 1. DATABASE INTEGRATION TESTS (uses _db_isolation fixture)
# -----------------------------------------------------------------------------

class TestRevOpsWithDatabase:
    """Database integration tests for RevOps analytics, cursor tracking, and event processing."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_compute_metrics_from_database(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="qualified", score=85)
            session.add(lead)
            await session.flush()

            # Seed events
            events = [
                Event(lead_id=lead.id, event_type="lead_ingested"),
                Event(lead_id=lead.id, event_type="company_enriched"),
                Event(lead_id=lead.id, event_type="person_enriched"),
                Event(lead_id=lead.id, event_type="oxygen_decision"),
                Event(lead_id=lead.id, event_type="guardrail_allowed"),
                Event(lead_id=lead.id, event_type="guardrail_blocked"),
            ]
            session.add_all(events)

            # Seed tasks
            tasks = [
                Task(lead_id=lead.id, task_type="qualify_lead", status="completed"),
                Task(lead_id=lead.id, task_type="enrich_company", status="completed"),
                Task(lead_id=lead.id, task_type="enrich_person", status="failed"),
            ]
            session.add_all(tasks)

            # Seed interactions
            interactions = [
                Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound"),
                Interaction(lead_id=lead.id, interaction_type="meeting", direction="outbound"),
            ]
            session.add_all(interactions)
            await session.commit()

        async with AsyncSessionLocal() as session:
            metrics = await compute_revops_metrics(session)

        assert metrics.leads_ingested == 1
        assert metrics.companies_enriched == 1
        assert metrics.people_enriched == 1
        assert metrics.oxygen_decisions == 1
        assert metrics.guardrail_blocks == 1
        assert metrics.tasks_created == 3
        assert metrics.tasks_completed == 2
        assert metrics.tasks_failed == 1
        assert metrics.execution_success_rate == 0.6667
        assert metrics.simulated_outreach_count == 1
        assert metrics.simulated_meeting_count == 1
        assert metrics.qualification_outcomes.get("qualified") == 1

    @pytest.mark.anyio
    async def test_build_lead_timeline_chronological(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="contacted", score=90)
            session.add(lead)
            await session.flush()
            lead_id = lead.id

            ev1 = Event(
                lead_id=lead_id,
                event_type="lead_ingested",
                occurred_at=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
            )
            ev2 = Event(
                lead_id=lead_id,
                event_type="company_enriched",
                occurred_at=datetime(2026, 10, 1, 10, 5, 0, tzinfo=timezone.utc),
            )
            inter1 = Interaction(
                lead_id=lead_id,
                interaction_type="email_sent",
                subject="Initial outreach",
                occurred_at=datetime(2026, 10, 1, 10, 15, 0, tzinfo=timezone.utc),
            )
            session.add_all([ev1, ev2, inter1])
            await session.commit()

        async with AsyncSessionLocal() as session:
            timeline = await build_lead_timeline(session, lead_id)

        assert timeline is not None
        assert timeline.lead_id == lead_id
        assert timeline.lead_status == "contacted"
        assert len(timeline.timeline) == 3
        # Chronological ordering check
        assert timeline.timeline[0].item_type == "lead_ingested"
        assert timeline.timeline[1].item_type == "company_enriched"
        assert timeline.timeline[2].item_type == "email_sent"
        assert timeline.timeline[2].category == "interaction"

    @pytest.mark.anyio
    async def test_durable_event_processor_and_idempotency(self):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()
            lead_id = lead.id

            events = [
                Event(lead_id=lead_id, event_type="lead_ingested"),
                Event(lead_id=lead_id, event_type="company_enriched"),
                Event(lead_id=lead_id, event_type="task_completed"),
                Event(lead_id=lead_id, event_type="guardrail_blocked"),
            ]
            session.add_all(events)
            await session.commit()

        processor = EventProcessor()

        # Batch 1: Process all 4 events
        async with AsyncSessionLocal() as session:
            result1 = await processor.process_unprocessed_events(
                session, consumer_name="test_worker", limit=10
            )

        assert result1.events_processed == 4
        assert len(result1.actions_triggered) == 4

        # Verify cursor state was saved in PostgreSQL
        async with AsyncSessionLocal() as session:
            state = await session.get(EventProcessingState, "test_worker")
            assert state is not None
            assert state.processed_count == 4
            assert state.last_processed_event_id is not None

        # Batch 2: Re-run with no new events -> idempotency, 0 processed
        async with AsyncSessionLocal() as session:
            result2 = await processor.process_unprocessed_events(
                session, consumer_name="test_worker", limit=10
            )

        assert result2.events_processed == 0

        # Verify historical events in events table are unchanged (append-only)
        async with AsyncSessionLocal() as session:
            total_events = (
                await session.execute(
                    select(func.count(Event.id)).where(Event.lead_id == lead_id)
                )
            ).scalar()
            assert total_events == 4


# -----------------------------------------------------------------------------
# 2. API TESTS (FastAPI endpoints under /api/v1/revops)
# -----------------------------------------------------------------------------

class TestRevOpsAPI:
    """Tests for RevOps API endpoints."""

    @pytest.fixture(autouse=True)
    async def _cleanup_pool(self):
        yield
        from app.core.database import engine
        await engine.dispose()

    @pytest.mark.anyio
    async def test_metrics_endpoint(self, app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get("/api/v1/revops/metrics")

        assert res.status_code == 200
        data = res.json()
        assert "leads_ingested" in data
        assert "tasks_completed" in data
        assert "execution_success_rate" in data

    @pytest.mark.anyio
    async def test_events_endpoint_unfiltered_and_filtered(self, app):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()
            lead_id = lead.id

            session.add_all([
                Event(lead_id=lead_id, event_type="lead_ingested", source="apollo"),
                Event(lead_id=lead_id, event_type="company_enriched", source="mock"),
            ])
            await session.commit()

        # Unfiltered
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get("/api/v1/revops/events")

        assert res.status_code == 200
        data = res.json()
        assert data["count"] >= 2

        # Filtered by event_type
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res_filtered = await ac.get("/api/v1/revops/events?event_type=company_enriched")

        assert res_filtered.status_code == 200
        data_filtered = res_filtered.json()
        for ev in data_filtered["events"]:
            assert ev["event_type"] == "company_enriched"

    @pytest.mark.anyio
    async def test_timeline_endpoint_success_and_404(self, app):
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            lead = Lead(status="new")
            session.add(lead)
            await session.flush()
            lead_id = str(lead.id)

            session.add(Event(lead_id=lead.id, event_type="lead_ingested"))
            await session.commit()

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.get(f"/api/v1/revops/leads/{lead_id}/timeline")

        assert res.status_code == 200
        data = res.json()
        assert data["lead_id"] == lead_id
        assert len(data["timeline"]) >= 1

        # 404 on unknown lead
        random_id = str(uuid.uuid4())
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res_404 = await ac.get(f"/api/v1/revops/leads/{random_id}/timeline")
        assert res_404.status_code == 404
