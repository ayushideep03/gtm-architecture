"""
Provider selection.

For a capability request, the selector:
  1. filters providers deterministically: capability enabled for the tenant,
     provider enabled for the tenant, constructed with credentials, matching
     constraints (e.g. region), within cost ceiling, not cooling down after
     repeated failures, with rate-limit capacity;
  2. ranks survivors by a weighted score of configured priority, observed
     reliability (Bayesian-smoothed success rate from the event log), observed
     latency and cost. Weights come from config.

Every decision is explainable: the result lists each candidate's score
breakdown and why every other provider was rejected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.catalogue import CapabilityCatalogue
from app.core.gtm_config import GTMConfig, ProviderConfig, get_gtm_config
from app.providers.registry import ProviderRecord, ProviderRegistry
from app.providers.runtime import (
    HealthTracker,
    InMemoryProviderHealth,
    InMemoryRateLimiter,
    ProviderStat,
    RateLimiter,
)


class StatsSource(Protocol):
    async def get(self, session, capability: str, names: list[str], window_days: int) -> dict[str, ProviderStat]: ...


@dataclass
class SelectionRequest:
    capability: str
    tenant_id: Optional[uuid.UUID] = None
    preferred: Optional[str] = None            # pin a provider by name
    attributes: dict[str, str] = field(default_factory=dict)  # matched against provider constraints
    max_cost: Optional[float] = None
    exclude: set[str] = field(default_factory=set)
    require_mode: Optional[str] = None         # "live" | "dry_run"


@dataclass
class Candidate:
    record: ProviderRecord
    config: ProviderConfig
    score: float
    breakdown: dict[str, float]

    @property
    def name(self) -> str:
        return self.record.name


@dataclass
class Rejection:
    provider: str
    reason: str


@dataclass
class SelectionResult:
    capability: str
    candidates: list[Candidate]
    rejected: list[Rejection]

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    def explain(self) -> dict:
        return {
            "capability": self.capability,
            "ranked": [{"provider": c.name, "score": round(c.score, 4), **{k: round(v, 4) for k, v in c.breakdown.items()}}
                       for c in self.candidates],
            "rejected": [{"provider": r.provider, "reason": r.reason} for r in self.rejected],
        }


class ProviderSelector:
    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        config_getter: Callable[[], GTMConfig] = get_gtm_config,
        stats: Optional[StatsSource] = None,
        health: Optional[HealthTracker] = None,
        rate_limiter: Optional[RateLimiter] = None,
        outcomes=None,
    ) -> None:
        self.registry = registry
        self._config_getter = config_getter
        self.stats = stats
        self.health: HealthTracker = health or InMemoryProviderHealth()
        self.rate_limiter: RateLimiter = rate_limiter or InMemoryRateLimiter()
        self.outcomes = outcomes   # learned positive-outcome rates (OutcomeQualityStats); optional

    @property
    def catalogue(self) -> CapabilityCatalogue:
        return self.registry.catalogue

    def config_for(self, record: ProviderRecord, tenant_id: Optional[uuid.UUID]) -> ProviderConfig:
        cfg = self._config_getter().for_tenant(tenant_id)
        return cfg.provider(record.name) or record.config

    # ── deterministic filtering ──────────────────────────────────────────────
    async def eligible(self, req: SelectionRequest) -> tuple[list[tuple[ProviderRecord, ProviderConfig]], list[Rejection]]:
        cfg = self._config_getter()
        canonical = self.catalogue.canonical(req.capability) or req.capability
        records = self.registry.candidates(canonical)
        if canonical in self.catalogue and not self.catalogue.is_enabled(canonical, req.tenant_id, cfg):
            return [], [Rejection(r.name, f"capability {canonical!r} disabled for tenant") for r in records]

        ok: list[tuple[ProviderRecord, ProviderConfig]] = []
        rejected: list[Rejection] = []
        for record in records:
            pc = self.config_for(record, req.tenant_id)
            reason = await self._reject_reason(record, pc, req)
            if reason:
                rejected.append(Rejection(record.name, reason))
            else:
                ok.append((record, pc))
        if req.preferred and not any(r.name == req.preferred for r, _ in ok) \
                and not any(r.provider == req.preferred for r in rejected):
            rejected.append(Rejection(req.preferred, f"not registered for capability {canonical!r}"))
        return ok, rejected

    async def _reject_reason(self, record: ProviderRecord, pc: ProviderConfig, req: SelectionRequest) -> Optional[str]:
        if req.preferred and record.name != req.preferred:
            return "not the requested provider"
        if record.name in req.exclude:
            return "excluded for this attempt"
        if not record.available:
            return record.unavailable_reason or "unavailable"
        if not pc.enabled:
            return "disabled by configuration"
        if req.require_mode and pc.mode != req.require_mode:
            return f"mode {pc.mode!r} does not match required {req.require_mode!r}"
        for key, allowed in pc.constraints.items():
            value = req.attributes.get(key)
            if value is not None and value not in allowed:
                return f"constraint {key}={value!r} not in {allowed}"
        if req.max_cost is not None and pc.cost_per_call > req.max_cost:
            return f"cost {pc.cost_per_call} exceeds ceiling {req.max_cost}"
        if await self.health.cooling_down(record.name):
            state = await self.health.state(record.name)
            return (f"cooling down after repeated failures "
                    f"({state.get('cooldown_remaining_s', 0):.0f}s left, last error {state.get('last_error_code')})")
        if not await self.rate_limiter.has_capacity(record.name, pc.rate_limit):
            return "rate limit reached"
        return None

    async def has_eligible(self, req: SelectionRequest) -> bool:
        return bool((await self.eligible(req))[0])

    # ── ranking ──────────────────────────────────────────────────────────────
    async def rank(self, req: SelectionRequest, session: Optional[AsyncSession] = None) -> SelectionResult:
        eligible, rejected = await self.eligible(req)
        canonical = self.catalogue.canonical(req.capability) or req.capability
        if not eligible:
            return SelectionResult(canonical, [], rejected)

        sel = self._config_getter().selection
        stats: dict[str, ProviderStat] = {}
        if self.stats is not None:
            spec = self.catalogue.get(canonical)
            names = [canonical] + (spec.aliases if spec else [])
            stats = await self.stats.get(session, canonical, names, sel.reliability_window_days)

        outcome_counts: dict = {}
        if self.outcomes is not None:
            outcome_counts = await self.outcomes.get(session, canonical)

        w = sel.weights
        scored: list[Candidate] = []
        for record, pc in eligible:
            st = stats.get(record.name, ProviderStat())
            reliability = (st.successes + sel.reliability_prior_successes) / (
                st.total + sel.reliability_prior_successes + sel.reliability_prior_failures)
            latency = 0.5 if st.avg_latency_s is None else 1.0 / (1.0 + st.avg_latency_s / sel.latency_reference_s)
            cost = 1.0 / (1.0 + pc.cost_per_call / sel.cost_reference) if sel.cost_reference > 0 else 1.0
            priority = pc.priority / 100.0
            pos, n = outcome_counts.get(record.name, (0, 0))
            outcome_quality = (pos + 1.0) / (n + 4.0)          # Beta(1,3) prior: unknown = modest, not zero
            breakdown = {
                "priority": priority, "reliability": reliability, "latency": latency, "cost": cost,
                "outcome_quality": outcome_quality, "outcome_observations": float(n),
                "observations": float(st.total),
            }
            score = (w.priority * priority + w.reliability * reliability + w.latency * latency + w.cost * cost
                     + (w.outcome_quality * outcome_quality if outcome_counts else 0.0))
            scored.append(Candidate(record=record, config=pc, score=score, breakdown=breakdown))
        scored.sort(key=lambda c: (-c.score, c.name))
        return SelectionResult(canonical, scored, rejected)

    async def select(self, req: SelectionRequest, session: Optional[AsyncSession] = None) -> Optional[Candidate]:
        return (await self.rank(req, session)).best
