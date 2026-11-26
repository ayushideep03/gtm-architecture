"""Identity constraints, source-record mapping, task reliability & idempotency.

Upgrade steps:
  1. Merge existing duplicates (oldest record wins, references repointed):
       companies by (tenant_id, domain), people by (tenant_id, email),
       open leads by (tenant_id, person_id).
  2. Replace the global unique constraints from 0001 (uq_companies_domain,
     uq_people_email) with tenant-scoped ones, so two organisations can hold
     the same prospect. Integrity constraints (partial unique indexes):
       uq_companies_tenant_domain, uq_people_tenant_email,
       uq_leads_open_person (one open lead per person), uq_tasks_tenant_idempotency.
  3. source_records table (provider external id -> our entities).
  4. leads.is_open; tasks retry / lease / review / idempotency columns.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Terminal lead statuses at the time of this migration (used only to backfill
# is_open; at runtime the list comes from config identity.lead.terminal_statuses).
_TERMINAL_AT_MIGRATION = ("closed_won", "closed_lost", "disqualified")


def _dups(table: str, partition: str, where: str) -> str:
    """Rows to merge: every row except the oldest in each duplicate group."""
    return (f"SELECT id, keep_id FROM (SELECT id, first_value(id) OVER "
            f"(PARTITION BY {partition} ORDER BY created_at, id) AS keep_id FROM {table} WHERE {where}) r "
            f"WHERE id <> keep_id")


def _merge_duplicates() -> None:
    # Self-contained statements (no temp tables), so this works whether alembic
    # runs one transaction per migration or one for the whole upgrade.
    company_dups = _dups("companies", "tenant_id, domain", "domain IS NOT NULL")
    op.execute(f"UPDATE people p SET company_id = d.keep_id FROM ({company_dups}) d WHERE p.company_id = d.id")
    op.execute(f"DELETE FROM companies c USING ({company_dups}) d WHERE c.id = d.id")

    person_dups = _dups("people", "tenant_id, email", "email IS NOT NULL")
    op.execute(f"UPDATE leads l SET person_id = d.keep_id FROM ({person_dups}) d WHERE l.person_id = d.id")
    op.execute(f"DELETE FROM people p USING ({person_dups}) d WHERE p.id = d.id")

    terminal = ", ".join(f"'{s}'" for s in _TERMINAL_AT_MIGRATION)
    op.execute(f"UPDATE leads SET is_open = (status::text NOT IN ({terminal}))")
    lead_dups = _dups("leads", "tenant_id, person_id", "is_open AND person_id IS NOT NULL")
    for table in ("interactions", "tasks", "events"):
        op.execute(f"UPDATE {table} t SET lead_id = d.keep_id FROM ({lead_dups}) d WHERE t.lead_id = d.id")
    op.execute(f"DELETE FROM leads l USING ({lead_dups}) d WHERE l.id = d.id")


def upgrade() -> None:
    op.add_column("leads", sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.true()))

    _merge_duplicates()

    op.drop_constraint("uq_companies_domain", "companies", type_="unique")
    op.drop_constraint("uq_people_email", "people", type_="unique")
    op.create_index("uq_companies_tenant_domain", "companies", ["tenant_id", "domain"], unique=True,
                    postgresql_where=sa.text("domain IS NOT NULL"))
    op.create_index("uq_people_tenant_email", "people", ["tenant_id", "email"], unique=True,
                    postgresql_where=sa.text("email IS NOT NULL"))
    op.create_index("ix_people_tenant_linkedin", "people", ["tenant_id", "linkedin_url"],
                    postgresql_where=sa.text("linkedin_url IS NOT NULL"))
    op.create_index("ix_companies_tenant_linkedin", "companies", ["tenant_id", "linkedin_url"],
                    postgresql_where=sa.text("linkedin_url IS NOT NULL"))
    op.create_index("uq_leads_open_person", "leads", ["tenant_id", "person_id"], unique=True,
                    postgresql_where=sa.text("is_open AND person_id IS NOT NULL"))

    op.create_table(
        "source_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
                  nullable=False, server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid")),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source", "external_id", name="uq_source_records_key"),
    )
    op.create_index("ix_source_records_tenant_id", "source_records", ["tenant_id"])
    op.create_index("ix_source_records_person_id", "source_records", ["person_id"])

    op.add_column("tasks", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("max_attempts", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("last_error_code", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.create_index("ix_tasks_next_attempt_at", "tasks", ["next_attempt_at"])
    op.create_index("ix_tasks_lease_expires_at", "tasks", ["lease_expires_at"])
    op.create_index("uq_tasks_tenant_idempotency", "tasks", ["tenant_id", "idempotency_key"], unique=True,
                    postgresql_where=sa.text("idempotency_key IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_tasks_tenant_idempotency", table_name="tasks")
    op.drop_index("ix_tasks_lease_expires_at", table_name="tasks")
    op.drop_index("ix_tasks_next_attempt_at", table_name="tasks")
    for col in ("idempotency_key", "needs_review", "last_error_code", "lease_expires_at",
                "started_at", "next_attempt_at", "max_attempts", "attempt_count"):
        op.drop_column("tasks", col)
    op.drop_table("source_records")
    op.drop_index("uq_leads_open_person", table_name="leads")
    op.drop_index("ix_companies_tenant_linkedin", table_name="companies")
    op.drop_index("ix_people_tenant_linkedin", table_name="people")
    op.drop_index("uq_people_tenant_email", table_name="people")
    op.drop_index("uq_companies_tenant_domain", table_name="companies")
    # Global constraints come back; fails if two tenants hold the same domain/email.
    op.create_unique_constraint("uq_people_email", "people", ["email"])
    op.create_unique_constraint("uq_companies_domain", "companies", ["domain"])
    op.drop_column("leads", "is_open")
    # Merged duplicates are not restored (their history was moved to the surviving records).
