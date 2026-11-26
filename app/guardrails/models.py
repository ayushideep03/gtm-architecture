"""Domain models for Guardrails & Policy Enforcement."""

from enum import Enum
from typing import Any, ClassVar, Optional
import uuid
from pydantic import BaseModel, Field


class GuardrailSeverity(str, Enum):
    BLOCK = "block"
    WARN = "warn"


class GuardrailRule(BaseModel):
    """Metadata describing a guardrail policy rule."""

    rule_id: str
    description: str
    severity: GuardrailSeverity = GuardrailSeverity.BLOCK
    enabled: bool = True


class GuardrailContext(BaseModel):
    """Context provided to guardrail rules for policy evaluation."""

    lead_id: Optional[uuid.UUID] = None
    person_id: Optional[uuid.UUID] = None
    company_id: Optional[uuid.UUID] = None
    action: str
    capability: Optional[str] = None
    task_type: Optional[str] = None
    target_id: Optional[uuid.UUID] = None
    target_type: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    task_history: list[dict[str, Any]] = Field(default_factory=list)
    has_company: bool = True
    has_person: bool = True
    is_company_enriched: bool = False
    is_person_enriched: bool = False
    tenant_id: Optional[uuid.UUID] = None
    # Facts about available data, keyed like capability requirements
    # ("company", "person", "person.email", "company.domain", ...).
    entity_facts: dict[str, bool] = Field(default_factory=dict)
    # Deterministic compliance status for the contact (app.compliance.rules.ComplianceStatus.as_dict()).
    # None = unknown -> external actions are blocked (fail closed).
    compliance: Optional[dict[str, Any]] = None

    NON_ACTIONS: ClassVar[frozenset[str]] = frozenset({"wait", "no_action", "stop", "escalate"})

    @property
    def is_actionable(self) -> bool:
        return self.action not in self.NON_ACTIONS

    @property
    def requested_capability(self) -> str:
        """The capability being authorised: explicit capability, else task type, else action."""
        return self.capability or self.task_type or self.action

    def facts(self) -> dict[str, bool]:
        base = {
            "lead": self.lead_id is not None,
            "company": self.has_company,
            "person": self.has_person,
        }
        return {**base, **self.entity_facts}


class GuardrailResult(BaseModel):
    """The aggregate policy decision produced by the guardrail evaluator."""

    allowed: bool
    decision: str
    reason: str
    rule_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
