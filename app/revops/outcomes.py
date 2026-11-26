"""
Outcome attribution: link every outbound touch to what happened next.

Rules (deterministic, config ``revops``):
  - an outcome event is attributed to the MOST RECENT outbound touch to that
    lead that precedes it, if within ``attribution_window_days``;
  - a touch keeps its strongest outcome (meeting > positive reply > objection >
    reply > not interested > unsubscribed > bounced > none);
  - a touch becomes ``final`` once its window has closed.

Each row also records what the touch was (channel, angle, personalisation,
whether research preceded it, which engine decided it, which providers drafted
and sent it, the prospect's segment) so RevOps can compare what works.
Re-running is idempotent (one row per touch, updated in place).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import RevOpsConfig, get_gtm_config
from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.models.memory import AgentDecision, OutcomeAttribution, ResearchFinding

OUTBOUND_TOUCHES = ("email_sent", "linkedin_message", "linkedin_connection", "call")
OUTCOME_RANK = {"none": 0, "bounced": 1, "unsubscribed": 2, "not_interested": 3, "reply": 4, "objection": 5,
                "positive_reply": 6, "meeting": 7}
POSITIVE_OUTCOMES = {"positive_reply", "meeting"}
REPLY_OUTCOMES = {"reply", "objection", "positive_reply", "meeting", "not_interested"}
NEGATIVE_OUTCOMES = {"not_interested", "unsubscribed", "bounced"}
OUTCOME_EVENTS = ("meeting_booked", "reply_classified", "reply_received", "unsubscribed", "complaint_received",
                  "email_bounced")
RESEARCH_KINDS = ("brief", "intent_signal", "news", "research")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload(ev: Event) -> dict:
    try:
        return json.loads(ev.payload or "{}")
    except ValueError:
        return {}


def outcome_for_event(ev: Event, cfg: RevOpsConfig) -> Optional[str]:
    p = _payload(ev)
    if ev.event_type == "meeting_booked":
        return "meeting"
    if ev.event_type == "reply_classified":
        cat = p.get("category")
        if cat in cfg.positive_reply_categories:
            return "positive_reply"
        if cat == "unsubscribe":
            return "unsubscribed"
        if cat in cfg.negative_reply_categories:
            return "not_interested"
        if cat in ("objection", "not_now"):
            return "objection"
        if cat == "out_of_office":
            return None
        return "reply"
    if ev.event_type == "reply_received":
        return "reply"
    if ev.event_type in ("unsubscribed", "complaint_received"):
        return "unsubscribed"
    if ev.event_type == "email_bounced" and p.get("bounce_type") == "hard":
        return "bounced"
    return None


def employee_band(n: Optional[int], bands: list[int]) -> str:
    if n is None:
        return "unknown"
    low = 1
    for upper in bands:
        if n <= upper:
            return f"{low}-{upper}"
        low = upper + 1
    return f"{low}+"


async def segment_for(db: AsyncSession, lead: Lead, cfg: RevOpsConfig) -> dict[str, str]:
    person = await db.get(Person, lead.person_id) if lead.person_id else None
    company = await db.get(Company, person.company_id) if person and person.company_id else None
    return {
        "industry": (company.industry if company and company.industry else "unknown"),
        "size_band": employee_band(company.employee_count if company else None, cfg.employee_bands),
        "seniority": (person.seniority if person and person.seniority else "unknown"),
    }


async def _touch_context(db: AsyncSession, touch: Interaction) -> dict[str, Any]:
    """What the touch was: its task, the draft behind it, the deciding engine, the providers."""
    ctx: dict[str, Any] = {"task_id": touch.task_id, "decision_id": None, "engine": None, "angle": None,
                           "personalisation_points": 0, "draft_provider": None}
    if touch.task_id is None:
        return ctx
    decision = (await db.execute(select(AgentDecision).where(AgentDecision.task_id == touch.task_id))).scalar_one_or_none()
    if decision is not None:
        ctx["decision_id"], ctx["engine"] = decision.id, decision.engine
    task = await db.get(Task, touch.task_id)
    payload = json.loads(task.payload or "{}") if task else {}
    draft_id = payload.get("draft_task_id")
    if draft_id:
        try:
            draft = await db.get(Task, uuid.UUID(str(draft_id)))
        except ValueError:
            draft = None
        if draft is not None:
            dp = json.loads(draft.payload or "{}")
            dr = json.loads(draft.result or "{}")
            ctx["angle"] = dp.get("angle")
            ctx["personalisation_points"] = len(dr.get("personalisation") or [])
            done = (await db.execute(select(Event).where(Event.event_type == "task_completed",
                                                         Event.payload.contains(str(draft.id))).limit(1))).scalar_one_or_none()
            ctx["draft_provider"] = _payload(done).get("provider") if done else None
    return ctx


ATTRIBUTION_LOCK_KEY = 0x6F787967656E01  # arbitrary, stable advisory-lock id ("oxygen" + 1)


async def attribute_outcomes(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Create/update one attribution row per outbound touch. Returns rows written."""
    cfg = get_gtm_config().revops
    now = now or _now()
    window = timedelta(days=cfg.attribution_window_days)
    # Several workers tick concurrently; only one attributes at a time (the others skip this
    # step for the tick). Without this, two workers insert the same touch row and both fail.
    from sqlalchemy import text as _sql
    if not (await db.execute(_sql("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ATTRIBUTION_LOCK_KEY})).scalar():
        return 0

    existing = {a.touch_id: a for a in (await db.execute(select(OutcomeAttribution))).scalars().all()}
    touches = (await db.execute(select(Interaction).where(Interaction.direction == "outbound",
                                                          Interaction.interaction_type.in_(OUTBOUND_TOUCHES))
                                .order_by(Interaction.occurred_at))).scalars().all()
    by_lead: dict[uuid.UUID, list[Interaction]] = defaultdict(list)
    for t in touches:
        by_lead[t.lead_id].append(t)

    written = 0
    for lead_id, lead_touches in by_lead.items():
        if all(existing.get(t.id) is not None and existing[t.id].final for t in lead_touches):
            continue
        lead = await db.get(Lead, lead_id)
        segment = await segment_for(db, lead, cfg)
        events = (await db.execute(select(Event).where(Event.lead_id == lead_id, Event.event_type.in_(OUTCOME_EVENTS))
                                   .order_by(Event.occurred_at))).scalars().all()
        research_times = (await db.execute(select(ResearchFinding.created_at).where(
            ResearchFinding.lead_id == lead_id, ResearchFinding.kind.in_(RESEARCH_KINDS)))).scalars().all()

        best: dict[uuid.UUID, tuple[str, datetime, dict]] = {}
        for ev in events:
            outcome = outcome_for_event(ev, cfg)
            if outcome is None:
                continue
            prior = [t for t in lead_touches if t.occurred_at <= ev.occurred_at and ev.occurred_at - t.occurred_at <= window]
            if not prior:
                continue
            touch = prior[-1]
            current = best.get(touch.id)
            if current is None or OUTCOME_RANK[outcome] > OUTCOME_RANK[current[0]]:
                best[touch.id] = (outcome, ev.occurred_at, {"event_id": str(ev.id), "event_type": ev.event_type,
                                                              **{k: v for k, v in _payload(ev).items()
                                                                 if k in ("category", "summary", "objection", "bounce_type")}})

        for t in lead_touches:
            row = existing.get(t.id)
            if row is not None and row.final:
                continue
            outcome, outcome_at, detail = best.get(t.id, ("none", None, None))
            if row is None:
                ctx = await _touch_context(db, t)
                row = OutcomeAttribution(
                    tenant_id=t.tenant_id, touch_id=t.id, lead_id=lead_id, task_id=ctx["task_id"],
                    decision_id=ctx["decision_id"], engine=ctx["engine"], angle=ctx["angle"],
                    personalisation_points=ctx["personalisation_points"], draft_provider=ctx["draft_provider"],
                    channel=t.channel or ("email" if t.interaction_type == "email_sent" else t.interaction_type),
                    touch_type=t.interaction_type, provider=t.provider, sent_at=t.occurred_at,
                    research_before_touch=any(rt <= t.occurred_at for rt in research_times), segment=segment)
                db.add(row)
            row.outcome, row.outcome_at, row.outcome_detail = outcome, outcome_at, detail
            row.final = now - t.occurred_at > window
            written += 1
    await db.flush()
    return written
