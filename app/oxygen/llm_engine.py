"""
LLM-driven decision engine — the Sales Controller.

    prospect memory ──► capability menu (catalogue × live availability × data requirements)
                   └──► planner prompt ──► LLM (profile from config, vendor fallback)
                                              │
                         strict-schema proposal ◄┘
                                │ validate: action allowed, parameters match the capability's
                                │ input schema, evidence ids exist in memory
                                ├─ invalid → one repair round with the errors → still invalid → rules fallback
                                ├─ low confidence → escalate to a human
                                ▼
                             Decision (with reasoning, evidence, confidence, risks, alternatives)

If no LLM is available (no API key, vendor down, refusal on every profile) the
deterministic rules engine decides, and the decision says so (engine
"rules_fallback" + fallback_reason). Guardrails, compliance and approval run
after this engine regardless of which engine decided.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from app.capabilities import get_capability_catalogue
from app.core.gtm_config import GTMConfig, get_gtm_config
from app.core.logging import get_logger
from app.llm import LLMError, LLMRouter, LLMUnavailable
from app.oxygen.decision import DeterministicDecisionEngine
from app.oxygen.interfaces import DecisionEngine
from app.oxygen.models import Decision, OxygenContext
from app.oxygen.planner import (
    NON_TASK_ACTIONS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ActionProposal,
    evidence_ids,
    proposal_schema,
    render_context,
)

logger = get_logger(__name__)

# Lead-level planning never discovers new leads.
_EXCLUDED_CATEGORIES = {"lead_source"}


def _requirement_facts(memory: dict) -> dict[str, bool]:
    person, company = memory.get("person"), memory.get("company")
    return {
        "lead": True,
        "company": bool(company and company.get("domain")),
        "company.domain": bool(company and company.get("domain")),
        "person": bool(person and person.get("email")),
        "person.email": bool(person and person.get("email")),
        "person.linkedin_url": bool(person and person.get("linkedin_url")),
    }


class LLMDecisionEngine(DecisionEngine):
    engine_name = "llm"

    def __init__(
        self,
        router: Optional[LLMRouter] = None,
        fallback: Optional[DecisionEngine] = None,
        config_getter=get_gtm_config,
    ) -> None:
        self.router = router or LLMRouter()
        self.fallback = fallback or DeterministicDecisionEngine()
        self._config_getter = config_getter

    # ── capability menu ──────────────────────────────────────────────────────
    async def capability_menu(self, context: OxygenContext, cfg: GTMConfig) -> tuple[list[dict], list[dict]]:
        from app.providers import get_provider_selector
        from app.providers.selection import SelectionRequest

        catalogue = get_capability_catalogue()
        tenant = getattr(context.lead, "tenant_id", None)
        memory = context.memory or {}
        facts = _requirement_facts(memory)
        tasks = memory.get("tasks", [])
        available, unavailable = [], []
        for spec in catalogue.specs():
            if spec.category in _EXCLUDED_CATEGORIES:
                continue
            spec = catalogue.get(spec.name, tenant) or spec
            missing = [r for r in spec.requires if not facts.get(r, False)]
            reason = None
            if not catalogue.is_enabled(spec.name, tenant):
                reason = "disabled for this organisation"
            elif missing:
                reason = f"missing data: {', '.join(missing)}"
            elif not await get_provider_selector().has_eligible(SelectionRequest(capability=spec.name, tenant_id=tenant)):
                reason = "no provider currently available"
            if reason:
                unavailable.append({"name": spec.name, "reason": reason})
                continue
            mine = [t for t in tasks if t["capability"] == spec.name]
            available.append({
                "name": spec.name,
                "description": spec.description,
                "side_effect": spec.side_effect.value,
                "requires_human_approval": catalogue.requires_approval(spec.name, tenant),
                "input_schema": spec.input_schema(),
                "history": {
                    "completed": sum(1 for t in mine if t["status"] in ("completed", "done")),
                    "pending_or_awaiting_approval": sum(1 for t in mine if t["status"] in
                                                        ("pending", "in_progress", "awaiting_approval")),
                    "last_completed_at": next((t["completed_at"] for t in mine if t["completed_at"]), None),
                    "repeat_policy": spec.repeatability.model_dump(),
                },
            })
        return available, unavailable

    # ── decide ───────────────────────────────────────────────────────────────
    async def decide(self, context: OxygenContext) -> Decision:
        cfg = self._config_getter().for_tenant(getattr(context.lead, "tenant_id", None))
        if context.memory is None:
            return self._fallback(context, "prospect memory unavailable")

        available, unavailable = await self.capability_menu(context, cfg)
        allowed = [c["name"] for c in available] + NON_TASK_ACTIONS
        menu = available + [{"name": u["name"], "available": False, "reason": u["reason"]} for u in unavailable]
        user = render_context(context.memory, menu, cfg)
        schema = proposal_schema(allowed)

        try:
            result = await self.router.complete_json(profile=cfg.oxygen.llm_profile, system=SYSTEM_PROMPT,
                                                     user=user, schema=schema, schema_name="action_proposal")
        except LLMUnavailable as exc:
            return self._fallback(context, f"LLM unavailable: {exc}")
        except LLMError as exc:
            return self._fallback(context, f"LLM error: {exc}")

        proposal, params, errors = self._validate(result.data, allowed, context)
        repaired = False
        if errors:
            repair_user = (user + "\n\nYOUR PREVIOUS PROPOSAL WAS REJECTED BY VALIDATION:\n- " + "\n- ".join(errors)
                           + "\n\nPrevious proposal:\n" + json.dumps(result.data, default=str)
                           + "\n\nReturn a corrected proposal.")
            try:
                result = await self.router.complete_json(profile=cfg.oxygen.llm_profile, system=SYSTEM_PROMPT,
                                                         user=repair_user, schema=schema,
                                                         schema_name="action_proposal")
            except LLMError as exc:
                return self._fallback(context, f"LLM repair failed: {exc}; original errors: {'; '.join(errors)}")
            proposal, params, errors = self._validate(result.data, allowed, context)
            repaired = True
            if errors:
                return self._fallback(context, "LLM proposal invalid after repair: " + "; ".join(errors))

        return self._to_decision(proposal, params, context, cfg, result, repaired)

    # ── validation ───────────────────────────────────────────────────────────
    def _validate(self, data: dict, allowed: list[str], context: OxygenContext):
        errors: list[str] = []
        try:
            proposal = ActionProposal.model_validate(data)
        except ValidationError as exc:
            return None, {}, [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()]
        if proposal.action not in allowed:
            errors.append(f"action {proposal.action!r} is not in the allowed list {allowed}")
        try:
            params = proposal.parameters()
        except (ValueError, TypeError) as exc:
            return proposal, {}, errors + [f"parameters_json: {exc}"]
        if proposal.action not in NON_TASK_ACTIONS and not errors:
            spec = get_capability_catalogue().get(proposal.action, getattr(context.lead, "tenant_id", None))
            check = spec.validate_parameters(params, {
                "lead_id": context.lead_id,
                "company_id": context.company.id if context.company else None,
                "person_id": context.person.id if context.person else None,
            })
            if not check.ok:
                errors += [f"parameters for {proposal.action}: {e}" for e in check.errors]
            else:
                params = check.parameters
        return proposal, params, errors

    # ── assembly ─────────────────────────────────────────────────────────────
    def _to_decision(self, p: ActionProposal, params: dict, context: OxygenContext, cfg: GTMConfig,
                     result, repaired: bool) -> Decision:
        known = evidence_ids(context.memory)
        refs = [r for r in p.evidence_refs if r in known]
        dropped = [r for r in p.evidence_refs if r not in known]
        action, reasoning = p.action, p.reasoning
        if action not in NON_TASK_ACTIONS and p.confidence < cfg.oxygen.min_confidence:
            reasoning = (f"Escalated: proposed '{action}' with confidence {p.confidence:.2f} below "
                         f"{cfg.oxygen.min_confidence:.2f}. Original reasoning: {p.reasoning}")
            action = "escalate"
        wait_hours = None
        if action == "wait":
            wait_hours = min(p.wait_hours or cfg.oxygen.default_wait_hours, cfg.oxygen.max_wait_hours)
        spec = get_capability_catalogue().get(action) if action not in NON_TASK_ACTIONS else None
        target_type = spec.target_type if spec else "lead"
        target_id = {"company": context.company.id if context.company else None,
                     "person": context.person.id if context.person else None}.get(target_type, context.lead_id)
        return Decision(
            action=action,
            reason=reasoning,
            target_id=target_id or context.lead_id,
            target_type=target_type or "lead",
            confidence=p.confidence,
            capability=action if action not in NON_TASK_ACTIONS else None,
            parameters=params if action not in NON_TASK_ACTIONS else {},
            source_event_ids=[e.id for e in context.events[:5]],
            engine="llm",
            model=f"{result.vendor}/{result.model}",
            prompt_version=PROMPT_VERSION,
            channel=p.channel,
            situation_summary=p.situation_summary,
            missing_information=p.missing_information,
            interest_signals=p.interest_signals,
            worth_pursuing=p.worth_pursuing,
            reasoning=reasoning,
            evidence_refs=refs,
            expected_outcome=p.expected_outcome,
            uncertainty=p.uncertainty,
            risks=p.risks,
            alternatives=[a.model_dump() for a in p.alternatives_considered],
            model_requested_approval=p.needs_human_approval,
            wait_hours=wait_hours,
            llm_meta={"vendor": result.vendor, "model": result.model, "profile": result.profile,
                      "latency_ms": result.latency_ms, "usage": result.usage, "fallback_attempts": result.attempts,
                      "repaired": repaired, "dropped_evidence_refs": dropped},
        )

    def _fallback(self, context: OxygenContext, why: str) -> Decision:
        logger.warning("Oxygen falling back to rules engine for lead %s: %s", context.lead_id, why)
        decision = self.fallback.decide(context)
        decision.engine = "rules_fallback"
        decision.fallback_reason = why
        decision.reasoning = f"[Rules fallback — {why}] {decision.reason}"
        return decision
