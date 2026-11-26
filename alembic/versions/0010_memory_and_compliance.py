"""Persistent sales memory and compliance foundations.

- facts / fact_evidence (evidence ledger + lifecycle)
- research_findings, agent_decisions, work_items
- suppressions, contact_consents, approvals
- interactions: task_id, channel, provider, details
- task_status enum: awaiting_approval, cancelled

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
                     nullable=False, server_default=_TENANT_DEFAULT, index=True)


def _ts(name: str, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.func.now() if default else None)


def upgrade() -> None:
    # Enum values must be committed before use; ALTER TYPE ... ADD VALUE is idempotent with IF NOT EXISTS.
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'awaiting_approval'")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'cancelled'")

    op.create_table(
        "facts",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("field", sa.String(128), nullable=False),
        sa.Column("value", JSONB),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("method", sa.String(128)),
        sa.Column("rationale", sa.Text()),
        sa.Column("source_task_id", UUID, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        _ts("observed_at", False, True),
        sa.Column("decided_by", sa.String(255)), _ts("decided_at"),
        sa.Column("superseded_by", UUID, sa.ForeignKey("facts.id", ondelete="SET NULL")),
        _ts("superseded_at"), _ts("created_at", False, True),
    )
    op.create_index("ix_facts_entity_field", "facts", ["tenant_id", "entity_type", "entity_id", "field", "status"])
    op.create_index("uq_facts_applied", "facts", ["tenant_id", "entity_type", "entity_id", "field"], unique=True,
                    postgresql_where=sa.text("status = 'applied'"))

    op.create_table(
        "fact_evidence",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("fact_id", UUID, sa.ForeignKey("facts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detail", sa.Text()), sa.Column("source_url", sa.String(1024)),
        sa.Column("source_ref", sa.String(255)), _ts("observed_at", False, True),
    )

    op.create_table(
        "research_findings",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE")),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body", JSONB), sa.Column("citations", JSONB),
        sa.Column("confidence", sa.Float()), sa.Column("source", sa.String(128)),
        sa.Column("source_task_id", UUID, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        _ts("created_at", False, True), _ts("stale_after"),
    )
    op.create_index("ix_findings_lead_kind", "research_findings", ["tenant_id", "lead_id", "kind"])

    op.create_table(
        "agent_decisions",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128)), sa.Column("prompt_version", sa.String(64)),
        sa.Column("action", sa.String(128), nullable=False), sa.Column("capability", sa.String(128)),
        sa.Column("channel", sa.String(64)), sa.Column("parameters", JSONB),
        sa.Column("reasoning", sa.Text()), sa.Column("evidence_refs", JSONB),
        sa.Column("expected_outcome", sa.Text()), sa.Column("confidence", sa.Float()),
        sa.Column("uncertainty", sa.Text()), sa.Column("risks", JSONB),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("guardrail_result", JSONB),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("context_snapshot", JSONB),
        _ts("created_at", False, True), _ts("updated_at", False, True),
    )
    op.create_index("ix_agent_decisions_lead", "agent_decisions", ["tenant_id", "lead_id", "created_at"])

    op.create_table(
        "work_items",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", JSONB),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        _ts("due_at", False, True), _ts("leased_until"), sa.Column("leased_by", sa.String(128)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        _ts("finished_at"), sa.Column("outcome", sa.String(64)),
        sa.Column("created_by", sa.String(128)), _ts("created_at", False, True),
    )
    op.create_index("ix_work_items_due", "work_items", ["due_at", "leased_until"],
                    postgresql_where=sa.text("finished_at IS NULL"))
    op.create_index("uq_work_items_open", "work_items", ["tenant_id", "kind", "lead_id"], unique=True,
                    postgresql_where=sa.text("finished_at IS NULL"))

    op.create_table(
        "suppressions",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("source", sa.String(128)), sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(255)), _ts("created_at", False, True),
        _ts("expires_at"), _ts("lifted_at"), sa.Column("lifted_by", sa.String(255)),
    )
    op.create_index("uq_suppressions_active", "suppressions", ["tenant_id", "scope", "value"], unique=True,
                    postgresql_where=sa.text("lifted_at IS NULL"))

    op.create_table(
        "contact_consents",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("person_id", UUID, sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("basis", sa.String(64), nullable=False), sa.Column("channel", sa.String(32)),
        sa.Column("source", sa.String(128), nullable=False), sa.Column("evidence", sa.Text()),
        _ts("recorded_at", False, True), _ts("withdrawn_at"),
    )
    op.create_index("ix_contact_consents_person", "contact_consents", ["tenant_id", "person_id"])

    op.create_table(
        "approvals",
        sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), index=True),
        sa.Column("decision_id", UUID, sa.ForeignKey("agent_decisions.id", ondelete="SET NULL")),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("original_payload", JSONB, nullable=False), sa.Column("final_payload", JSONB),
        sa.Column("payload_hash", sa.String(64)),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reasoning", sa.Text()), sa.Column("evidence", JSONB), sa.Column("reviewer_note", sa.Text()),
        _ts("requested_at", False, True), _ts("expires_at"), _ts("decided_at"),
        sa.Column("decided_by", sa.String(255)),
    )

    op.add_column("interactions", sa.Column("task_id", UUID, sa.ForeignKey("tasks.id", ondelete="SET NULL")))
    op.add_column("interactions", sa.Column("channel", sa.String(32)))
    op.add_column("interactions", sa.Column("provider", sa.String(128)))
    op.add_column("interactions", sa.Column("details", JSONB))


def downgrade() -> None:
    for col in ("details", "provider", "channel", "task_id"):
        op.drop_column("interactions", col)
    for table in ("approvals", "contact_consents", "suppressions", "work_items", "agent_decisions",
                  "research_findings", "fact_evidence", "facts"):
        op.drop_table(table)
    # Postgres cannot drop enum values; move affected rows to a safe status instead.
    op.execute("UPDATE tasks SET status = 'failed' WHERE status::text IN ('awaiting_approval', 'cancelled')")
