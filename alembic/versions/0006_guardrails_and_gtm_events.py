"""Add guardrails and gtm capability event types.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'guardrail_allowed'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'guardrail_blocked'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'prospect_researched'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'outreach_drafted'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'outreach_simulated'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'meeting_booking_simulated'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'crm_updated'")


def downgrade() -> None:
    pass
