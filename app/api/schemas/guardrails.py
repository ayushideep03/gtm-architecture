"""Schemas for Guardrails API."""

from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class GuardrailRuleResponse(BaseModel):
    """Schema describing an individual guardrail rule."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    description: str
    severity: str
    enabled: bool


class GuardrailRuleListResponse(BaseModel):
    """Schema for listing registered guardrail rules."""

    count: int
    rules: list[GuardrailRuleResponse]


class GuardrailEvaluateRequest(BaseModel):
    """Diagnostic request schema to evaluate guardrail policies."""

    lead_id: Optional[uuid.UUID] = None
    person_id: Optional[uuid.UUID] = None
    company_id: Optional[uuid.UUID] = None
    action: str
    capability: Optional[str] = None
    task_type: Optional[str] = None
    target_id: Optional[uuid.UUID] = None
    target_type: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    has_company: bool = True
    has_person: bool = True
    is_company_enriched: bool = False
    is_person_enriched: bool = False


class GuardrailEvaluateResponse(BaseModel):
    """Schema for the outcome of guardrail policy evaluation."""

    allowed: bool
    decision: str
    reason: str
    rule_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
