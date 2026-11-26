"""
ICP qualification engine: deterministic, configurable, explainable.

A campaign's ICP (``campaign.icp``) defines target industries, employee range,
countries, technologies, titles and departments, a minimum score and optional
weights. ``score_prospect`` returns a 0-100 score, a fit category, one scored
and explained dimension per rule, and qualification / disqualification reasons.

It is exposed as the ``qualify_lead`` provider ``icp_rules`` (so the workflow
engine uses it like any other provider) and as a bulk "recalculate" for when a
user edits the ICP.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.execution.interfaces import ExecutionProvider

DEFAULT_WEIGHTS = {"industry": 30, "company_size": 20, "location": 15, "technology": 15, "persona": 20}
HARD_DISQUALIFIERS = ("industry",)          # outside the target industries -> disqualified regardless of score


@dataclass
class Dimension:
    name: str
    label: str
    score: float             # 0..1 match
    weight: int
    detail: str
    evidence: str

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "match": round(self.score, 2), "weight": self.weight,
                "points": round(self.score * self.weight, 1), "detail": self.detail, "evidence": self.evidence}


@dataclass
class IcpResult:
    score: int
    fit: str
    qualified: bool
    dimensions: list[Dimension] = field(default_factory=list)
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": self.score, "fit": self.fit, "qualified": self.qualified,
                "dimensions": [d.as_dict() for d in self.dimensions], "reasons_for": self.reasons_for,
                "reasons_against": self.reasons_against}


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _techs(company: Any) -> list[str]:
    raw = getattr(company, "technologies", None)
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else [str(val)]
    except ValueError:
        return [t.strip() for t in str(raw).split(",") if t.strip()]


def fit_category(score: int) -> str:
    return "strong" if score >= 75 else "good" if score >= 60 else "weak" if score >= 40 else "poor"


def score_prospect(company: Any, person: Any, icp: dict) -> IcpResult:
    w = {**DEFAULT_WEIGHTS, **(icp.get("weights") or {})}
    dims: list[Dimension] = []

    industry = getattr(company, "industry", None)
    targets = [_norm(i) for i in icp.get("industries") or []]
    if not targets:
        dims.append(Dimension("industry", "Industry relevance", 1.0, w["industry"], "No industry restriction", "—"))
    elif not industry:
        dims.append(Dimension("industry", "Industry relevance", 0.3, w["industry"],
                              "Industry unknown — scored conservatively", "company.industry missing"))
    else:
        hit = _norm(industry) in targets
        dims.append(Dimension("industry", "Industry relevance", 1.0 if hit else 0.0, w["industry"],
                              f"{industry} {'is' if hit else 'is not'} a target industry "
                              f"({', '.join(icp.get('industries'))})", f"company.industry = {industry}"))

    emp = getattr(company, "employee_count", None)
    lo, hi = icp.get("min_employees"), icp.get("max_employees")
    if emp is None:
        dims.append(Dimension("company_size", "Company size match", 0.3, w["company_size"],
                              "Employee count unknown", "company.employee_count missing"))
    else:
        inside = (lo is None or emp >= lo) and (hi is None or emp <= hi)
        near = not inside and ((lo and lo * 0.7 <= emp < lo) or (hi and hi < emp <= hi * 1.3))
        dims.append(Dimension("company_size", "Company size match", 1.0 if inside else 0.5 if near else 0.0,
                              w["company_size"], f"{emp} employees vs target {lo or 0}–{hi or '∞'}"
                              + ("" if inside else " (near range)" if near else " (outside range)"),
                              f"company.employee_count = {emp}"))

    country = getattr(company, "country", None)
    countries = [_norm(c) for c in icp.get("countries") or []]
    if not countries:
        dims.append(Dimension("location", "Location", 1.0, w["location"], "No location restriction", "—"))
    else:
        hit = _norm(country) in countries
        dims.append(Dimension("location", "Location", 1.0 if hit else 0.0, w["location"],
                              f"{country or 'Unknown country'} {'is' if hit else 'is not'} in the target regions",
                              f"company.country = {country}"))

    techs = _techs(company)
    wanted = [_norm(t) for t in icp.get("technologies") or []]
    if not wanted:
        dims.append(Dimension("technology", "Technology relevance", 1.0, w["technology"], "No technology criteria", "—"))
    else:
        overlap = sorted({t for t in techs if _norm(t) in wanted})
        dims.append(Dimension("technology", "Technology relevance", min(1.0, len(overlap) / 2), w["technology"],
                              f"Uses {', '.join(overlap)}" if overlap else "No overlap with target technologies",
                              f"company.technologies = {', '.join(techs) or 'none recorded'}"))

    title, dept = getattr(person, "title", None), getattr(person, "department", None)
    titles = [_norm(t) for t in icp.get("titles") or []]
    depts = [_norm(d) for d in icp.get("departments") or []]
    if not titles and not depts:
        dims.append(Dimension("persona", "Persona fit", 1.0, w["persona"], "No persona criteria", "—"))
    else:
        t_hit = any(t and t in _norm(title) for t in titles)
        d_hit = _norm(dept) in depts
        val = 1.0 if t_hit else 0.6 if d_hit else 0.1
        dims.append(Dimension("persona", "Persona fit", val, w["persona"],
                              f"{title or 'Unknown title'} ({dept or 'unknown department'}) — "
                              + ("target title" if t_hit else "target department" if d_hit else "not a target persona"),
                              f"person.title = {title}; person.department = {dept}"))

    total_w = sum(d.weight for d in dims) or 1
    score = int(round(100 * sum(d.score * d.weight for d in dims) / total_w))
    min_score = int(icp.get("min_score", 60))
    hard = [d for d in dims if d.name in HARD_DISQUALIFIERS and d.score == 0.0]
    qualified = score >= min_score and not hard
    reasons_for = [d.detail for d in dims if d.score >= 0.99 and d.evidence != "—"]
    reasons_against = [d.detail for d in dims if d.score < 0.5]
    if hard:
        reasons_against.insert(0, f"Disqualifier: {hard[0].detail}")
    if score < min_score:
        reasons_against.append(f"Score {score} is below the campaign minimum of {min_score}")
    return IcpResult(score, fit_category(score), qualified, dims, reasons_for, reasons_against)


async def icp_for_lead(db, lead) -> dict:
    """The lead's campaign ICP, or the platform default campaign ICP from config."""
    from app.models.pipeline import Campaign
    if getattr(lead, "campaign_id", None):
        campaign = await db.get(Campaign, lead.campaign_id)
        if campaign is not None and campaign.icp:
            return campaign.icp
    from app.core.gtm_config import get_gtm_config
    cfg = get_gtm_config().oxygen.campaign
    return {"min_score": cfg.qualification_min_score}


