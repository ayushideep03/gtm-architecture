"""
Lead ingestion API routes.

Endpoints:
    POST /api/v1/leads/ingest   — trigger ingestion from a named provider
    GET  /api/v1/leads/providers — list available providers
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.ingest import IngestRequest, IngestResponse
from app.core.database import get_db
from app.core.logging import get_logger
from app.integrations.lead_source import ProviderError
from app.integrations.lead_source_record import LeadSearchCriteria
from app.integrations.registry import provider_registry
from app.services.lead_ingestion import LeadIngestionService

logger = get_logger(__name__)
router = APIRouter(prefix="/leads", tags=["leads"])

# One stateless service instance for the lifetime of the process
_ingestion_service = LeadIngestionService()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger lead ingestion from a provider",
    description=(
        "Fetches leads from the specified provider using the given query "
        "parameters, normalizes them, persists Company/Person/Lead entities "
        "(deduplicating by domain/email), and emits a lead_ingested event per lead."
    ),
)
async def ingest_leads(
    body: IngestRequest,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    """
    POST /api/v1/leads/ingest

    Flow:
        1. Validate request
        2. Resolve provider from registry
        3. Build LeadSearchCriteria from query
        4. Fetch records from provider
        5. Pass records to LeadIngestionService
        6. Commit and return summary
    """

    # 1. Resolve provider — raises ProviderError if unknown
    try:
        provider = provider_registry.get(body.provider)
    except ProviderError as exc:
        logger.warning("Unknown provider requested: %s", body.provider)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # 2. Build search criteria from optional query dict
    criteria = LeadSearchCriteria(**(body.query or {}))

    # 3. Fetch records from provider
    try:
        records = await provider.search_leads(criteria)
    except ProviderError as exc:
        logger.error("Provider error during search: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Provider error: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected provider failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider returned an unexpected error",
        ) from exc

    logger.info(
        "Provider %r returned %d record(s) for query %s",
        body.provider,
        len(records),
        body.query,
    )

    # 4. Ingest records (service handles per-record error isolation)
    try:
        summary = await _ingestion_service.ingest_batch(
            records=records,
            db=db,
            provider_name=body.provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Ingestion batch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ingestion failed — see server logs",
        ) from exc

    return IngestResponse(**summary.to_dict())


@router.get(
    "/providers",
    summary="List available lead source providers",
    response_model=dict,
)
async def list_providers() -> dict:
    """GET /api/v1/leads/providers — returns the list of registered providers."""
    return {"providers": provider_registry.list_providers()}
