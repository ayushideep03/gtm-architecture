"""
Demo clock: "advance time" for one organisation so follow-up delays can be shown live.

Advancing by N hours moves this organisation's time-based records N hours into the past
(sent emails, tasks, decisions, scheduled work, follow-ups, approvals, research), exactly as
if N hours had passed. Nothing else is touched and the event log keeps real timestamps.
The total offset is stored and displayed. Only available in the demo profile.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import DemoClock

# (table, timestamp columns) shifted by an advance; every table is tenant-scoped.
SHIFT = [
    ("interactions", ["occurred_at"]),
    ("tasks", ["created_at", "started_at", "completed_at", "next_attempt_at"]),
    ("agent_decisions", ["created_at"]),
    ("work_items", ["due_at", "created_at"]),
    ("follow_ups", ["due_at", "created_at"]),
    ("approvals", ["requested_at", "decided_at"]),   # not expires_at: waiting approvals stay valid
    ("research_findings", ["created_at", "stale_after"]),
    ("outcome_attributions", ["sent_at", "outcome_at"]),
]


async def offset_hours(db: AsyncSession, tenant_id) -> float:
    row = (await db.execute(select(DemoClock).where(DemoClock.tenant_id == tenant_id))).scalar_one_or_none()
    return row.offset_hours if row else 0.0


async def advance(db: AsyncSession, tenant_id, hours: float) -> dict:
    if not 0 < hours <= 24 * 30:
        raise ValueError("advance between 1 hour and 30 days at a time")
    shifted = {}
    for table, cols in SHIFT:
        sets = ", ".join(f"{c} = {c} - make_interval(secs => :s)" for c in cols)
        res = await db.execute(text(f"UPDATE {table} SET {sets} WHERE tenant_id = :t"),
                               {"s": hours * 3600, "t": tenant_id})
        shifted[table] = res.rowcount or 0
    row = (await db.execute(select(DemoClock).where(DemoClock.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        row = DemoClock(tenant_id=tenant_id, offset_hours=0.0)
        db.add(row)
    row.offset_hours += hours
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return {"advanced_hours": hours, "total_offset_hours": row.offset_hours, "rows_shifted": shifted}
