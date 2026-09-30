"""Add execution events and completed task status.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'completed'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'task_started'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'task_failed'")


def downgrade() -> None:
    pass
