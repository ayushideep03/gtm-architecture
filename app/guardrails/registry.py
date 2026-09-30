"""Registry for Guardrail rules and policy evaluation."""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.guardrails.interfaces import GuardrailRuleEvaluator
from app.guardrails.models import (
    GuardrailContext,
    GuardrailResult,
    GuardrailRule,
    GuardrailSeverity,
)
from app.guardrails.rules import (
    DuplicateActiveTaskRule,
    ExecutionCapabilityNotRegisteredRule,
    MissingRequiredCRMEntityRule,
    MissingRequiredTaskPayloadRule,
    MissingTargetRule,
    SafeValidInternalExecutionRule,
    TaskAlreadyCompletedRule,
    UnknownCapabilityRule,
    UnsupportedTaskTypeRule,
)

logger = get_logger(__name__)


class GuardrailRegistry:
    """Registry coordinating guardrail rules and aggregate evaluation."""

    def __init__(self) -> None:
        self._evaluators: dict[str, GuardrailRuleEvaluator] = {}

    def register(self, evaluator: GuardrailRuleEvaluator) -> None:
        """Register a guardrail rule evaluator."""
        self._evaluators[evaluator.rule.rule_id] = evaluator
        logger.info(
            "Registered guardrail rule [%s]: %s",
            evaluator.rule.rule_id,
            evaluator.rule.description,
        )

    def get_evaluator(self, rule_id: str) -> Optional[GuardrailRuleEvaluator]:
        """Retrieve a rule evaluator by rule ID."""
        return self._evaluators.get(rule_id)

    def list_rules(self) -> list[GuardrailRule]:
        """List all registered guardrail rules sorted by ID."""
        return [
            e.rule
            for e in sorted(self._evaluators.values(), key=lambda x: x.rule.rule_id)
        ]

    async def evaluate_all(
        self,
        context: GuardrailContext,
        session: Optional[AsyncSession] = None,
    ) -> GuardrailResult:
        """Evaluate context across all registered and enabled rules.

        Fails closed: if any BLOCK rule fails, allowed is False.
        """
        evaluated_rule_ids: list[str] = []
        warnings: list[str] = []

        for evaluator in self._evaluators.values():
            rule = evaluator.rule
            if not rule.enabled:
                continue

            evaluated_rule_ids.append(rule.rule_id)
            try:
                allowed, reason = await evaluator.evaluate(context, session=session)
            except Exception as exc:
                logger.exception("Guardrail rule %s threw unhandled exception: %s", rule.rule_id, exc)
                return GuardrailResult(
                    allowed=False,
                    decision=context.action,
                    reason=f"Guardrail evaluation error in rule {rule.rule_id}: {exc}",
                    rule_ids=[rule.rule_id],
                )

            if not allowed:
                if rule.severity == GuardrailSeverity.BLOCK:
                    logger.warning(
                        "Guardrail BLOCKED action '%s' by rule [%s]: %s",
                        context.action,
                        rule.rule_id,
                        reason,
                    )
                    return GuardrailResult(
                        allowed=False,
                        decision=context.action,
                        reason=reason or f"Blocked by guardrail rule {rule.rule_id}",
                        rule_ids=[rule.rule_id],
                        warnings=warnings,
                    )
                else:
                    warnings.append(f"[{rule.rule_id}] {reason}")

        return GuardrailResult(
            allowed=True,
            decision=context.action,
            reason="All guardrail policy checks passed",
            rule_ids=evaluated_rule_ids,
            warnings=warnings,
        )


def create_default_guardrail_registry() -> GuardrailRegistry:
    """Create and initialize registry with the 9 core policy rules."""
    registry = GuardrailRegistry()
    registry.register(UnknownCapabilityRule())
    registry.register(MissingTargetRule())
    registry.register(MissingRequiredTaskPayloadRule())
    registry.register(DuplicateActiveTaskRule())
    registry.register(TaskAlreadyCompletedRule())
    registry.register(ExecutionCapabilityNotRegisteredRule())
    registry.register(UnsupportedTaskTypeRule())
    registry.register(MissingRequiredCRMEntityRule())
    registry.register(SafeValidInternalExecutionRule())
    return registry


guardrail_registry = create_default_guardrail_registry()
