"""initial schema with lead_ingested event type

Revision ID: 0001
Revises:
Create Date: 2026-09-30

Creates all 6 domain tables:
    companies, people, leads, interactions, tasks, events

Enum types:
    lead_status       (9 values)
    interaction_type  (7 values)
    interaction_direction (2 values)
    task_status       (5 values)
    event_type        (10 values, includes lead_ingested added in Step 2)

All PKs are UUIDs. All timestamps are timezone-aware.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types ────────────────────────────────────────────────────────────
    lead_status = postgresql.ENUM(
        "new", "enriching", "qualified", "disqualified",
        "contacted", "replied", "meeting_booked", "closed_won", "closed_lost",
        name="lead_status",
    )
    lead_status.create(op.get_bind(), checkfirst=True)

    interaction_type = postgresql.ENUM(
        "email_sent", "email_received", "call",
        "linkedin_message", "linkedin_connection", "meeting", "note",
        name="interaction_type",
    )
    interaction_type.create(op.get_bind(), checkfirst=True)

    interaction_direction = postgresql.ENUM(
        "inbound", "outbound",
        name="interaction_direction",
    )
    interaction_direction.create(op.get_bind(), checkfirst=True)

    task_status = postgresql.ENUM(
        "pending", "in_progress", "done", "failed", "skipped",
        name="task_status",
    )
    task_status.create(op.get_bind(), checkfirst=True)

    event_type = postgresql.ENUM(
        "lead_created", "lead_enriched", "lead_ingested",
        "lead_qualified", "lead_disqualified",
        "email_sent", "reply_received", "meeting_booked",
        "task_created", "task_completed",
        name="event_type",
    )
    event_type.create(op.get_bind(), checkfirst=True)

    # ── companies ─────────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("employee_count", sa.Integer, nullable=True),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("linkedin_url", sa.String(512), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("domain", name="uq_companies_domain"),
    )
    op.create_index("ix_companies_domain", "companies", ["domain"])

    # ── people ────────────────────────────────────────────────────────────────
    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("linkedin_url", sa.String(512), nullable=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_people_email"),
    )
    op.create_index("ix_people_email", "people", ["email"])
    op.create_index("ix_people_company_id", "people", ["company_id"])

    # ── leads ─────────────────────────────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "new", "enriching", "qualified", "disqualified",
                "contacted", "replied", "meeting_booked", "closed_won", "closed_lost",
                name="lead_status",
                create_type=False,
            ),
            nullable=False,
            server_default="new",
        ),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_leads_person_id", "leads", ["person_id"])
    op.create_index("ix_leads_status", "leads", ["status"])

    # ── interactions ──────────────────────────────────────────────────────────
    op.create_table(
        "interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "interaction_type",
            postgresql.ENUM(
                "email_sent", "email_received", "call",
                "linkedin_message", "linkedin_connection", "meeting", "note",
                name="interaction_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            postgresql.ENUM("inbound", "outbound", name="interaction_direction", create_type=False),
            nullable=False,
            server_default="outbound",
        ),
        sa.Column("subject", sa.String(512), nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("channel_message_id", sa.String(512), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interactions_lead_id", "interactions", ["lead_id"])
    op.create_index("ix_interactions_occurred_at", "interactions", ["occurred_at"])

    # ── tasks ─────────────────────────────────────────────────────────────────
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("task_type", sa.String(128), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "in_progress", "done", "failed", "skipped",
                name="task_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
        sa.Column("payload", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_lead_id", "tasks", ["lead_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # ── events ────────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "lead_created", "lead_enriched", "lead_ingested",
                "lead_qualified", "lead_disqualified",
                "email_sent", "reply_received", "meeting_booked",
                "task_created", "task_completed",
                name="event_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.Text, nullable=True),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_lead_id", "events", ["lead_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("tasks")
    op.drop_table("interactions")
    op.drop_table("leads")
    op.drop_table("people")
    op.drop_table("companies")

    # Drop enum types
    for name in [
        "event_type", "task_status", "interaction_direction",
        "interaction_type", "lead_status",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name}")