async def qualify_lead_record(db, lead, *, source: str = "icp_rules", task_id=None) -> IcpResult:
    """Score, set lead status/score, and record the explained result as a qualification finding."""
    from app.memory.records import record_finding
    from app.models.base import Company, Person
    person = await db.get(Person, lead.person_id) if lead.person_id else None
    company = await db.get(Company, person.company_id) if person and person.company_id else None
    icp = await icp_for_lead(db, lead)
    result = score_prospect(company, person, icp)
    lead.score = result.score
    if lead.status in ("new", "enriching", "qualified", "disqualified"):
        lead.status = "qualified" if result.qualified else "disqualified"
    summary = (f"{'Qualified' if result.qualified else 'Not qualified'} ({result.score}/100, {result.fit} fit): "
               + ("; ".join(result.reasons_for[:2]) if result.qualified else "; ".join(result.reasons_against[:2])))
    await record_finding(db, tenant_id=lead.tenant_id, lead_id=lead.id, entity_type="lead", entity_id=lead.id,
                         kind="qualification", summary=summary, body={**result.as_dict(), "icp": icp,
                                                                      "engine": "icp_rules"},
                         confidence=result.score / 100, source=source, source_task_id=task_id,
                         citations=[{"evidence_ref": str(company.id) if company else None, "quote": d.evidence}
                                    for d in result.dimensions if d.evidence != "—"])
    return result


class IcpQualificationProvider(ExecutionProvider):
    """``qualify_lead`` provider backed by the deterministic ICP rules (no AI, no external call)."""

    capability = "qualify_lead"

    def __init__(self, name: str = "icp_rules", **_: Any) -> None:
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return [self.capability]

    async def execute(self, context, session=None):
        from app.execution.models import ExecutionResult
        from app.models.base import Lead
        if session is None or context.lead_id is None:
            return ExecutionResult(success=False, status="failed", provider=self._name, error_code="BAD_INPUT",
                                   error_message="qualify_lead needs a lead and a session")
        lead = await session.get(Lead, context.lead_id)
        result = await qualify_lead_record(session, lead, source=self._name, task_id=context.task_id)
        await session.flush()
        return ExecutionResult(success=True, status="completed", provider=self._name,
                               result={"qualified": result.qualified, "score": result.score, "fit": result.fit,
                                       "reasons_for": result.reasons_for, "reasons_against": result.reasons_against})

