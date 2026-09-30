"""Add oxygen events to event_type enum.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'oxygen_decision'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'oxygen_task_created'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'oxygen_waiting'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'oxygen_no_action'")


def downgrade() -> None:
    pass
