"""Guardrails & Policy Enforcement package."""

from app.guardrails.evaluator import GuardrailEvaluator
from app.guardrails.interfaces import GuardrailRuleEvaluator
from app.guardrails.models import (
    GuardrailContext,
    GuardrailResult,
    GuardrailRule,
    GuardrailSeverity,
)
from app.guardrails.registry import GuardrailRegistry, guardrail_registry
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

__all__ = [
    "GuardrailContext",
    "GuardrailResult",
    "GuardrailRule",
    "GuardrailSeverity",
    "GuardrailRuleEvaluator",
    "GuardrailRegistry",
    "guardrail_registry",
    "GuardrailEvaluator",
    "UnknownCapabilityRule",
    "MissingTargetRule",
    "MissingRequiredTaskPayloadRule",
    "DuplicateActiveTaskRule",
    "TaskAlreadyCompletedRule",
    "ExecutionCapabilityNotRegisteredRule",
    "UnsupportedTaskTypeRule",
    "MissingRequiredCRMEntityRule",
    "SafeValidInternalExecutionRule",
]
