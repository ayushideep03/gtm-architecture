"""Execution domain models and state transitions."""

from enum import Enum
from typing import Any, Optional
import uuid
from pydantic import BaseModel, Field


# ── Task Lifecycle & State Transitions ───────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DONE = "done"  # legacy alias for completed
    FAILED = "failed"
    SKIPPED = "skipped"


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.PENDING.value: {TaskStatus.IN_PROGRESS.value},
    TaskStatus.IN_PROGRESS.value: {
        TaskStatus.COMPLETED.value,
        TaskStatus.DONE.value,
        TaskStatus.FAILED.value,
    },
    TaskStatus.COMPLETED.value: set(),
    TaskStatus.DONE.value: set(),
    TaskStatus.FAILED.value: set(),
    TaskStatus.SKIPPED.value: set(),
}


def validate_transition(current_status: str, target_status: str) -> bool:
    """Validate whether transitioning from current_status to target_status is allowed."""
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    return target_status in allowed


# ── Domain Exceptions ────────────────────────────────────────────────────────

class ExecutionError(Exception):
    """Base exception for execution layer errors."""
    pass


class TaskNotFoundError(ExecutionError):
    """Raised when a task cannot be found."""
    pass


class InvalidStateTransitionError(ExecutionError):
    """Raised when an illegal task state transition is attempted."""
    pass


class UnknownTaskTypeError(ExecutionError):
    """Raised when a task type is not mapped to any provider."""
    pass


class ProviderNotFoundError(ExecutionError):
    """Raised when an explicit provider cannot be resolved."""
    pass


class ConcurrentExecutionError(ExecutionError):
    """Raised when an attempt is made to execute a task that is already running."""
    pass


# ── Execution Context & Results ──────────────────────────────────────────────

class ExecutionContext(BaseModel):
    """Context and input parameters passed to an execution provider."""

    task_id: uuid.UUID
    task_type: str
    lead_id: Optional[uuid.UUID] = None
    target_id: Optional[uuid.UUID] = None
    capability: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Standardized result returned by an execution provider."""

    success: bool
    status: str = "completed"
    provider: str
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionOutcome(BaseModel):
    """Final outcome produced by the TaskExecutor after persisting state and events."""

    task_id: uuid.UUID
    success: bool
    task_status: str
    result: dict[str, Any] = Field(default_factory=dict)
    event_type: str
    error_message: Optional[str] = None
    already_executed: bool = False
