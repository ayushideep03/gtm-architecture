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
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship, validates

from app.core.database import Base
from app.core.tenancy import DEFAULT_TENANT_ID
from app.events.types import CORE_EVENT_TYPES, validate_event_type

# ── Helpers ───────────────────────────────────────────────────────────────────


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TenantScoped:
    """
    Mixin adding ``tenant_id`` (FK organizations.id). Rows created without an
    explicit tenant belong to the default organisation; ``None`` is coerced to
    the default so the NOT NULL constraint always holds.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
            default=DEFAULT_TENANT_ID,
            server_default=str(DEFAULT_TENANT_ID),
        )

    @validates("tenant_id")
    def _default_tenant(self, key, value):
        return value or DEFAULT_TENANT_ID


# ── Organization (tenant) ─────────────────────────────────────────────────────


class Organization(Base):
    """A tenant. Every tenant-owned row references one organisation."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug!r}>"


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

TASK_STATUS_VALUES = (
    "pending",
    "in_progress",
    "done",
    "completed",
    "failed",
    "skipped",
    "awaiting_approval",   # external action waiting for a human decision
    "cancelled",           # approval rejected / expired, or withdrawn
)

# Kept for backwards compatibility; the live set is app.events.types (extensible).
EVENT_TYPE_VALUES = CORE_EVENT_TYPES


# ── Company ───────────────────────────────────────────────────────────────────


class Company(TenantScoped, Base):
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

    __table_args__ = (
        Index("uq_companies_tenant_domain", "tenant_id", "domain", unique=True,
              postgresql_where=text("domain IS NOT NULL")),
        Index("ix_companies_tenant_linkedin", "tenant_id", "linkedin_url",
              postgresql_where=text("linkedin_url IS NOT NULL")),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


# ── Person ────────────────────────────────────────────────────────────────────


class Person(TenantScoped, Base):
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

    __table_args__ = (
        Index("uq_people_tenant_email", "tenant_id", "email", unique=True,
              postgresql_where=text("email IS NOT NULL")),
        Index("ix_people_tenant_linkedin", "tenant_id", "linkedin_url",
              postgresql_where=text("linkedin_url IS NOT NULL")),
    )

    def __repr__(self) -> str:
        return f"<Person id={self.id} email={self.email!r}>"


# ── Lead ──────────────────────────────────────────────────────────────────────


class Lead(TenantScoped, Base):
    """
    A qualified sales opportunity tied to a Person (and indirectly a Company).

    The Lead is the central entity that the Oxygen orchestrator acts on.
    """

    __tablename__ = "leads"
    __table_args__ = (
        Index("uq_leads_open_person", "tenant_id", "person_id", unique=True,
              postgresql_where=text("is_open AND person_id IS NOT NULL")),
    )

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
    # True while the lead is not in a terminal status (terminal statuses come from
    # config: identity.lead.terminal_statuses). A partial unique index on
    # (tenant_id, person_id) WHERE is_open guarantees one open lead per person.
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    @validates("status")
    def _track_open(self, key, value: str) -> str:
        from app.core.gtm_config import get_gtm_config
        terminal = get_gtm_config().for_tenant(self.tenant_id).identity.lead.terminal_statuses
        self.is_open = value not in terminal
        return value

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


class Interaction(TenantScoped, Base):
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
    # Memory links: which task produced it, which channel/provider, structured details
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead", back_populates="interactions")

    def __repr__(self) -> str:
        return f"<Interaction id={self.id} type={self.interaction_type!r}>"


# ── Task ──────────────────────────────────────────────────────────────────────


class Task(TenantScoped, Base):
    """
    A unit of work assigned to an agent or human.

    The Oxygen orchestrator creates Tasks; workers execute them.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("uq_tasks_tenant_idempotency", "tenant_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

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

    # ── Reliability / recovery ───────────────────────────────────────────────
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = config default
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when an external action's outcome is unknown; a human must reconcile before any retry.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Unique per tenant (partial index) for capabilities that declare idempotency fields.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

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


class Event(TenantScoped, Base):
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
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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

    @validates("event_type")
    def _check_event_type(self, key, value: str) -> str:
        return validate_event_type(value)

    def __repr__(self) -> str:
        return f"<Event id={self.id} type={self.event_type!r}>"


# ── Source records (lead-source identity mapping) ────────────────────────────


class SourceRecord(TenantScoped, Base):
    """
    Maps a provider's own record id (source, external_id) to our entities, so a
    re-import of the same record resolves to the same company / person / lead
    even if its email or domain changed at the source.
    """

    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_source_records_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ── Event Processing State (RevOps durable cursor) ───────────────────────────


class EventProcessingState(Base):
    """Tracks consumer cursor and processed count for durable RevOps event stream processing."""

    __tablename__ = "event_processing_states"

    consumer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_processed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    last_processed_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<EventProcessingState consumer={self.consumer_name!r} count={self.processed_count}>"

