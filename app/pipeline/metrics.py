"""
Management metrics (stage 12). Every number is a query over this organisation's records
(row-level security scopes them); nothing is hard-coded or cached.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Date, String, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Interaction, Lead
from app.models.memory import Approval, ResearchFinding, Suppression
from app.models.pipeline import Campaign, CampaignAccount, Draft, FollowUp, Meeting

SIGNAL_KINDS = ("news", "hiring", "technology", "expansion", "buying_signal", "digital_transformation", "challenge",
                "intent_signal")
POSITIVE = ("positive_interest", "meeting_request")
NEGATIVE = ("not_interested", "unsubscribe")


def _rate(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


async def _count(db: AsyncSession, stmt) -> int:
    return (await db.execute(stmt)).scalar() or 0


async def summary(db: AsyncSession, campaign_id: Optional[uuid.UUID] = None) -> dict:
    lead_filter = [Lead.campaign_id == campaign_id] if campaign_id else []

    def leads_q(*extra):
        return select(func.count(Lead.id)).where(*lead_filter, *extra)

    lead_ids = select(Lead.id).where(*lead_filter)
    acct_q = select(func.count(CampaignAccount.id)).where(CampaignAccount.selected.is_(True))
    if campaign_id:
        acct_q = acct_q.where(CampaignAccount.campaign_id == campaign_id)

    sent = await _count(db, select(func.count(Interaction.id)).where(
        Interaction.lead_id.in_(lead_ids), Interaction.direction == "outbound", Interaction.interaction_type == "email_sent"))
    replies = await _count(db, select(func.count(Interaction.id)).where(
        Interaction.lead_id.in_(lead_ids), Interaction.direction == "inbound",
        Interaction.interaction_type == "email_received"))
    category = func.split_part(ResearchFinding.summary, literal_column("':'"), literal_column("1"))
    cats = dict((await db.execute(
        select(category, func.count(func.distinct(ResearchFinding.lead_id)))
        .where(ResearchFinding.kind == "reply_analysis", ResearchFinding.lead_id.in_(lead_ids))
        .group_by(category))).all())
    positive = sum(v for k, v in cats.items() if k in POSITIVE)
    negative = sum(v for k, v in cats.items() if k in NEGATIVE)
    leads = await _count(db, leads_q())
    qualified = await _count(db, leads_q(Lead.status.in_(("qualified", "contacted", "replied", "meeting_booked",
                                                           "closed_won"))))
    disqualified = await _count(db, leads_q(Lead.status == "disqualified"))
    meetings_booked = await _count(db, select(func.count(Meeting.id)).where(Meeting.lead_id.in_(lead_ids),
                                                                            Meeting.status == "booked"))
    meetings_proposed = await _count(db, select(func.count(Meeting.id)).where(
        Meeting.lead_id.in_(lead_ids), Meeting.status.in_(("pending_approval", "invited", "booked", "declined",
                                                           "reschedule_requested"))))
    opt_outs = await _count(db, select(func.count(func.distinct(Suppression.value))).where(
        Suppression.scope == "person", Suppression.reason == "unsubscribe",
        Suppression.value.in_(select(cast(Lead.person_id, String)).where(*lead_filter))))
    kpis = {
        "target_accounts": await _count(db, acct_q),
        "leads_discovered": leads,
        "qualified_leads": qualified,
        "disqualified_leads": disqualified,
        "research_signals": await _count(db, select(func.count(ResearchFinding.id)).where(
            ResearchFinding.kind.in_(SIGNAL_KINDS), ResearchFinding.lead_id.in_(lead_ids))),
        "drafts_generated": await _count(db, select(func.count(Draft.id)).where(Draft.lead_id.in_(lead_ids))),
        "pending_approvals": await _count(db, select(func.count(Approval.id)).where(
            Approval.status == "pending", Approval.lead_id.in_(lead_ids))),
        "emails_sent": sent,
        "follow_ups_scheduled": await _count(db, select(func.count(FollowUp.id)).where(
            FollowUp.lead_id.in_(lead_ids), FollowUp.status.in_(("scheduled", "due", "drafted")))),
        "replies_received": replies,
        "positive_replies": positive,
        "negative_replies": negative,
        "meetings_proposed": meetings_proposed,
        "meetings_booked": meetings_booked,
        "opt_outs": opt_outs,
    }
    rates = {
        "qualification_rate": _rate(qualified, qualified + disqualified),
        "reply_rate": _rate(replies, sent),
        "positive_reply_rate": _rate(positive, replies),
        "meeting_rate": _rate(meetings_booked, sent),
        "lead_to_meeting": _rate(meetings_booked, leads),
    }
    funnel = [
        {"stage": "Target accounts", "count": kpis["target_accounts"]},
        {"stage": "Leads discovered", "count": leads},
        {"stage": "Qualified", "count": qualified},
        {"stage": "Contacted", "count": await _count(db, leads_q(Lead.status.in_(("contacted", "replied",
                                                                                   "meeting_booked", "closed_won"))))},
        {"stage": "Replied", "count": await _count(db, select(func.count(func.distinct(Interaction.lead_id))).where(
            Interaction.lead_id.in_(lead_ids), Interaction.interaction_type == "email_received"))},
        {"stage": "Positive", "count": positive},
        {"stage": "Meetings booked", "count": meetings_booked},
    ]
    status_counts = dict((await db.execute(select(Lead.status, func.count(Lead.id)).where(*lead_filter)
                                           .group_by(Lead.status))).all())
    return {"kpis": kpis, "rates": rates, "funnel": funnel, "lead_status": status_counts,
            "reply_categories": cats, "timeseries": await timeseries(db, lead_ids)}


async def timeseries(db: AsyncSession, lead_ids, days: int = 14) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = cast(Interaction.occurred_at, Date)
    rows = (await db.execute(
        select(day, Interaction.interaction_type, Interaction.direction, func.count(Interaction.id))
        .where(Interaction.lead_id.in_(lead_ids), Interaction.occurred_at >= since,
               Interaction.interaction_type.in_(("email_sent", "email_received")))
        .group_by(day, Interaction.interaction_type, Interaction.direction))).all()
    by: dict[str, dict] = {}
    for d, typ, direction, n in rows:
        e = by.setdefault(d.isoformat(), {"date": d.isoformat(), "sent": 0, "replies": 0})
        e["sent" if typ == "email_sent" else "replies"] += n
    out = []
    for i in range(days, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        out.append(by.get(d, {"date": d, "sent": 0, "replies": 0}))
    return out


async def campaign_performance(db: AsyncSession) -> list[dict]:
    out = []
    for c in (await db.execute(select(Campaign).order_by(Campaign.created_at))).scalars().all():
        s = await summary(db, c.id)
        out.append({"id": str(c.id), "name": c.name, "status": c.status, **{k: s["kpis"][k] for k in (
            "target_accounts", "leads_discovered", "qualified_leads", "emails_sent", "replies_received",
            "positive_replies", "meetings_booked", "opt_outs")}, **s["rates"]})
    return out
