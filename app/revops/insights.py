"""
Learning from outcomes — as evidence, never as self-modification.

1. ``planner_insights``: for the prospect's segment (industry, company size,
   seniority) what has worked so far — outcome rates by channel and angle with
   sample sizes (``weak`` when small) and a few anonymised converted journeys
   (the sequence of actions that preceded a meeting or positive reply). This is
   added to the prospect memory the planner reads.
2. ``OutcomeQualityStats``: per provider, the positive-outcome rate of the
   touches it drafted/sent, used by the ProviderSelector as one weighted signal.

Neither changes code, prompts, guardrails, permissions or configuration.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import get_gtm_config
from app.models.base import Lead, Task
from app.models.memory import OutcomeAttribution
from app.revops.dashboard import group_stats, outcome_stats
from app.revops.outcomes import POSITIVE_OUTCOMES, segment_for


async def planner_insights(db: AsyncSession, lead: Lead) -> dict[str, Any]:
    cfg = get_gtm_config().revops
    segment = await segment_for(db, lead, cfg)
    rows = (await db.execute(select(OutcomeAttribution).where(OutcomeAttribution.tenant_id == lead.tenant_id,
                                                              OutcomeAttribution.lead_id != lead.id))).scalars().all()
    if not rows:
        return {"segment": segment, "note": "No outreach outcomes recorded yet; no learned evidence available."}

    def same(key):
        return [r for r in rows if r.segment.get(key) == segment[key] and segment[key] != "unknown"]

    peers = same("industry") or same("size_band") or []
    basis = "same industry" if same("industry") else ("same company size" if peers else "all prospects")
    sample = peers or rows
    min_n = cfg.min_sample_for_insight
    journeys = []
    for r in sorted((r for r in sample if r.outcome in POSITIVE_OUTCOMES), key=lambda r: r.sent_at, reverse=True):
        if len(journeys) >= cfg.insight_similar_journeys:
            break
        steps = (await db.execute(select(Task.task_type).where(Task.lead_id == r.lead_id, Task.status == "completed",
                                                              Task.completed_at <= r.sent_at)
                                  .order_by(Task.completed_at))).scalars().all()
        journeys.append({"segment": r.segment, "steps_before_touch": list(dict.fromkeys(steps)),
                         "channel": r.channel, "angle": r.angle, "personalisation_points": r.personalisation_points,
                         "outcome": r.outcome})
    return {
        "segment": segment,
        "basis": basis,
        "baseline": outcome_stats(rows, min_n),
        "peer_outcomes": outcome_stats(sample, min_n),
        "by_channel": group_stats(sample, lambda r: r.channel or "unknown", min_n),
        "by_angle": dict(list(group_stats(sample, lambda r: r.angle or "(none)", min_n).items())[:6]),
        "research_effect": group_stats(sample, lambda r: "researched first" if r.research_before_touch
                                       else "no research", min_n),
        "converted_journeys": journeys,
        "note": ("Observed outcomes from past outreach (anonymised). Treat 'weak' groups (small n) as anecdotes, "
                 "not rules; guardrails and approval always apply."),
    }


class OutcomeQualityStats:
    """Per-provider positive-outcome counts for capabilities that produce touches."""

    _FIELDS = {"draft_outreach": "draft_provider", "send_email": "provider"}

    def __init__(self, ttl_s: float = 60.0) -> None:
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, dict]] = {}

    async def get(self, session: Optional[AsyncSession], capability: str) -> dict[str, tuple[int, int]]:
        field = self._FIELDS.get(capability)
        if field is None or session is None:
            return {}
        cached = self._cache.get(capability)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            async with session.begin_nested():
                rows = (await session.execute(select(getattr(OutcomeAttribution, field), OutcomeAttribution.outcome)
                                              .where(OutcomeAttribution.final.is_(True)))).all()
        except Exception:  # noqa: BLE001 - advisory signal only
            return {}
        stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for provider, outcome in rows:
            if provider:
                stats[provider][1] += 1
                stats[provider][0] += int(outcome in POSITIVE_OUTCOMES)
        result = {k: (v[0], v[1]) for k, v in stats.items()}
        self._cache[capability] = (time.monotonic() + self._ttl, result)
        return result
