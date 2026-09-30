"""
Domain models — core GTM entities.

Hierarchy:
    Company
      └── Person  (many per company)
           └── Lead  (one per person, or standalone)
                ├── Interaction  (email, call, LinkedIn, etc.)
                ├── Task         (follow-up, review, enrichment, etc.)
                └── Event        (state-change signals for the orchestrator)

Design notes:
- UUIDs are used as primary keys for distributed-safe IDs.
- created_at / updated_at are set automatically.
- All FKs are nullable where the relationship is optional (e.g. a Lead
  may exist without a Company if the company is not yet identified).
- Keep columns minimal — later migrations will add domain-specific fields.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ── Helpers ───────────────────────────────────────────────────────────────────


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enumerations ──────────────────────────────────────────────────────────────

LEAD_STATUS_VALUES = (
    "new",
    "enriching",
    "qualified",
    "disqualified",
    "contacted",
    "replied",
    "meeting_booked",
    "closed_won",
    "closed_lost",
)

INTERACTION_TYPE_VALUES = (
    "email_sent",
    "email_received",
    "call",
    "linkedin_message",
    "linkedin_connection",
    "meeting",
    "note",
)

TASK_STATUS_VALUES = ("pending", "in_progress", "done", "failed", "skipped")

EVENT_TYPE_VALUES = (
    "lead_created",
    "lead_enriched",
    "lead_ingested",
    "lead_qualified",
    "lead_disqualified",
    "email_sent",
    "reply_received",
    "meeting_booked",
    "task_created",
    "task_completed",
    "company_enriched",
    "person_enriched",
    "enrichment_completed",
    "enrichment_failed",
    "oxygen_decision",
    "oxygen_task_created",
    "oxygen_waiting",
    "oxygen_no_action",
)


# ── Company ───────────────────────────────────────────────────────────────────


class Company(Base):
    """A target account / organisation."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Enrichment fields
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    technologies: Mapped[str | None] = mapped_column(Text, nullable=True)
    funding_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revenue_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrichment_source: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    people: Mapped[list["Person"]] = relationship(
        "Person", back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("domain", name="uq_companies_domain"),)

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


# ── Person ────────────────────────────────────────────────────────────────────


class Person(Base):
    """A human contact — employee of a Company."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Enrichment fields
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrichment_source: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    company: Mapped["Company | None"] = relationship("Company", back_populates="people")
    leads: Mapped[list["Lead"]] = relationship(
        "Lead", back_populates="person", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("email", name="uq_people_email"),)

    def __repr__(self) -> str:
        return f"<Person id={self.id} email={self.email!r}>"


# ── Lead ──────────────────────────────────────────────────────────────────────


class Lead(Base):
    """
    A qualified sales opportunity tied to a Person (and indirectly a Company).

    The Lead is the central entity that the Oxygen orchestrator acts on.
    """

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # e.g. "apollo", "linkedin", "manual"
    status: Mapped[str] = mapped_column(
        Enum(*LEAD_STATUS_VALUES, name="lead_status"),
        nullable=False,
        default="new",
        index=True,
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    person: Mapped["Person | None"] = relationship("Person", back_populates="leads")
    interactions: Mapped[list["Interaction"]] = relationship(
        "Interaction", back_populates="lead", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="lead", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        "Event", back_populates="lead", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} status={self.status!r}>"


# ── Interaction ───────────────────────────────────────────────────────────────


class Interaction(Base):
    """
    A recorded touchpoint between the system and a prospect.

    Examples: email sent, reply received, LinkedIn DM, call log.
    """

    __tablename__ = "interactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    interaction_type: Mapped[str] = mapped_column(
        Enum(*INTERACTION_TYPE_VALUES, name="interaction_type"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(
        Enum("inbound", "outbound", name="interaction_direction"),
        nullable=False,
        default="outbound",
    )
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_message_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )  # external ID (e.g. email message-id, LinkedIn urn)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead", back_populates="interactions")

    def __repr__(self) -> str:
        return f"<Interaction id={self.id} type={self.interaction_type!r}>"


# ── Task ──────────────────────────────────────────────────────────────────────


class Task(Base):
    """
    A unit of work assigned to an agent or human.

    The Oxygen orchestrator creates Tasks; workers execute them.
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(
        String(128), nullable=False
    )  # e.g. "send_email", "enrich_lead", "qualify_lead"
    status: Mapped[str] = mapped_column(
        Enum(*TASK_STATUS_VALUES, name="task_status"),
        nullable=False,
        default="pending",
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    payload: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON-encoded task-specific parameters
    result: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON-encoded result after execution
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    lead: Mapped["Lead | None"] = relationship("Lead", back_populates="tasks")

    def __repr__(self) -> str:
        return f"<Task id={self.id} type={self.task_type!r} status={self.status!r}>"


# ── Event ─────────────────────────────────────────────────────────────────────


class Event(Base):
    """
    An immutable domain event (append-only log).

    Events are the primary signal the Oxygen orchestrator listens to
    for deciding what to do next. They are never updated — only appended.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(
        Enum(*EVENT_TYPE_VALUES, name="event_type"),
        nullable=False,
        index=True,
    )
    payload: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON-encoded event payload
    source: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # which system generated this event

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    lead: Mapped["Lead | None"] = relationship("Lead", back_populates="events")

    def __repr__(self) -> str:
        return f"<Event id={self.id} type={self.event_type!r}>"
