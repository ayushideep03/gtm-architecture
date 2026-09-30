"""
Pydantic schemas for the GTM domain entities.

Schemas are separated from ORM models to keep I/O contracts stable
regardless of internal database changes.

Convention:
    <Entity>Base    — shared fields (used for create & read)
    <Entity>Create  — fields accepted on POST (input)
    <Entity>Read    — fields returned by the API (output, includes id + timestamps)
    <Entity>Update  — partial patch schema (all fields optional)
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


# ── Shared config ─────────────────────────────────────────────────────────────

class _GTMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Company ───────────────────────────────────────────────────────────────────

class CompanyBase(_GTMBase):
    name: str
    domain: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    country: Optional[str] = None
    city: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None


class CompanyCreate(CompanyBase):
    pass


class CompanyRead(CompanyBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CompanyUpdate(_GTMBase):
    name: Optional[str] = None
    domain: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    country: Optional[str] = None
    city: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None


# ── Person ────────────────────────────────────────────────────────────────────

class PersonBase(_GTMBase):
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    company_id: Optional[uuid.UUID] = None


class PersonCreate(PersonBase):
    pass


class PersonRead(PersonBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PersonUpdate(_GTMBase):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    company_id: Optional[uuid.UUID] = None


# ── Lead ──────────────────────────────────────────────────────────────────────

VALID_LEAD_STATUSES = {
    "new", "enriching", "qualified", "disqualified",
    "contacted", "replied", "meeting_booked", "closed_won", "closed_lost",
}


class LeadBase(_GTMBase):
    person_id: Optional[uuid.UUID] = None
    source: Optional[str] = None
    status: str = "new"
    score: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in VALID_LEAD_STATUSES:
            raise ValueError(f"Invalid status: {v!r}. Must be one of {VALID_LEAD_STATUSES}")
        return v


class LeadCreate(LeadBase):
    pass


class LeadRead(LeadBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class LeadUpdate(_GTMBase):
    status: Optional[str] = None
    score: Optional[int] = None
    notes: Optional[str] = None
    source: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_LEAD_STATUSES:
            raise ValueError(f"Invalid status: {v!r}")
        return v


# ── Interaction ───────────────────────────────────────────────────────────────

class InteractionBase(_GTMBase):
    lead_id: uuid.UUID
    interaction_type: str
    direction: str = "outbound"
    subject: Optional[str] = None
    body: Optional[str] = None
    channel_message_id: Optional[str] = None
    occurred_at: Optional[datetime] = None


class InteractionCreate(InteractionBase):
    pass


class InteractionRead(InteractionBase):
    id: uuid.UUID
    occurred_at: datetime
    created_at: datetime


# ── Task ──────────────────────────────────────────────────────────────────────

class TaskBase(_GTMBase):
    lead_id: Optional[uuid.UUID] = None
    task_type: str
    status: str = "pending"
    priority: int = 5
    payload: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskCreate(TaskBase):
    pass


class TaskRead(TaskBase):
    id: uuid.UUID
    result: Optional[str] = None
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# ── Event ─────────────────────────────────────────────────────────────────────

class EventBase(_GTMBase):
    lead_id: Optional[uuid.UUID] = None
    event_type: str
    payload: Optional[str] = None
    source: Optional[str] = None


class EventCreate(EventBase):
    pass


class EventRead(EventBase):
    id: uuid.UUID
    occurred_at: datetime
