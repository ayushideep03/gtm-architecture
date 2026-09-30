"""Create event_processing_states table for durable RevOps processing.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_processing_states",
        sa.Column("consumer_name", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("last_processed_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_processed_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("event_processing_states")
