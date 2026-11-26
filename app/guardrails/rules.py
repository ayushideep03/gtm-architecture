"""
Deterministic guardrail rules.

Rules read capability metadata from the catalogue (schema, target type,
requirements, repeatability) instead of naming task types, so a new capability
is governed automatically and no rule needs editing. Rule behaviour itself is
code, not configuration: AI decisions and config cannot switch safety checks off.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities import get_capability_catalogue
from app.guardrails.interfaces import GuardrailRuleEvaluator
from app.guardrails.models import GuardrailContext, GuardrailRule, GuardrailSeverity

_DONE = ("completed", "done")
_ACTIVE = ("pending", "in_progress")


def _spec(context: GuardrailContext, name: Optional[str] = None):
    return get_capability_catalogue().get(name or context.requested_capability, context.tenant_id)


def _canonical(name: Optional[str]) -> Optional[str]:
    return get_capability_catalogue().canonical(name) or name


def _same_capability(task: dict[str, Any], capability: str) -> bool:
    return _canonical(task.get("task_type")) == capability


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class UnknownCapabilityRule(GuardrailRuleEvaluator):
    """GR-001: the capability must exist in the catalogue and be enabled for the tenant."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-001", description="Block unknown or unregistered capabilities",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        catalogue = get_capability_catalogue()
        name = context.requested_capability
        if catalogue.canonical(name) is None:
            return False, f"Capability '{name}' is unknown or unavailable in registry"
        if not catalogue.is_enabled(name, context.tenant_id):
            return False, f"Capability '{name}' is disabled for this organisation"
        return True, None


class MissingTargetRule(GuardrailRuleEvaluator):
    """GR-002: capabilities that act on an entity need a target id."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-002", description="Block actionable decisions without a target ID",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        spec = _spec(context)
        needs_target = spec is None or spec.target_type is not None
        if needs_target and not context.target_id:
            return False, f"Action '{context.action}' requires a valid target_id"
        return True, None


class MissingRequiredTaskPayloadRule(GuardrailRuleEvaluator):
    """GR-003: parameters must validate against the capability's input schema."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-003", description="Block actions missing mandatory execution payload parameters",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        spec = _spec(context, context.task_type or context.action) or _spec(context)
        if spec is None:
            return True, None  # unknown capabilities are handled by GR-001 / GR-007
        validation = spec.validate_parameters(
            context.parameters,
            {"lead_id": context.lead_id, "company_id": context.company_id, "person_id": context.person_id},
        )
        if not validation.ok:
            return False, f"{spec.name} parameters invalid: " + "; ".join(validation.errors)
        return True, None


class DuplicateActiveTaskRule(GuardrailRuleEvaluator):
    """GR-004: at most one active task per capability per lead."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-004", description="Block duplicate active (pending or in_progress) tasks",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        capability = _canonical(context.task_type or context.action)
        for task in context.task_history:
            if _same_capability(task, capability) and task.get("status") in _ACTIVE:
                return False, f"An active task of type '{capability}' already exists for this lead"
        return True, None


class TaskAlreadyCompletedRule(GuardrailRuleEvaluator):
    """GR-005: enforce the capability's repeatability policy (once / max count / cooldown)."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-005", description="Block repeated execution of already completed tasks",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        spec = _spec(context, context.task_type or context.action)
        if spec is None:
            return True, None
        policy = spec.repeatability
        completed = [t for t in context.task_history
                     if _same_capability(t, spec.name) and t.get("status") in _DONE]
        if not completed:
            return True, None

        if policy.mode == "once_per_lead":
            return False, f"Task '{spec.name}' has already been successfully completed for this lead"
        if policy.mode == "once_per_target":
            target = str(context.target_id) if context.target_id else None
            if any(str(t.get("target_id")) == target for t in completed if t.get("target_id")) or target is None:
                return False, f"Task '{spec.name}' has already been completed for this target"
        if policy.max_per_lead is not None and len(completed) >= policy.max_per_lead:
            return False, (f"Task '{spec.name}' has already been successfully completed "
                           f"{len(completed)} time(s) for this lead (limit {policy.max_per_lead})")
        if policy.cooldown_s > 0:
            now = datetime.now(timezone.utc)
            for task in completed:
                ts = _parse_ts(task.get("completed_at"))
                # Unknown completion time is treated as recent (fail closed).
                if ts is None or (now - ts).total_seconds() < policy.cooldown_s:
                    return False, (f"Task '{spec.name}' has already been successfully completed "
                                   f"within its cooldown of {int(policy.cooldown_s)}s")
        return True, None


