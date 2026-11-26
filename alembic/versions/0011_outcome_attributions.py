"""Outcome attribution for RevOps and learning.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "outcome_attributions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False,
                  server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid"), index=True),
        sa.Column("touch_id", UUID, sa.ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("decision_id", UUID),
        sa.Column("channel", sa.String(32)), sa.Column("touch_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(128)), sa.Column("draft_provider", sa.String(128)),
        sa.Column("engine", sa.String(32)), sa.Column("angle", sa.Text()),
        sa.Column("personalisation_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("research_before_touch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("segment", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="none"),
        sa.Column("outcome_at", sa.DateTime(timezone=True)),
        sa.Column("outcome_detail", postgresql.JSONB()),
        sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_outcome_attr_lead", "outcome_attributions", ["tenant_id", "lead_id"])


def downgrade() -> None:
    op.drop_table("outcome_attributions")
