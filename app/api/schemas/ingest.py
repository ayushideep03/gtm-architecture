"""
Request and response schemas for the lead ingestion API.

These are separate from the domain schemas (app/schemas/__init__.py) because
they describe the API contract for the ingestion endpoint, not the domain
entities themselves.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class IngestRequest(BaseModel):
    """
    Request body for POST /api/v1/leads/ingest.

    provider:  The registered provider key (e.g. "mock_apollo", "mock_scout").
    query:     Search parameters passed to the provider. All fields optional.
    """

    model_config = ConfigDict(extra="ignore")

    provider: str
    query: Optional[dict[str, Any]] = None

    @field_validator("provider")
    @classmethod
    def provider_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("provider must not be empty")
        return v


class IngestResponse(BaseModel):
    """
    Response body for POST /api/v1/leads/ingest.

    Summarizes what was created/found during ingestion.
    Does NOT expose raw provider data.
    """

    provider: str
    records_received: int
    companies_created: int
    companies_existing: int
    people_created: int
    people_existing: int
    leads_created: int
    events_created: int
    errors: list[str] = []
