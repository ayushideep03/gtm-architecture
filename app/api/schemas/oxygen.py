"""
Pydantic schemas for the Oxygen API.
"""

from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict


class DecisionResponse(BaseModel):
    """Schema for a decision returned by the decision engine."""
    model_config = ConfigDict(from_attributes=True)

    action: str
    reason: str
    target_id: Optional[uuid.UUID] = None
    target_type: Optional[str] = None
    confidence: Optional[float] = None
    capability: Optional[str] = None
    parameters: dict[str, Any] = {}
    source_event_ids: list[uuid.UUID] = []
    # Sales Controller proposal
    engine: str = "rules"
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    channel: Optional[str] = None
    situation_summary: Optional[str] = None
    missing_information: list[str] = []
    interest_signals: list[str] = []
    worth_pursuing: Optional[bool] = None
    reasoning: Optional[str] = None
    evidence_refs: list[str] = []
    expected_outcome: Optional[str] = None
    uncertainty: Optional[str] = None
    risks: list[str] = []
    alternatives: list[dict[str, Any]] = []
    wait_hours: Optional[float] = None
    fallback_reason: Optional[str] = None
    llm_meta: dict[str, Any] = {}


class OrchestrateResponse(BaseModel):
    """Schema for the full outcome of an orchestration run."""
    model_config = ConfigDict(from_attributes=True)

    lead_id: uuid.UUID
    decision: DecisionResponse
    task_id: Optional[uuid.UUID] = None
    task_created: bool = False
    event_id: Optional[uuid.UUID] = None
    status: str
    error: Optional[str] = None
    decision_record_id: Optional[uuid.UUID] = None
    approval_id: Optional[uuid.UUID] = None
    requires_approval: bool = False


class CapabilityResponse(BaseModel):
    """Schema describing an individual capability."""
    name: str
    description: str
    input_contract: dict[str, Any] = {}
    is_available: bool = True


class CapabilityListResponse(BaseModel):
    """Schema for the list of registered capabilities."""
    capabilities: list[CapabilityResponse]
