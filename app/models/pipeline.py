"""
Campaign pipeline models: campaigns and their target accounts, follow-up
sequences, meetings, drafts, users/roles and the demo clock.

Every table is tenant-scoped (``tenant_id``) and protected by PostgreSQL
row-level security (migration 0012): an API request only sees its own
organisation's rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


ROLES = ("admin", "operator", "approver")
CAMPAIGN_STATUSES = ("draft", "active", "paused", "stopped")
FOLLOW_UP_STATUSES = ("scheduled", "due", "drafted", "sent", "cancelled", "skipped")
MEETING_STATUSES = ("requested", "pending_approval", "invited", "booked", "declined", "reschedule_requested",
                    "cancelled")
DRAFT_STATUSES = ("draft", "submitted", "approved", "rejected", "sent", "superseded", "escalated")


class AppUser(TenantScoped, Base):
    """A person who signs in. Role decides what they may do; tenant decides what they may see."""

    __tablename__ = "app_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)          # admin | operator | approver
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True)   # sha256 hex; never the key
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Campaign(TenantScoped, Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # ICP rules: industries, employee range, countries, technologies, titles/departments, min score, weights
    icp: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    offer: Mapped[Optional[str]] = mapped_column(Text)                   # product / service context for drafting
    value_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # [{"step": 1, "delay_days": 0, "angle": "..."}, {"step": 2, "delay_days": 3, ...}]
    sequence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[Optional[str]] = mapped_column(String(128))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class CampaignAccount(TenantScoped, Base):
    """A target account selected for a campaign, and how its lead discovery went."""

    __tablename__ = "campaign_accounts"
    __table_args__ = (Index("uq_campaign_accounts", "campaign_id", "company_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"),
                                                   nullable=False, index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"),
                                                  nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")   # high | medium | low
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    discovery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    discovery_source: Mapped[Optional[str]] = mapped_column(String(128))
    prospects_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    added_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class FollowUp(TenantScoped, Base):
    """One step of a lead's outreach sequence. Unique per (lead, step): never scheduled twice."""

    __tablename__ = "follow_ups"
    __table_args__ = (Index("uq_follow_ups_lead_step", "tenant_id", "lead_id", "step", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True),
                                                             ForeignKey("campaigns.id", ondelete="SET NULL"))
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_hours: Mapped[float] = mapped_column(Float, nullable=False)
    angle: Mapped[Optional[str]] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    draft_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    send_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class Meeting(TenantScoped, Base):
    __tablename__ = "meetings"
    __table_args__ = (Index("uq_meetings_uid", "tenant_id", "uid", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True),
                                                             ForeignKey("campaigns.id", ondelete="SET NULL"))
    uid: Mapped[str] = mapped_column(String(255), nullable=False)          # iCalendar UID / provider event id
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    provider: Mapped[Optional[str]] = mapped_column(String(128))
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    proposed_slots: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # ISO start times
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    invite: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)           # subject, body, ics preview
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)          # [{at, status, by, note}]
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class Draft(TenantScoped, Base):
    """A message Oxygen (or a person) wrote: first touch, follow-up or reply. Versioned per lead and kind."""

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True),
                                                             ForeignKey("campaigns.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="initial")   # initial | follow_up | reply
    step: Mapped[Optional[int]] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[Optional[str]] = mapped_column(Text)          # why it is personalised this way
    strategy: Mapped[Optional[str]] = mapped_column(Text)             # suggested messaging strategy
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # [{ref, kind, summary}]
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    generated_by: Mapped[Optional[str]] = mapped_column(String(128))   # provider/model, or a person's name
    source_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    send_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    reply_to_interaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    edited_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class DemoClock(TenantScoped, Base):
    """How far the presenter has advanced this organisation's demo clock (for display and audit)."""

    __tablename__ = "demo_clock"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    offset_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


__all__ = ["AppUser", "Campaign", "CampaignAccount", "FollowUp", "Meeting", "Draft", "DemoClock", "ROLES",
           "CAMPAIGN_STATUSES", "FOLLOW_UP_STATUSES", "MEETING_STATUSES", "DRAFT_STATUSES", "text"]
