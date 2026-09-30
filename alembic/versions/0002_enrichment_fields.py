"""Add enrichment fields to companies and people; extend event_type enum.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01

Changes:
    companies:
        + description        TEXT
        + technologies       TEXT (JSON-encoded list)
        + funding_stage      VARCHAR(64)
        + revenue_range      VARCHAR(64)
        + enriched_at        TIMESTAMPTZ
        + enrichment_source  VARCHAR(128)

    people:
        + seniority          VARCHAR(64)
        + department         VARCHAR(128)
        + location           VARCHAR(255)
        + enriched_at        TIMESTAMPTZ
        + enrichment_source  VARCHAR(128)

    event_type enum:
        + company_enriched
        + person_enriched
        + enrichment_failed
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Extend the event_type enum with new enrichment event types -----------
    # PostgreSQL requires ALTER TYPE ... ADD VALUE outside a transaction block
    # when using transactional DDL.  Alembic does not automatically handle
    # this, so we execute it with COMMIT beforehand.
    op.execute("COMMIT")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'company_enriched'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'person_enriched'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'enrichment_failed'")
    # Resume the transaction (alembic will open a new one on next DDL op)

    # -- companies: enrichment columns ----------------------------------------
    op.add_column("companies", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("technologies", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("funding_stage", sa.String(64), nullable=True))
    op.add_column("companies", sa.Column("revenue_range", sa.String(64), nullable=True))
    op.add_column("companies", sa.Column(
        "enriched_at", sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column("companies", sa.Column(
        "enrichment_source", sa.String(128), nullable=True
    ))

    # -- people: enrichment columns -------------------------------------------
    op.add_column("people", sa.Column("seniority", sa.String(64), nullable=True))
    op.add_column("people", sa.Column("department", sa.String(128), nullable=True))
    op.add_column("people", sa.Column("location", sa.String(255), nullable=True))
    op.add_column("people", sa.Column(
        "enriched_at", sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column("people", sa.Column(
        "enrichment_source", sa.String(128), nullable=True
    ))


def downgrade() -> None:
    # -- people ---------------------------------------------------------------
    for col in ("enrichment_source", "enriched_at", "location", "department", "seniority"):
        op.drop_column("people", col)

    # -- companies ------------------------------------------------------------
    for col in ("enrichment_source", "enriched_at", "revenue_range",
                "funding_stage", "technologies", "description"):
        op.drop_column("companies", col)

    # NOTE: PostgreSQL does not support removing enum values once added.
    # The three enrichment event types will remain in the enum on downgrade.
    # This is acceptable because the downgrade only removes the columns.
