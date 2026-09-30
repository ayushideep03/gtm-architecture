"""Pydantic schemas for the RevOps API."""

from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class EventResponse(BaseModel):
    """Schema for individual domain event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: Optional[uuid.UUID] = None
    event_type: str
    source: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    occurred_at: datetime


class EventListResponse(BaseModel):
    """Schema for list of domain events."""

    count: int
    events: list[EventResponse]
