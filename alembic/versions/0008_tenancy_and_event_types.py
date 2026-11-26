"""Multi-tenancy foundation and extensible event types.

- organizations table, seeded with the default organisation
- tenant_id (NOT NULL, FK organizations.id, indexed) on companies, people,
  leads, interactions, tasks, events; existing rows join the default org
- events.event_type: Postgres ENUM -> VARCHAR(64) (validated in application
  code via app.events.types), so new event types need no migration

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match app.core.tenancy.DEFAULT_TENANT_ID (kept literal: migrations are frozen history).
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
TENANT_TABLES = ("companies", "people", "leads", "interactions", "tasks", "events")

# Enum values as of 0007, used only to restore the type on downgrade.
_EVENT_TYPES_0007 = (
    "lead_created", "lead_enriched", "lead_ingested", "lead_qualified", "lead_disqualified",
    "email_sent", "reply_received", "meeting_booked", "task_created", "task_started",
    "task_completed", "task_failed", "company_enriched", "person_enriched",
    "enrichment_completed", "enrichment_failed", "oxygen_decision", "oxygen_task_created",
    "oxygen_waiting", "oxygen_no_action", "guardrail_allowed", "guardrail_blocked",
    "prospect_researched", "outreach_drafted", "outreach_simulated",
    "meeting_booking_simulated", "crm_updated",
)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        f"INSERT INTO organizations (id, name, slug) "
        f"VALUES ('{DEFAULT_TENANT_ID}', 'Default organisation', 'default')"
    )

    for table in TENANT_TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text(f"'{DEFAULT_TENANT_ID}'::uuid"),
            ),
        )
        op.create_foreign_key(
            f"fk_{table}_tenant_id", table, "organizations", ["tenant_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    op.execute("ALTER TABLE events ALTER COLUMN event_type TYPE VARCHAR(64) USING event_type::text")
    op.execute("DROP TYPE IF EXISTS event_type")


def downgrade() -> None:
    values = ", ".join(f"'{v}'" for v in _EVENT_TYPES_0007)
    op.execute(f"CREATE TYPE event_type AS ENUM ({values})")
    # Rows with event types introduced after 0007 cannot be represented; remove them.
    op.execute(f"DELETE FROM events WHERE event_type NOT IN ({values})")
    op.execute("ALTER TABLE events ALTER COLUMN event_type TYPE event_type USING event_type::event_type")

    for table in TENANT_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
    op.drop_table("organizations")
