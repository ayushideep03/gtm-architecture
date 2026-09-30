"""
Request and response schemas for the enrichment API.
"""

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EnrichCompanyRequest(BaseModel):
    """Optional request body for company enrichment."""
    model_config = ConfigDict(extra="ignore")

    provider: str = "mock_enrichment"


class EnrichPersonRequest(BaseModel):
    """Optional request body for person enrichment."""
    model_config = ConfigDict(extra="ignore")

    provider: str = "mock_enrichment"


class EnrichLeadRequest(BaseModel):
    """Optional request body for lead enrichment."""
    model_config = ConfigDict(extra="ignore")

    provider: str = "mock_enrichment"


class EnrichmentResponse(BaseModel):
    """
    Response body for enrichment endpoints.

    Describes what the enrichment service did without exposing internal state.
    """
    entity_type: str
    entity_id: uuid.UUID
    provider: str
    found: bool
    fields_updated: list[str] = []
    event_id: Optional[uuid.UUID] = None
    error: Optional[str] = None
    success: bool


class LeadEnrichmentResponse(BaseModel):
    """Response for lead-level enrichment (enriches both company and person)."""
    lead_id: uuid.UUID
    provider: str
    company: Optional[EnrichmentResponse] = None
    person: Optional[EnrichmentResponse] = None
