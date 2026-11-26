"""
RevOps dashboard: pipeline, conversion, outreach / channel / segment
effectiveness, qualification accuracy, research effectiveness, provider
reliability, cost per action, approvals, guardrails, engine usage, and
evidence-backed recommendations.

Every rate carries its sample size ``n`` and a ``weak`` flag when n is below
``revops.min_sample_for_insight``. Recommendations are proposals for a human;
nothing here changes configuration, prompts, guardrails or permissions.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import get_gtm_config
from app.models.base import Company, Event, Lead, Person
from app.models.memory import AgentDecision, Approval, OutcomeAttribution
from app.revops.outcomes import NEGATIVE_OUTCOMES, POSITIVE_OUTCOMES, REPLY_OUTCOMES

_LATER_STAGES = ("qualified", "contacted", "replied", "meeting_booked", "closed_won", "closed_lost")


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def outcome_stats(rows: Iterable[OutcomeAttribution], min_n: int) -> dict[str, Any]:
    rows = list(rows)
    n = len(rows)
    replied = sum(r.outcome in REPLY_OUTCOMES for r in rows)
    positive = sum(r.outcome in POSITIVE_OUTCOMES for r in rows)
    meetings = sum(r.outcome == "meeting" for r in rows)
    negative = sum(r.outcome in NEGATIVE_OUTCOMES for r in rows)
    return {"n": n, "reply_rate": _rate(replied, n), "positive_rate": _rate(positive, n),
            "meeting_rate": _rate(meetings, n), "negative_rate": _rate(negative, n), "weak": n < min_n}


def group_stats(rows: list[OutcomeAttribution], key: Callable[[OutcomeAttribution], Any], min_n: int) -> dict:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[str(key(r))].append(r)
    return {k: outcome_stats(v, min_n) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def _payload(ev: Event) -> dict:
    try:
        return json.loads(ev.payload or "{}")
    except ValueError:
        return {}


async def revops_dashboard(db: AsyncSession, days: int = 90) -> dict[str, Any]:
    cfg = get_gtm_config().revops
    since = datetime.now(timezone.utc) - timedelta(days=days)
    min_n = cfg.min_sample_for_insight

    # ── Pipeline ─────────────────────────────────────────────────────────────
    statuses = dict((await db.execute(select(Lead.status, func.count()).group_by(Lead.status))).all())
    total_leads = sum(statuses.values())
    enriched = (await db.execute(select(func.count(Lead.id)).join(Person, Person.id == Lead.person_id)
                                 .join(Company, Company.id == Person.company_id)
                                 .where(Company.enriched_at.isnot(None)))).scalar() or 0
    attributions = (await db.execute(select(OutcomeAttribution).where(OutcomeAttribution.sent_at >= since))).scalars().all()
    contacted_leads = {a.lead_id for a in attributions}
    replied_leads = {a.lead_id for a in attributions if a.outcome in REPLY_OUTCOMES}
    positive_leads = {a.lead_id for a in attributions if a.outcome in POSITIVE_OUTCOMES}
    meeting_leads = {a.lead_id for a in attributions if a.outcome == "meeting"} | set(
        (await db.execute(select(Lead.id).where(Lead.status == "meeting_booked"))).scalars().all())
    qualified = sum(statuses.get(s, 0) for s in _LATER_STAGES)
    funnel = [
        {"stage": "prospects", "count": total_leads},
        {"stage": "enriched", "count": enriched},
        {"stage": "qualified", "count": qualified},
        {"stage": "contacted", "count": len(contacted_leads)},
        {"stage": "replied", "count": len(replied_leads)},
        {"stage": "positive", "count": len(positive_leads)},
        {"stage": "meetings", "count": len(meeting_leads)},
    ]
    for prev, cur in zip(funnel, funnel[1:]):
        cur["conversion_from_previous"] = _rate(cur["count"], prev["count"])

    # ── Outreach effectiveness ───────────────────────────────────────────────
    def pers_bucket(a): return "3+" if a.personalisation_points >= 3 else str(a.personalisation_points)
    outreach = {
        "overall": outcome_stats(attributions, min_n),
        "by_channel": group_stats(attributions, lambda a: a.channel or "unknown", min_n),
        "by_angle": dict(list(group_stats(attributions, lambda a: a.angle or "(none)", min_n).items())[:10]),
        "by_engine": group_stats(attributions, lambda a: a.engine or "unknown", min_n),
        "by_personalisation_points": group_stats(attributions, pers_bucket, min_n),
        "research_effectiveness": group_stats(attributions,
                                              lambda a: "researched first" if a.research_before_touch else "no research",
                                              min_n),
        "by_draft_provider": group_stats(attributions, lambda a: a.draft_provider or "unknown", min_n),
    }
    segments = {
        "industry": group_stats(attributions, lambda a: a.segment.get("industry", "unknown"), min_n),
        "size_band": group_stats(attributions, lambda a: a.segment.get("size_band", "unknown"), min_n),
        "seniority": group_stats(attributions, lambda a: a.segment.get("seniority", "unknown"), min_n),
    }

    # ── Qualification accuracy (score vs outcome, contacted leads) ───────────
    scores = dict((await db.execute(select(Lead.id, Lead.score).where(Lead.id.in_(contacted_leads)))).all()) \
        if contacted_leads else {}

    def score_bucket(a):
        s = scores.get(a.lead_id)
        return "unscored" if s is None else ("80-100" if s >= 80 else "60-79" if s >= 60 else "40-59" if s >= 40 else "0-39")
    qualification = {"score_vs_outcome": group_stats(attributions, score_bucket, min_n),
                     "note": "Higher score buckets should show higher positive rates if qualification is calibrated."}

    # ── Providers & cost ─────────────────────────────────────────────────────
    exec_events = (await db.execute(select(Event).where(Event.event_type.in_(("task_completed", "task_failed")),
                                                        Event.occurred_at >= since))).scalars().all()
    prov: dict[tuple[str, str], dict] = defaultdict(lambda: {"success": 0, "failed": 0, "latency_ms": [], "cost": 0.0})
    cost_by_capability: Counter = Counter()
    for ev in exec_events:
        p = _payload(ev)
        key = (p.get("provider") or "none", p.get("capability") or p.get("task_type") or "unknown")
        row = prov[key]
        row["success" if ev.event_type == "task_completed" else "failed"] += 1
        if p.get("duration_ms") is not None:
            row["latency_ms"].append(float(p["duration_ms"]))
        cost = float(p.get("cost") or 0.0) if ev.event_type == "task_completed" else 0.0
        row["cost"] += cost
        cost_by_capability[key[1]] += cost
    providers = [{"provider": k[0], "capability": k[1], "success": v["success"], "failed": v["failed"],
                  "success_rate": _rate(v["success"], v["success"] + v["failed"]),
                  "median_latency_ms": round(median(v["latency_ms"]), 1) if v["latency_ms"] else None,
                  "cost": round(v["cost"], 4)} for k, v in sorted(prov.items())]
    total_cost = round(sum(cost_by_capability.values()), 4)
    costs = {"total": total_cost, "by_capability": {k: round(v, 4) for k, v in cost_by_capability.items()},
             "per_positive_reply": round(total_cost / len(positive_leads), 4) if positive_leads else None,
             "per_meeting": round(total_cost / len(meeting_leads), 4) if meeting_leads else None,
             "note": "LLM token costs are recorded per decision in agent_decisions.context_snapshot.llm.usage."}

    # ── Approvals, guardrails, engines ───────────────────────────────────────
    approvals = (await db.execute(select(Approval).where(Approval.requested_at >= since))).scalars().all()
    decided = [a for a in approvals if a.decided_at]
    approval_stats = {
        "by_status": dict(Counter(a.status for a in approvals)),
        "edit_rate": _rate(sum(a.edited for a in approvals if a.status == "approved"),
                           sum(a.status == "approved" for a in approvals)),
        "median_minutes_to_decision": round(median([(a.decided_at - a.requested_at).total_seconds() / 60
                                                     for a in decided]), 1) if decided else None,
    }
    decisions = (await db.execute(select(AgentDecision).where(AgentDecision.created_at >= since))).scalars().all()
    blocked_rules: Counter = Counter()
    for d in decisions:
        if d.status == "blocked" and d.guardrail_result:
            blocked_rules.update(d.guardrail_result.get("rule_ids", []))
    engine_counts = Counter(d.engine for d in decisions)
    decision_stats = {
        "total": len(decisions), "by_engine": dict(engine_counts), "by_status": dict(Counter(d.status for d in decisions)),
        "fallback_rate": _rate(engine_counts.get("rules_fallback", 0),
                               engine_counts.get("llm", 0) + engine_counts.get("rules_fallback", 0)),
        "blocked_by_rule": dict(blocked_rules),
        "compliance_blocks_at_send": (await db.execute(select(func.count(Event.id)).where(
            Event.event_type == "compliance_blocked", Event.occurred_at >= since))).scalar() or 0,
    }

    dashboard = {
        "window_days": days, "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": {"funnel": funnel, "lead_status": statuses},
        "outreach": outreach, "segments": segments, "qualification": qualification,
        "providers": providers, "costs": costs, "approvals": approval_stats, "decisions": decision_stats,
    }
    dashboard["recommendations"] = recommendations(dashboard, cfg.min_sample_for_recommendation)
    return dashboard


def recommendations(d: dict, min_n: int) -> list[dict]:
    """Deterministic, evidence-backed suggestions for a human. Nothing is changed automatically."""
    recs = []
    overall = d["outreach"]["overall"]
    base = overall.get("positive_rate")
    if base is not None and overall["n"] >= min_n:
        for name, s in d["outreach"]["by_channel"].items():
            if s["n"] >= min_n and s["positive_rate"] is not None and s["positive_rate"] < base / 2:
                recs.append({"area": "channels", "suggestion": f"Channel '{name}' converts at less than half the "
                             f"average; review messaging or deprioritise it in the campaign.",
                             "evidence": {"channel": s, "overall": overall}})
        rs = d["outreach"]["research_effectiveness"]
        a, b = rs.get("researched first"), rs.get("no research")
        if a and b and a["n"] >= min_n and b["n"] >= min_n and a["positive_rate"] is not None \
                and b["positive_rate"] is not None and a["positive_rate"] > b["positive_rate"] * 1.5:
            recs.append({"area": "research", "suggestion": "Touches preceded by research convert notably better; "
                         "consider requiring research before first outreach in the campaign constraints.",
                         "evidence": rs})
    for p in d["providers"]:
        total = p["success"] + p["failed"]
        if total >= min_n and p["success_rate"] is not None and p["success_rate"] < 0.8:
            recs.append({"area": "providers", "suggestion": f"Provider '{p['provider']}' fails "
                         f"{(1 - p['success_rate']):.0%} of '{p['capability']}' calls; check its health, credentials "
                         f"or priority.", "evidence": p})
    approvals = d["approvals"]
    approved = approvals["by_status"].get("approved", 0)
    if approved >= min_n and (approvals["edit_rate"] or 0) > 0.5:
        recs.append({"area": "drafting", "suggestion": "Reviewers edit most drafts before approving; review the "
                     "campaign tone/offer and the outreach policy.", "evidence": approvals})
    fb = d["decisions"]["fallback_rate"]
    if fb is not None and fb > 0.2 and d["decisions"]["total"] >= min_n:
        recs.append({"area": "ai", "suggestion": f"{fb:.0%} of decisions used the rules fallback; check LLM "
                     f"credentials and availability (GET /api/v1/oxygen/engine).", "evidence": d["decisions"]})
    return recs
