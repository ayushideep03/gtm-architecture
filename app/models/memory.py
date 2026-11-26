"""
Persistent sales memory and compliance models.

These extend the existing CRM (companies, people, leads, interactions, tasks,
events) — they do not duplicate it:

  facts / fact_evidence   evidence ledger: every important value with source,
                          timestamp, confidence and lifecycle
  research_findings       synthesised research: briefs, intent signals, news,
                          objections (with citations and freshness)
  agent_decisions         every action proposal Oxygen made, with reasoning,
                          evidence, confidence, risks and outcome
  work_items              when Oxygen should look at a lead again (evaluations,
                          scheduled rechecks); tasks remain the executable actions
  suppressions            do-not-contact records (email / domain / person / company)
  contact_consents        lawful basis for contacting a person
  approvals               human approval of external actions, bound to an exact
                          payload hash

All tables are tenant-scoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantScoped, _now, _uuid

FACT_STATUSES = ("proposed", "applied", "superseded", "dismissed")
FACT_BANDS = ("verified", "probable", "possible")
ENTITY_TYPES = ("company", "person", "lead")


class Fact(TenantScoped, Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_entity_field", "tenant_id", "entity_type", "entity_id", "field", "status"),
        Index("uq_facts_applied", "tenant_id", "entity_type", "entity_id", "field", unique=True,
              postgresql_where=text("status = 'applied'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[Optional[str]] = mapped_column(String(128))          # e.g. provider name, "ingestion"
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    source_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("facts.id", ondelete="SET NULL"))
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class FactEvidence(TenantScoped, Base):
    __tablename__ = "fact_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)       # snapshot of the configured weight
    primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_ref: Mapped[Optional[str]] = mapped_column(String(255))     # e.g. interaction / task / provider record id
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ResearchFinding(TenantScoped, Base):
    __tablename__ = "research_findings"
    __table_args__ = (Index("ix_findings_lead_kind", "tenant_id", "lead_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)       # brief | intent_signal | news | objection | ...
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[Optional[dict]] = mapped_column(JSONB)
    citations: Mapped[Optional[list]] = mapped_column(JSONB)            # [{url, title, quote}]
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(128))          # provider / model
    source_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    stale_after: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AgentDecision(TenantScoped, Base):
    """One action proposal from Oxygen (rules or LLM) and what became of it."""

    __tablename__ = "agent_decisions"
    __table_args__ = (Index("ix_agent_decisions_lead", "tenant_id", "lead_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)     # rules | llm
    model: Mapped[Optional[str]] = mapped_column(String(128))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[Optional[str]] = mapped_column(String(128))
    channel: Mapped[Optional[str]] = mapped_column(String(64))
    parameters: Mapped[Optional[dict]] = mapped_column(JSONB)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    evidence_refs: Mapped[Optional[list]] = mapped_column(JSONB)        # fact / finding / interaction ids
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    uncertainty: Mapped[Optional[str]] = mapped_column(Text)
    risks: Mapped[Optional[list]] = mapped_column(JSONB)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guardrail_result: Mapped[Optional[dict]] = mapped_column(JSONB)
    # proposed | blocked | awaiting_approval | approved | rejected | executed | failed | no_action | waiting
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)     # what the engine saw (for replay / audit)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class WorkItem(TenantScoped, Base):
    """Agent attention queue: evaluate / recheck a lead at a time, leased by workers."""

    __tablename__ = "work_items"
    __table_args__ = (
        Index("ix_work_items_due", "due_at", "leased_until", postgresql_where=text("finished_at IS NULL")),
        Index("uq_work_items_open", "tenant_id", "kind", "lead_id", unique=True,
              postgresql_where=text("finished_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)        # evaluate | recheck | ...
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    leased_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    leased_by: Mapped[Optional[str]] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Suppression(TenantScoped, Base):
    __tablename__ = "suppressions"
    __table_args__ = (
        Index("uq_suppressions_active", "tenant_id", "scope", "value", unique=True,
              postgresql_where=text("lifted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)       # email | domain | person | company
    value: Mapped[str] = mapped_column(String(320), nullable=False)      # normalised email/domain or entity id
    reason: Mapped[str] = mapped_column(String(64), nullable=False)      # unsubscribe | hard_bounce | complaint | manual | legal
    source: Mapped[Optional[str]] = mapped_column(String(128))
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lifted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lifted_by: Mapped[Optional[str]] = mapped_column(String(255))


class ContactConsent(TenantScoped, Base):
    __tablename__ = "contact_consents"
    __table_args__ = (Index("ix_contact_consents_person", "tenant_id", "person_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    basis: Mapped[str] = mapped_column(String(64), nullable=False)       # consent | legitimate_interest | contract | none
    channel: Mapped[Optional[str]] = mapped_column(String(32))           # None = all channels
    source: Mapped[str] = mapped_column(String(128), nullable=False)     # e.g. "default_policy", "web_form", user id
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Approval(TenantScoped, Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, nullable=False)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_decisions.id", ondelete="SET NULL"))
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)  # pending|approved|rejected|expired
    original_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    final_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))      # sha256 of the approved payload
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)               # why Oxygen proposed it
    evidence: Mapped[Optional[list]] = mapped_column(JSONB)
    reviewer_note: Mapped[Optional[str]] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[Optional[str]] = mapped_column(String(255))


class OutcomeAttribution(TenantScoped, Base):
    """One outbound touch and what happened next (RevOps + learning source of truth)."""

    __tablename__ = "outcome_attributions"
    __table_args__ = (Index("ix_outcome_attr_lead", "tenant_id", "lead_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    touch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="CASCADE"),
                                                unique=True, nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    channel: Mapped[Optional[str]] = mapped_column(String(32))
    touch_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(128))       # sending provider
    draft_provider: Mapped[Optional[str]] = mapped_column(String(128))
    engine: Mapped[Optional[str]] = mapped_column(String(32))           # llm | rules | rules_fallback
    angle: Mapped[Optional[str]] = mapped_column(Text)
    personalisation_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    research_before_touch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    segment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # industry, size band, seniority
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    outcome_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    outcome_detail: Mapped[Optional[dict]] = mapped_column(JSONB)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # window closed
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
