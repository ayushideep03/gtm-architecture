"""
Capability specification: the vendor-neutral contract for one kind of action.

A capability says WHAT can be done (inputs, outputs, side effects, what data it
needs, how often it may repeat). Providers say HOW (which vendor does it).
Oxygen plans over capabilities; guardrails validate against capability
metadata; the provider selector picks a provider per call. Adding a new
capability or provider therefore needs no changes to Oxygen or guardrails.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

from app.core.gtm_config import CapabilityPolicy, RepeatabilityConfig, SideEffect

# Parameter names that can be filled from the lead's context when the planner
# omits them. Values are attribute paths on the guardrail/oxygen context.
CONTEXT_FILLABLE = {
    "lead_id": "lead_id",
    "company_id": "company_id",
    "person_id": "person_id",
}


@dataclass
class ParameterValidation:
    ok: bool
    parameters: dict[str, Any]
    errors: list[str] = field(default_factory=list)


@dataclass
class CapabilitySpec:
    name: str
    description: str
    category: str
    input_model: type[BaseModel]
    side_effect: SideEffect
    target_type: Optional[str] = None            # "lead" | "company" | "person" | None
    requires: list[str] = field(default_factory=list)   # facts that must hold, e.g. "person.email"
    repeatability: RepeatabilityConfig = field(default_factory=RepeatabilityConfig)
    idempotency_fields: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    output_model: Optional[type[BaseModel]] = None
    # Interfaces a provider may implement to serve this capability, and how to
    # call a non-ExecutionProvider implementation (see app/capabilities/invokers.py).
    provider_interfaces: tuple[type, ...] = ()
    invoker: Optional[Callable[..., Any]] = None

    # ── policy (spec defaults + config overrides) ────────────────────────────
    def with_policy(self, policy: Optional[CapabilityPolicy]) -> "CapabilitySpec":
        if policy is None:
            return self
        clone = CapabilitySpec(**self.__dict__)
        if policy.description:
            clone.description = policy.description
        if policy.repeatability is not None:
            clone.repeatability = policy.repeatability
        if policy.idempotency_fields is not None:
            clone.idempotency_fields = list(policy.idempotency_fields)
        return clone

    # ── schema ───────────────────────────────────────────────────────────────
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def validate_parameters(
        self, parameters: dict[str, Any], context_values: Optional[dict[str, Any]] = None
    ) -> ParameterValidation:
        """Validate (and normalise) planner-supplied parameters against the input model."""
        params = dict(parameters or {})
        fields = self.input_model.model_fields
        for pname, ctx_key in CONTEXT_FILLABLE.items():
            if pname in fields and params.get(pname) in (None, "") and context_values:
                value = context_values.get(ctx_key)
                if value is not None:
                    params[pname] = str(value) if isinstance(value, uuid.UUID) else value
        try:
            model = self.input_model.model_validate(params)
        except ValidationError as exc:
            errors = [
                f"{'.'.join(str(p) for p in err['loc']) or 'parameters'}: {err['msg']}"
                for err in exc.errors()
            ]
            return ParameterValidation(ok=False, parameters=params, errors=errors)
        return ParameterValidation(ok=True, parameters=model.model_dump(mode="json", exclude_none=True))

    def idempotency_key(self, lead_id: Any, parameters: dict[str, Any]) -> Optional[str]:
        """
        Stable key identifying "this exact action" (e.g. sending draft X to lead Y).
        Only capabilities that declare idempotency fields get a key; the database
        enforces uniqueness per tenant so the action can never be recorded twice.
        """
        if not self.idempotency_fields:
            return None
        parts = [self.name, str(lead_id)]
        parts += [f"{f}={parameters.get(f)}" for f in self.idempotency_fields]
        return ":".join(parts)[:255]

    def describe_for_planner(self) -> dict[str, Any]:
        """Compact, LLM-readable description (used by the Phase 5 planner)."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "side_effect": self.side_effect.value,
            "requires": self.requires,
            "input_schema": self.input_schema(),
        }