class ExecutionCapabilityNotRegisteredRule(GuardrailRuleEvaluator):
    """GR-006: there must be an eligible provider for the capability right now."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-006", description="Block actions whose task type has no registered execution provider",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        from app.providers import get_provider_selector
        from app.providers.selection import SelectionRequest

        capability = _canonical(context.task_type or context.action)
        if not await get_provider_selector().has_eligible(SelectionRequest(capability=capability, tenant_id=context.tenant_id)):
            return False, f"No execution provider registered for task type '{capability}'"
        return True, None


class UnsupportedTaskTypeRule(GuardrailRuleEvaluator):
    """GR-007: the task type must be a catalogue capability consistent with the declared capability."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-007", description="Block unrecognized or unsupported task types",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        catalogue = get_capability_catalogue()
        task_type = context.task_type or context.action
        canonical_tt = catalogue.canonical(task_type)
        if canonical_tt is None:
            return False, f"Task type '{task_type}' is unsupported by the GTM policy engine"
        if context.capability and catalogue.canonical(context.capability) not in (None, canonical_tt):
            return False, (f"Task type '{task_type}' does not match declared capability "
                           f"'{context.capability}'")
        return True, None


class MissingRequiredCRMEntityRule(GuardrailRuleEvaluator):
    """GR-008: data the capability requires (company, person.email, ...) must exist."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-008", description="Block actions missing necessary CRM entities",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.lead_id:
            return False, "Action requires an associated lead_id"
        if not context.is_actionable:
            return True, None
        spec = _spec(context, context.task_type or context.action)
        if spec is None:
            return True, None
        facts = context.facts()
        missing = [req for req in spec.requires if not facts.get(req, False)]
        if missing:
            return False, f"Cannot execute {spec.name}: required data missing ({', '.join(missing)})"
        return True, None


class SafeValidInternalExecutionRule(GuardrailRuleEvaluator):
    """GR-009: marker rule recording that all checks ran."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-009", description="Authorize verified safe internal GTM actions",
                             severity=GuardrailSeverity.WARN, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        return True, None


class IdempotencyRule(GuardrailRuleEvaluator):
    """GR-010: the exact same action (same idempotency key) must never be recorded twice."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-010", description="Block repeating an action already recorded under the same idempotency key",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable:
            return True, None
        spec = _spec(context, context.task_type or context.action)
        if spec is None or not spec.idempotency_fields:
            return True, None
        validation = spec.validate_parameters(
            context.parameters,
            {"lead_id": context.lead_id, "company_id": context.company_id, "person_id": context.person_id},
        )
        key = spec.idempotency_key(context.lead_id, validation.parameters)
        for task in context.task_history:
            if key and task.get("idempotency_key") == key:
                return False, (f"Action already recorded (task {task.get('id')}, status {task.get('status')}); "
                               f"recover that task instead of repeating it")
        return True, None


def _external(context: GuardrailContext):
    from app.core.gtm_config import SideEffect
    spec = _spec(context, context.task_type or context.action)
    if spec is None or spec.side_effect not in (SideEffect.EXTERNAL_COMMUNICATION, SideEffect.SPEND):
        return None
    return spec


class SuppressionRule(GuardrailRuleEvaluator):
    """GR-011: never contact a suppressed email, domain, person or company."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-011", description="Block external contact with suppressed recipients",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable or _external(context) is None:
            return True, None
        if context.compliance is None:
            return False, "Compliance status unknown for this contact; external action blocked (fail closed)"
        if context.compliance.get("suppressed"):
            return False, "Recipient is suppressed: " + "; ".join(context.compliance.get("suppression_reasons", []))
        return True, None


class LawfulBasisRule(GuardrailRuleEvaluator):
    """GR-012: external contact needs a recorded, allowed lawful basis."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-012", description="Block external contact without a valid lawful basis",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable or _external(context) is None:
            return True, None
        if context.compliance is None:
            return False, "Compliance status unknown for this contact; external action blocked (fail closed)"
        if not context.compliance.get("lawful_basis_ok", False):
            return False, context.compliance.get("lawful_basis_reason") or "No valid lawful basis"
        return True, None


class ContactFrequencyRule(GuardrailRuleEvaluator):
    """GR-013: respect configured contact-frequency caps and minimum gap between touches."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(rule_id="GR-013", description="Block external contact above frequency limits",
                             severity=GuardrailSeverity.BLOCK, enabled=True)

    async def evaluate(self, context: GuardrailContext, session: Optional[AsyncSession] = None):
        if not context.is_actionable or _external(context) is None:
            return True, None
        if context.compliance is None:
            return False, "Compliance status unknown for this contact; external action blocked (fail closed)"
        if not context.compliance.get("frequency_ok", False):
            return False, context.compliance.get("frequency_reason") or "Contact frequency limit reached"
        return True, None
