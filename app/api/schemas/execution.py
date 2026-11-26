"""Pydantic schemas for the Execution API."""

from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict


class TaskDetailResponse(BaseModel):
    """Schema for a Task entity returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: Optional[uuid.UUID] = None
    task_type: str
    status: str
    priority: int
    payload: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    attempt_count: int = 0
    max_attempts: Optional[int] = None
    next_attempt_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    needs_review: bool = False
    idempotency_key: Optional[str] = None


class ExecutionResponse(BaseModel):
    """Schema for the outcome of task execution."""

    model_config = ConfigDict(from_attributes=True)

    task_id: uuid.UUID
    success: bool
    task_status: str
    result: dict[str, Any] = {}
    event_type: str
    error_message: Optional[str] = None
    already_executed: bool = False


# Alias for backwards compatibility / routes
ExecutionOutcomeResponse = ExecutionResponse


class ExecutionProviderResponse(BaseModel):
    """Schema describing an individual registered execution provider."""

    provider_name: str
    supported_task_types: list[str]          # canonical capability names
    available: bool = True
    unavailable_reason: Optional[str] = None
    enabled: bool = True
    mode: str = "live"
    priority: int = 50
    cost_per_call: float = 0.0
    tags: list[str] = []


class ExecutionProviderListResponse(BaseModel):
    """Schema for the list of registered execution providers."""

    count: int
    providers: list[ExecutionProviderResponse]


class TaskRetryRequest(BaseModel):
    """Explicit, audited retry of a failed task."""

    requested_by: str
    reason: str = ""
    acknowledge_review: bool = False   # required when the task is flagged needs_review
    extra_attempts: Optional[int] = None


class StaleRecoveryResponse(BaseModel):
    resumed: list[uuid.UUID]
    flagged_for_review: list[uuid.UUID]
