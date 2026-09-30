"""Domain models for the GTM Evaluation Framework."""

from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class EvalCase(BaseModel):
    """Specification of an individual evaluation test case."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    name: str
    description: str
    input_context: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    """Result of running an individual evaluation test case."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    passed: bool
    actual: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalRun(BaseModel):
    """Execution summary and results for a complete evaluation suite run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    results: list[EvalResult] = Field(default_factory=list)
