"""Add enrichment_completed to event_type enum.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'enrichment_completed'")


def downgrade() -> None:
    pass
