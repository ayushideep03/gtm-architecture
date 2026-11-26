"""
Follow-up automation.

When an outreach email is sent, the next step of the lead's campaign sequence is
scheduled (``campaign.sequence[i].delay_days`` after the send). Rows are unique per
(lead, step), so a step can never be scheduled twice.

    scheduled ──(due time reached, campaign active)──► due ──(Oxygen drafts it)──► drafted ──(approved+sent)──► sent
        └──(prospect replied / unsubscribed / campaign stopped / manual)──► cancelled
    out-of-office reply: still scheduled, due time pushed past the prospect's return.

The worker runs ``process_due`` every tick; due follow-ups wake Oxygen for that lead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Campaign, FollowUp

ACTIVE = ("scheduled", "due", "drafted")
DEFAULT_SEQUENCE = [{"step": 1, "delay_days": 0}, {"step": 2, "delay_days": 3}]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def sequence_for(db: AsyncSession, lead) -> list[dict]:
    if getattr(lead, "campaign_id", None):
        c = await db.get(Campaign, lead.campaign_id)
        if c is not None and c.sequence:
            return sorted(c.sequence, key=lambda s: int(s.get("step", 0)))
    return DEFAULT_SEQUENCE


async def on_email_sent(db: AsyncSession, lead, task_id, *, sent_at: Optional[datetime] = None) -> Optional[FollowUp]:
    """Mark the step just sent and schedule the next one (idempotent)."""
    from app.models.base import Interaction
    sent_at = sent_at or _now()
    sent_count = (await db.execute(select(func.count(Interaction.id)).where(
        Interaction.lead_id == lead.id, Interaction.direction == "outbound",
        Interaction.interaction_type == "email_sent"))).scalar() or 0
    await db.execute(update(FollowUp).where(FollowUp.lead_id == lead.id, FollowUp.status.in_(("due", "drafted")))
                     .values(status="sent", send_task_id=task_id, updated_at=_now()))
    seq = await sequence_for(db, lead)
    if sent_count >= len(seq):
        return None
    nxt = seq[sent_count]
    step = int(nxt.get("step", sent_count + 1))
    delay_h = float(nxt.get("delay_days", 3)) * 24
    stmt = pg_insert(FollowUp).values(
        tenant_id=lead.tenant_id, lead_id=lead.id, campaign_id=getattr(lead, "campaign_id", None), step=step,
        delay_hours=delay_h, angle=nxt.get("angle"), due_at=sent_at + timedelta(hours=delay_h), status="scheduled",
        reason=f"Step {step} of {len(seq)}: {delay_h / 24:g} day(s) after step {step - 1}",
    ).on_conflict_do_nothing(index_elements=["tenant_id", "lead_id", "step"])
    await db.execute(stmt)
    return (await db.execute(select(FollowUp).where(FollowUp.lead_id == lead.id, FollowUp.step == step))).scalar_one()


async def cancel_for_lead(db: AsyncSession, lead_id, reason: str) -> int:
    res = await db.execute(update(FollowUp).where(FollowUp.lead_id == lead_id, FollowUp.status.in_(ACTIVE))
                           .values(status="cancelled", reason=reason, updated_at=_now()))
    return res.rowcount or 0


async def postpone_for_lead(db: AsyncSession, lead_id, until: datetime, reason: str) -> int:
    rows = (await db.execute(select(FollowUp).where(FollowUp.lead_id == lead_id, FollowUp.status.in_(ACTIVE)))).scalars().all()
    for f in rows:
        if f.due_at < until:
            f.due_at = until
        f.status, f.reason = "scheduled", reason
    return len(rows)


async def on_reply(db: AsyncSession, lead, category: str, when: datetime, follow_up_after_days=None) -> str:
    """A reply changes the sequence: out-of-office pauses it, anything else ends it."""
    if category == "out_of_office":
        days = float(follow_up_after_days or 7)
        n = await postpone_for_lead(db, lead.id, when + timedelta(days=days),
                                    f"Prospect out of office; postponed until after their return (+{days:g} days)")
        return f"{n} follow-up(s) postponed (out of office)"
    n = await cancel_for_lead(db, lead.id, f"Prospect replied ({category}); automatic follow-ups stopped")
    return f"{n} follow-up(s) cancelled (reply: {category})"


async def on_draft(db: AsyncSession, lead_id, draft_task_id) -> None:
    await db.execute(update(FollowUp).where(FollowUp.lead_id == lead_id, FollowUp.status == "due")
                     .values(status="drafted", draft_task_id=draft_task_id, updated_at=_now()))


async def process_due(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Due follow-ups of active campaigns become 'due' and wake Oxygen for the lead."""
    from app.memory.records import schedule_work
    from app.models.base import Lead
    now = now or _now()
    rows = (await db.execute(
        select(FollowUp, Lead).join(Lead, Lead.id == FollowUp.lead_id)
        .outerjoin(Campaign, Campaign.id == FollowUp.campaign_id)
        .where(FollowUp.status == "scheduled", FollowUp.due_at <= now,
               (Campaign.id.is_(None)) | (Campaign.status == "active"))
        .with_for_update(of=FollowUp, skip_locked=True))).all()
    for f, lead in rows:
        f.status, f.reason = "due", f"Step {f.step} due; Oxygen drafts it for approval"
        await schedule_work(db, tenant_id=f.tenant_id, lead_id=lead.id, kind="evaluate",
                            reason=f"Follow-up step {f.step} is due", priority=6, created_by="follow_ups",
                            payload={"follow_up_id": str(f.id)})
    return len(rows)


def view(f: FollowUp) -> dict:
    return {"id": str(f.id), "lead_id": str(f.lead_id), "campaign_id": str(f.campaign_id) if f.campaign_id else None,
            "step": f.step, "delay_hours": f.delay_hours, "angle": f.angle, "due_at": f.due_at, "status": f.status,
            "reason": f.reason, "draft_task_id": str(f.draft_task_id) if f.draft_task_id else None,
            "send_task_id": str(f.send_task_id) if f.send_task_id else None, "updated_at": f.updated_at}
