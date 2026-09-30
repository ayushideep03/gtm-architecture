"""Schemas for the Evals API."""

from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.evals.models import EvalCase, EvalResult, EvalRun


class EvalCaseListResponse(BaseModel):
    """Schema for listing eval test cases."""

    count: int
    cases: list[EvalCase]


class EvalRunResponse(BaseModel):
    """Schema for an eval test run response."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[EvalResult]
