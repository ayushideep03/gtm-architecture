"""
Enrichment API routes.

Endpoints:
    POST /api/v1/enrichment/company/{company_id} — manually trigger company enrichment
    POST /api/v1/enrichment/person/{person_id}   — manually trigger person enrichment
    POST /api/v1/enrichment/lead/{lead_id}       — manually trigger lead-level enrichment
    GET  /api/v1/enrichment/providers           — list available enrichment providers
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.enrichment import (
    EnrichCompanyRequest,
    EnrichLeadRequest,
    EnrichPersonRequest,
    EnrichmentResponse,
    LeadEnrichmentResponse,
)
from app.core.database import get_db
from app.core.logging import get_logger
from app.integrations.enrichment_provider import EnrichmentProviderError
from app.integrations.enrichment_registry import enrichment_registry
from app.models.base import Company, Lead, Person
from app.services.enrichment import EnrichmentOutcome, EnrichmentService

logger = get_logger(__name__)
router = APIRouter(prefix="/enrichment", tags=["enrichment"])

_enrichment_service = EnrichmentService()


def _to_response(outcome: EnrichmentOutcome) -> EnrichmentResponse:
    return EnrichmentResponse(
        entity_type=outcome.entity_type,
        entity_id=outcome.entity_id,
        provider=outcome.provider,
        found=outcome.found,
        fields_updated=outcome.fields_updated,
        event_id=outcome.event_id,
        error=outcome.error,
        success=outcome.success,
    )


@router.post(
    "/company/{company_id}",
    response_model=EnrichmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger company enrichment",
)
async def enrich_company(
    company_id: uuid.UUID,
    body: Optional[EnrichCompanyRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> EnrichmentResponse:
    provider_name = body.provider if body else "mock_enrichment"

    # Validate provider
    try:
        enrichment_registry.get_company_provider(provider_name)
    except EnrichmentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Check entity exists
    exists = await db.execute(select(Company).where(Company.id == company_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )

    outcome = await _enrichment_service.enrich_company_by_id(
        db=db,
        company_id=company_id,
        provider_name=provider_name,
    )
    await db.commit()
    return _to_response(outcome)


@router.post(
    "/person/{person_id}",
    response_model=EnrichmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger person enrichment",
)
async def enrich_person(
    person_id: uuid.UUID,
    body: Optional[EnrichPersonRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> EnrichmentResponse:
    provider_name = body.provider if body else "mock_enrichment"

    # Validate provider
    try:
        enrichment_registry.get_person_provider(provider_name)
    except EnrichmentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Check entity exists
    exists = await db.execute(select(Person).where(Person.id == person_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found",
        )

    outcome = await _enrichment_service.enrich_person_by_id(
        db=db,
        person_id=person_id,
        provider_name=provider_name,
    )
    await db.commit()
    return _to_response(outcome)


@router.post(
    "/lead/{lead_id}",
    response_model=LeadEnrichmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger lead-level enrichment",
)
async def enrich_lead(
    lead_id: uuid.UUID,
    body: Optional[EnrichLeadRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> LeadEnrichmentResponse:
    provider_name = body.provider if body else "mock_enrichment"

    # Validate provider
    available = enrichment_registry.list_providers()
    all_known = set(available.get("company", []) + available.get("person", []))
    if provider_name not in all_known:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown enrichment provider {provider_name!r}",
        )

    # Check entity exists
    exists = await db.execute(select(Lead).where(Lead.id == lead_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    outcomes = await _enrichment_service.enrich_lead(
        db=db,
        lead_id=lead_id,
        provider_name=provider_name,
    )
    await db.commit()

    return LeadEnrichmentResponse(
        lead_id=lead_id,
        provider=provider_name,
        company=_to_response(outcomes["company"]) if outcomes.get("company") else None,
        person=_to_response(outcomes["person"]) if outcomes.get("person") else None,
    )


@router.get(
    "/providers",
    summary="List available enrichment providers",
    response_model=dict,
)
async def list_providers() -> dict:
    """GET /api/v1/enrichment/providers — list registered company and person providers."""
    return enrichment_registry.list_providers()
