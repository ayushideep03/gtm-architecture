"""Deterministic guardrail policy rules."""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.registry import execution_registry
from app.guardrails.interfaces import GuardrailRuleEvaluator
from app.guardrails.models import GuardrailContext, GuardrailRule, GuardrailSeverity


class UnknownCapabilityRule(GuardrailRuleEvaluator):
    """Rule GR-001: Block actions that target unknown or unregistered capabilities."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-001",
            description="Block unknown or unregistered capabilities",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        if context.capability:
            from app.oxygen.registry import capability_registry
            if not capability_registry.is_available(context.capability):
                return False, f"Capability '{context.capability}' is unknown or unavailable in registry"
        return True, None


class MissingTargetRule(GuardrailRuleEvaluator):
    """Rule GR-002: Block actionable decisions that lack a target entity ID."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-002",
            description="Block actionable decisions without a target ID",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        if not context.target_id:
            return False, f"Action '{context.action}' requires a valid target_id"
        return True, None


class MissingRequiredTaskPayloadRule(GuardrailRuleEvaluator):
    """Rule GR-003: Block actions that lack mandatory task parameters."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-003",
            description="Block actions missing mandatory execution payload parameters",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        tt = context.task_type or context.action

        if tt == "enrich_company":
            if not (context.parameters.get("company_id") or context.parameters.get("domain")):
                return False, "enrich_company task requires 'company_id' or 'domain' in payload"

        if tt == "enrich_person":
            if not (context.parameters.get("person_id") or context.parameters.get("email")):
                return False, "enrich_person task requires 'person_id' or 'email' in payload"

        if tt == "qualify_lead":
            if not (context.parameters.get("lead_id") or context.lead_id):
                return False, "qualify_lead task requires 'lead_id' in payload or context"

        return True, None


class DuplicateActiveTaskRule(GuardrailRuleEvaluator):
    """Rule GR-004: Block creating a task if an identical task is already active."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-004",
            description="Block duplicate active (pending or in_progress) tasks",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        tt = context.task_type or context.action
        for task in context.task_history:
            if task.get("task_type") == tt and task.get("status") in ("pending", "in_progress"):
                return False, f"An active task of type '{tt}' already exists for this lead"
        return True, None


class TaskAlreadyCompletedRule(GuardrailRuleEvaluator):
    """Rule GR-005: Block duplicate execution of single-run tasks that are already completed."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-005",
            description="Block repeated execution of already completed tasks",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        tt = context.task_type or context.action
        # qualify_lead is a single-run lifecycle milestone
        if tt == "qualify_lead":
            for task in context.task_history:
                if task.get("task_type") == tt and task.get("status") in ("completed", "done"):
                    return False, f"Task '{tt}' has already been successfully completed for this lead"
        return True, None


class ExecutionCapabilityNotRegisteredRule(GuardrailRuleEvaluator):
    """Rule GR-006: Block task types that have no execution provider registered."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-006",
            description="Block actions whose task type has no registered execution provider",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        tt = context.task_type or context.action
        if not execution_registry.is_executable(tt):
            return False, f"No execution provider registered for task type '{tt}'"
        return True, None


class UnsupportedTaskTypeRule(GuardrailRuleEvaluator):
    """Rule GR-007: Block unrecognized/disallowed task types."""

    KNOWN_TASK_TYPES = {
        "enrich_company",
        "enrich_person",
        "enrich_lead",
        "qualify_lead",
        "research_prospect",
        "draft_outreach",
        "send_outreach_simulation",
        "book_meeting_simulation",
        "crm_update",
    }

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-007",
            description="Block unrecognized or unsupported task types",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if context.action in ("wait", "no_action"):
            return True, None

        tt = context.task_type or context.action
        if tt not in self.KNOWN_TASK_TYPES and not execution_registry.is_executable(tt):
            return False, f"Task type '{tt}' is unsupported by the GTM policy engine"
        return True, None


class MissingRequiredCRMEntityRule(GuardrailRuleEvaluator):
    """Rule GR-008: Block actions when mandatory CRM entities (Lead, Person, Company) are missing."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-008",
            description="Block actions missing necessary CRM entities",
            severity=GuardrailSeverity.BLOCK,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        if not context.lead_id:
            return False, "Action requires an associated lead_id"

        tt = context.task_type or context.action
        if tt == "enrich_company" and not context.has_company:
            return False, "Cannot execute enrich_company: associated Company entity is missing"

        if tt == "enrich_person" and not context.has_person:
            return False, "Cannot execute enrich_person: associated Person entity is missing"

        return True, None


class SafeValidInternalExecutionRule(GuardrailRuleEvaluator):
    """Rule GR-009: Authorize valid safe internal actions."""

    @property
    def rule(self) -> GuardrailRule:
        return GuardrailRule(
            rule_id="GR-009",
            description="Authorize verified safe internal GTM actions",
            severity=GuardrailSeverity.WARN,
            enabled=True,
        )

    async def evaluate(
        self, context: GuardrailContext, session: Optional[AsyncSession] = None
    ) -> tuple[bool, Optional[str]]:
        # This rule checks that valid internal actions pass safely
        return True, None
