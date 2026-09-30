"""
Tests for database configuration, ORM model definitions, and schema validation.

NOTE: These tests verify model structure and Pydantic schema behaviour.
They do NOT require a live PostgreSQL connection — that's covered in
test_health.py via /health/db.
"""

import uuid
from datetime import timezone

import pytest


# ── Model structure tests ─────────────────────────────────────────────────────

class TestModelDefinitions:
    """ORM models should be importable and have the expected columns."""

    def test_company_model_imports(self):
        from app.models import Company
        assert Company.__tablename__ == "companies"

    def test_person_model_imports(self):
        from app.models import Person
        assert Person.__tablename__ == "people"

    def test_lead_model_imports(self):
        from app.models import Lead
        assert Lead.__tablename__ == "leads"

    def test_interaction_model_imports(self):
        from app.models import Interaction
        assert Interaction.__tablename__ == "interactions"

    def test_task_model_imports(self):
        from app.models import Task
        assert Task.__tablename__ == "tasks"

    def test_event_model_imports(self):
        from app.models import Event
        assert Event.__tablename__ == "events"

    def test_base_metadata_has_all_tables(self):
        from app.core.database import Base
        from app.models import Base as ModelsBase  # noqa: F401 — triggers model registration

        # Force import of all models
        import app.models  # noqa: F401

        tables = set(Base.metadata.tables.keys())
        expected = {"companies", "people", "leads", "interactions", "tasks", "events"}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"


# ── Pydantic schema tests ─────────────────────────────────────────────────────

class TestSchemas:
    """Pydantic schemas should validate input and reject invalid data."""

    def test_company_create_schema(self):
        from app.schemas import CompanyCreate
        company = CompanyCreate(name="Acme Corp", domain="acme.com")
        assert company.name == "Acme Corp"
        assert company.domain == "acme.com"

    def test_person_create_schema(self):
        from app.schemas import PersonCreate
        person = PersonCreate(first_name="Alice", last_name="Smith", email="alice@example.com")
        assert person.first_name == "Alice"
        assert person.email == "alice@example.com"

    def test_lead_create_valid_status(self):
        from app.schemas import LeadCreate
        lead = LeadCreate(status="new")
        assert lead.status == "new"

    def test_lead_create_invalid_status(self):
        from app.schemas import LeadCreate
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            LeadCreate(status="invalid_status_value")

    def test_lead_all_valid_statuses(self):
        from app.schemas import LeadCreate, VALID_LEAD_STATUSES
        for status in VALID_LEAD_STATUSES:
            lead = LeadCreate(status=status)
            assert lead.status == status

    def test_event_create_schema(self):
        from app.schemas import EventCreate
        event = EventCreate(
            event_type="lead_created",
            source="test",
        )
        assert event.event_type == "lead_created"

    def test_task_create_schema(self):
        from app.schemas import TaskCreate
        task = TaskCreate(task_type="enrich_lead", priority=3)
        assert task.task_type == "enrich_lead"
        assert task.priority == 3

    def test_interaction_create_schema(self):
        from app.schemas import InteractionCreate
        iid = uuid.uuid4()
        interaction = InteractionCreate(
            lead_id=iid,
            interaction_type="email_sent",
            direction="outbound",
        )
        assert interaction.lead_id == iid
        assert interaction.direction == "outbound"

    def test_company_read_from_orm_attributes(self):
        """CompanyRead.model_validate should work with ORM-style objects."""
        from app.schemas import CompanyRead
        from datetime import datetime

        class FakeCompany:
            id = uuid.uuid4()
            name = "Test Co"
            domain = "test.co"
            industry = "SaaS"
            employee_count = 50
            country = "US"
            city = "Austin"
            linkedin_url = None
            website = "https://test.co"
            created_at = datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

        result = CompanyRead.model_validate(FakeCompany())
        assert result.name == "Test Co"
        assert isinstance(result.id, uuid.UUID)
