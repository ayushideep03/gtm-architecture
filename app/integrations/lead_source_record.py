"""
Normalized lead source record schema.

Every provider (Apollo, Scout, web search, manual CSV) produces data in its
own format. Before that data reaches the ingestion service, it MUST be
normalized into a LeadSourceRecord.

Design decisions:
- All fields are Optional except source and collected_at, because real
  providers don't always supply every field.
- raw_data preserves the original provider payload for traceability.
  We never discard what a provider told us.
- collected_at is always set by the provider adapter, not the consumer.
- external_id is the provider's own ID for this record. It allows us to
  detect re-ingestion of the same provider record in later steps.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class LeadSourceRecord(BaseModel):
    """
    Canonical, provider-agnostic representation of a lead from any source.

    The ingestion service reads this model — not provider-specific objects.
    """

    model_config = ConfigDict(extra="ignore")

    # ── Provenance ─────────────────────────────────────────────────────────────
    source: str  # e.g. "mock_apollo", "scout", "manual"
    external_id: Optional[str] = None  # provider's own ID for this record
    source_url: Optional[str] = None  # URL the record was found at

    # ── Person fields ──────────────────────────────────────────────────────────
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None  # some providers only give full name
    email: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    linkedin_url: Optional[str] = None

    # ── Company fields ─────────────────────────────────────────────────────────
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    company_linkedin_url: Optional[str] = None
    company_website: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None

    # ── Location ───────────────────────────────────────────────────────────────
    location: Optional[str] = None  # free-text city/country string

    # ── Raw provenance ─────────────────────────────────────────────────────────
    raw_data: Optional[dict[str, Any]] = None  # original provider payload
    collected_at: Optional[datetime] = None

    @model_validator(mode="after")
    def set_collected_at(self) -> "LeadSourceRecord":
        if self.collected_at is None:
            self.collected_at = datetime.now(timezone.utc)
        return self

    def has_email(self) -> bool:
        return bool(self.email and self.email.strip())

    def has_company_domain(self) -> bool:
        return bool(self.company_domain and self.company_domain.strip())

    def effective_first_name(self) -> str:
        """Best-effort first name extraction."""
        if self.first_name:
            return self.first_name.strip()
        if self.full_name:
            parts = self.full_name.strip().split()
            return parts[0] if parts else "Unknown"
        return "Unknown"

    def effective_last_name(self) -> Optional[str]:
        """Best-effort last name extraction."""
        if self.last_name:
            return self.last_name.strip()
        if self.full_name:
            parts = self.full_name.strip().split()
            return " ".join(parts[1:]) if len(parts) > 1 else None
        return None


class LeadSearchCriteria(BaseModel):
    """
    Flexible search parameters sent to a provider.

    Providers implement whatever subset of these fields they support.
    Unknown fields are ignored. This keeps the interface stable as we add
    providers with different capabilities.
    """

    model_config = ConfigDict(extra="ignore")

    # ── Common search parameters ──────────────────────────────────────────────
    company_domain: Optional[str] = None
    company_name: Optional[str] = None
    job_titles: Optional[list[str]] = None
    location: Optional[str] = None
    keywords: Optional[list[str]] = None
    limit: int = 10  # max records to return

    # ── Provider-specific pass-through ────────────────────────────────────────
    # Extra fields the caller wants to pass to a specific provider.
    # Must NOT be used by the ingestion service directly.
    extra: Optional[dict[str, Any]] = None
