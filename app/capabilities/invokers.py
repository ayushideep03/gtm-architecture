"""
Invokers: how to run a capability on a provider that implements a vendor
interface (enrichment, lead source) rather than the generic ExecutionProvider.

The TaskExecutor always deals in ExecutionContext -> ExecutionResult. When the
selected provider is, say, a CompanyEnrichmentProvider, the capability's
invoker adapts the call: it resolves the target entity, calls the vendor
through the existing service layer, and returns a standard ExecutionResult.

Imports of services are local to avoid import cycles at module load.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import ExecutionContext, ExecutionResult


def _provider_name(provider: Any) -> str:
    return getattr(provider, "provider_name", type(provider).__name__)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _failure(provider: Any, code: str, message: str) -> ExecutionResult:
    return ExecutionResult(
        success=False, status="failed", provider=_provider_name(provider),
        error_code=code, error_message=message,
    )


async def invoke_company_enrichment(
    provider: Any, context: ExecutionContext, session: Optional[AsyncSession]
) -> ExecutionResult:
    from app.services.enrichment import EnrichmentService
    from app.services.identity_resolution import IdentityResolver

    if session is None:
        return _failure(provider, "SESSION_REQUIRED", "Company enrichment requires a database session")

    company_id = _as_uuid(context.payload.get("company_id") or context.target_id)
    if company_id is None and context.payload.get("domain"):
        resolved = await IdentityResolver().resolve_company(session, domain=context.payload["domain"])
        company_id = resolved.company_id
    if company_id is None:
        return _failure(provider, "MISSING_TARGET_ID", "No resolvable company for enrichment")

    outcome = await EnrichmentService().enrich_company_by_id(session, company_id, provider)
    if not outcome.success:
        return _failure(provider, "COMPANY_ENRICHMENT_FAILED", outcome.error or "Company enrichment failed")
    return ExecutionResult(
        success=True, status="completed", provider=_provider_name(provider),
        result={
            "entity_type": outcome.entity_type,
            "entity_id": str(outcome.entity_id),
            "fields_updated": outcome.fields_updated,
            "found": outcome.found,
        },
    )


async def invoke_person_enrichment(
    provider: Any, context: ExecutionContext, session: Optional[AsyncSession]
) -> ExecutionResult:
    from app.services.enrichment import EnrichmentService
    from app.services.identity_resolution import IdentityResolver

    if session is None:
        return _failure(provider, "SESSION_REQUIRED", "Person enrichment requires a database session")

    person_id = _as_uuid(context.payload.get("person_id") or context.target_id)
    if person_id is None and context.payload.get("email"):
        resolved = await IdentityResolver().resolve_person(session, email=context.payload["email"])
        person_id = resolved.person_id
    if person_id is None:
        return _failure(provider, "MISSING_TARGET_ID", "No resolvable person for enrichment")

    outcome = await EnrichmentService().enrich_person_by_id(session, person_id, provider)
    if not outcome.success:
        return _failure(provider, "PERSON_ENRICHMENT_FAILED", outcome.error or "Person enrichment failed")
    return ExecutionResult(
        success=True, status="completed", provider=_provider_name(provider),
        result={
            "entity_type": outcome.entity_type,
            "entity_id": str(outcome.entity_id),
            "fields_updated": outcome.fields_updated,
            "found": outcome.found,
        },
    )


async def invoke_find_leads(
    provider: Any, context: ExecutionContext, session: Optional[AsyncSession]
) -> ExecutionResult:
    from app.integrations.lead_source_record import LeadSearchCriteria
    from app.services.lead_ingestion import LeadIngestionService

    if session is None:
        return _failure(provider, "SESSION_REQUIRED", "Lead search requires a database session")
    known = set(LeadSearchCriteria.model_fields)
    criteria = LeadSearchCriteria(**{k: v for k, v in context.payload.items() if k in known})
    records = await provider.search_leads(criteria)
    summary = await LeadIngestionService().ingest_batch(
        records=records, db=session, provider_name=_provider_name(provider)
    )
    return ExecutionResult(
        success=True, status="completed", provider=_provider_name(provider),
        result=summary.to_dict(),
    )
