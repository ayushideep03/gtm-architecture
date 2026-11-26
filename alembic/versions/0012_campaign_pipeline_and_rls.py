"""Campaign pipeline (campaigns, target accounts, follow-ups, meetings, drafts, users, demo clock)
and PostgreSQL row-level security for tenant isolation.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.text("now()")


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False,
                     server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid"), index=True)


def _ts(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=NOW)


# The policy: with app.tenant_id set (API request / per-lead job) only that tenant's rows are visible
# and writable; unset (worker queue scans, migrations, tests) every tenant is visible.
POLICY = ("(nullif(current_setting('app.tenant_id', true), '') IS NULL "
          "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)")


def upgrade() -> None:
    op.create_table(
        "app_users", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False), sa.Column("api_key_hash", sa.String(64), unique=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), _ts(),
        sa.CheckConstraint("role IN ('admin', 'operator', 'approver')", name="ck_app_users_role"),
    )
    op.create_table(
        "campaigns", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("icp", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("offer", sa.Text()),
        sa.Column("value_points", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sequence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.String(128)), sa.Column("started_at", sa.DateTime(timezone=True)),
        _ts(), _ts("updated_at"),
        sa.CheckConstraint("status IN ('draft', 'active', 'paused', 'stopped')", name="ck_campaigns_status"),
    )
    op.create_table(
        "campaign_accounts", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("campaign_id", UUID, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovery_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("discovery_source", sa.String(128)),
        sa.Column("prospects_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovered_at", sa.DateTime(timezone=True)), sa.Column("added_by", sa.String(128)), _ts(),
    )
    op.create_index("uq_campaign_accounts", "campaign_accounts", ["campaign_id", "company_id"], unique=True)
    op.create_table(
        "follow_ups", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("campaign_id", UUID, sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("step", sa.Integer(), nullable=False), sa.Column("delay_hours", sa.Float(), nullable=False),
        sa.Column("angle", sa.Text()), sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("reason", sa.Text()), sa.Column("draft_task_id", UUID), sa.Column("send_task_id", UUID),
        _ts(), _ts("updated_at"),
    )
    op.create_index("uq_follow_ups_lead_step", "follow_ups", ["tenant_id", "lead_id", "step"], unique=True)
    op.create_table(
        "meetings", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("campaign_id", UUID, sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("uid", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="requested"),
        sa.Column("provider", sa.String(128)),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("title", sa.String(255)),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("proposed_slots", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("start_at", sa.DateTime(timezone=True)), sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("invite", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("task_id", UUID), _ts(), _ts("updated_at"),
    )
    op.create_index("uq_meetings_uid", "meetings", ["tenant_id", "uid"], unique=True)
    op.create_table(
        "drafts", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("campaign_id", UUID, sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(32), nullable=False, server_default="initial"), sa.Column("step", sa.Integer()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("subject", sa.Text()), sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("explanation", sa.Text()), sa.Column("strategy", sa.Text()),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("generated_by", sa.String(128)), sa.Column("source_task_id", UUID),
        sa.Column("send_task_id", UUID), sa.Column("reply_to_interaction_id", UUID),
        sa.Column("edited_by", sa.String(128)), _ts(), _ts("updated_at"),
    )
    op.create_table(
        "demo_clock", sa.Column("id", UUID, primary_key=True), _tenant(),
        sa.Column("offset_hours", sa.Float(), nullable=False, server_default="0"), _ts("updated_at"),
    )
    op.create_index("uq_demo_clock_tenant", "demo_clock", ["tenant_id"], unique=True)

    op.add_column("leads", sa.Column("campaign_id", UUID, sa.ForeignKey("campaigns.id", ondelete="SET NULL")))
    op.create_index("ix_leads_campaign_id", "leads", ["campaign_id"])
    op.add_column("people", sa.Column("verification_status", sa.String(32)))

    # Row-level security on every tenant-owned table (FORCE: applies to the table owner too).
    conn = op.get_bind()
    tables = [r[0] for r in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND column_name = 'tenant_id' ORDER BY table_name"))]
    for table in tables:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON "{table}" USING {POLICY} WITH CHECK {POLICY}')


def downgrade() -> None:
    conn = op.get_bind()
    tables = [r[0] for r in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND column_name = 'tenant_id'"))]
    for table in tables:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_column("people", "verification_status")
    op.drop_index("ix_leads_campaign_id", table_name="leads")
    op.drop_column("leads", "campaign_id")
    for t in ("demo_clock", "drafts", "meetings", "follow_ups", "campaign_accounts", "campaigns", "app_users"):
        op.drop_table(t)
