"""Execution workers, mock providers, and adapters."""

from typing import Optional
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.execution.interfaces import ExecutionProvider
from app.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    ExecutionResult,
)
from app.execution.registry import ExecutionRegistry, execution_registry
from app.models.base import Company, Lead, Person
from app.services.enrichment import EnrichmentService

logger = get_logger(__name__)


# ── Mock Lead Qualification Provider ──────────────────────────────────────────

class MockLeadQualificationProvider(ExecutionProvider):
    """Deterministic mock provider for lead qualification."""

    @property
    def provider_name(self) -> str:
        return "mock_lead_qualification"

    @property
    def supported_task_types(self) -> list[str]:
        return ["qualify_lead"]

    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        """Deterministically qualify a lead based on context and CRM state."""
        # 1. Check for intentional test failure simulation
        if context.payload.get("force_fail"):
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MOCK_QUALIFICATION_FAILED",
                error_message=context.payload.get(
                    "error_message", "Mock qualification failure simulated"
                ),
            )

        lead: Optional[Lead] = None
        has_domain = False
        has_email = False

        # 2. Check if direct lead context is loaded via session
        if session and context.lead_id:
            stmt = select(Lead).where(Lead.id == context.lead_id)
            res = await session.execute(stmt)
            lead = res.scalar_one_or_none()

            if lead and lead.person_id:
                p_stmt = select(Person).where(Person.id == lead.person_id)
                p_res = await session.execute(p_stmt)
                person = p_res.scalar_one_or_none()
                if person and person.email:
                    has_email = True
                if person and person.company_id:
                    c_stmt = select(Company).where(Company.id == person.company_id)
                    c_res = await session.execute(c_stmt)
                    comp = c_res.scalar_one_or_none()
                    if comp and comp.domain:
                        has_domain = True

        # Check payload overrides if DB lookups were partial
        if "domain" in context.payload:
            has_domain = bool(context.payload["domain"])
        if "email" in context.payload:
            has_email = bool(context.payload["email"])

        # 3. Deterministic evaluation logic
        explicit_disqualify = context.payload.get("qualified") is False
        if explicit_disqualify:
            is_qualified = False
            score = context.payload.get("score", 25)
            reason = context.payload.get(
                "reason", "Explicitly disqualified by policy parameters"
            )
        elif has_domain or has_email or context.payload.get("qualified") is True:
            is_qualified = True
            score = context.payload.get("score", 85)
            reason = "Valid business contact with verified domain parameters"
        else:
            is_qualified = True
            score = 75
            reason = "Standard qualification threshold met"

        # 4. Update Lead in CRM if session and lead exist
        if session and lead:
            lead.status = "qualified" if is_qualified else "disqualified"
            lead.score = score

        output_data = {
            "qualified": is_qualified,
            "score": score,
            "reason": reason,
            "qualification_version": "mock-v1",
        }

        return ExecutionResult(
            success=True,
            status="completed",
            provider=self.provider_name,
            result=output_data,
        )


# ── Company Enrichment Execution Adapter ─────────────────────────────────────

class CompanyEnrichmentExecutionAdapter(ExecutionProvider):
    """Execution adapter delegating company enrichment to EnrichmentService."""

    def __init__(self, enrichment_service: Optional[EnrichmentService] = None) -> None:
        self.service = enrichment_service or EnrichmentService()

    @property
    def provider_name(self) -> str:
        return "mock_company_enrichment_adapter"

    @property
    def supported_task_types(self) -> list[str]:
        return ["enrich_company"]

    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        if context.payload.get("force_fail"):
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MOCK_ADAPTER_FAILED",
                error_message=context.payload.get(
                    "error_message", "Mock company enrichment failure simulated"
                ),
            )

        if not session:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="SESSION_REQUIRED",
                error_message="Database session is required for company enrichment adapter",
            )

        company_id_raw = context.payload.get("company_id") or context.target_id
        if not company_id_raw:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MISSING_TARGET_ID",
                error_message="Missing company_id in execution payload",
            )

        try:
            company_uuid = (
                company_id_raw
                if isinstance(company_id_raw, uuid.UUID)
                else uuid.UUID(str(company_id_raw))
            )
        except ValueError:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="INVALID_UUID",
                error_message=f"Invalid company UUID: {company_id_raw}",
            )

        outcome = await self.service.enrich_company_by_id(
            db=session,
            company_id=company_uuid,
            provider_name="mock_enrichment",
        )

        if outcome.success:
            return ExecutionResult(
                success=True,
                status="completed",
                provider=self.provider_name,
                result={
                    "entity_type": outcome.entity_type,
                    "entity_id": str(outcome.entity_id),
                    "fields_updated": outcome.fields_updated,
                    "found": outcome.found,
                },
            )
        else:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="COMPANY_ENRICHMENT_FAILED",
                error_message=outcome.error or "Company enrichment failed",
            )


# ── Person Enrichment Execution Adapter ──────────────────────────────────────

class PersonEnrichmentExecutionAdapter(ExecutionProvider):
    """Execution adapter delegating person enrichment to EnrichmentService."""

    def __init__(self, enrichment_service: Optional[EnrichmentService] = None) -> None:
        self.service = enrichment_service or EnrichmentService()

    @property
    def provider_name(self) -> str:
        return "mock_person_enrichment_adapter"

    @property
    def supported_task_types(self) -> list[str]:
        return ["enrich_person"]

    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        if context.payload.get("force_fail"):
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MOCK_ADAPTER_FAILED",
                error_message=context.payload.get(
                    "error_message", "Mock person enrichment failure simulated"
                ),
            )

        if not session:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="SESSION_REQUIRED",
                error_message="Database session is required for person enrichment adapter",
            )

        person_id_raw = context.payload.get("person_id") or context.target_id
        if not person_id_raw:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MISSING_TARGET_ID",
                error_message="Missing person_id in execution payload",
            )

        try:
            person_uuid = (
                person_id_raw
                if isinstance(person_id_raw, uuid.UUID)
                else uuid.UUID(str(person_id_raw))
            )
        except ValueError:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="INVALID_UUID",
                error_message=f"Invalid person UUID: {person_id_raw}",
            )

        outcome = await self.service.enrich_person_by_id(
            db=session,
            person_id=person_uuid,
            provider_name="mock_enrichment",
        )

        if outcome.success:
            return ExecutionResult(
                success=True,
                status="completed",
                provider=self.provider_name,
                result={
                    "entity_type": outcome.entity_type,
                    "entity_id": str(outcome.entity_id),
                    "fields_updated": outcome.fields_updated,
                    "found": outcome.found,
                },
            )
        else:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="PERSON_ENRICHMENT_FAILED",
                error_message=outcome.error or "Person enrichment failed",
            )


# ── Default Registration ─────────────────────────────────────────────────────

def register_default_providers(registry: Optional[ExecutionRegistry] = None) -> None:
    """Register default mock providers and adapters into registry."""
    reg = registry or execution_registry
    reg.register(MockLeadQualificationProvider())
    reg.register(CompanyEnrichmentExecutionAdapter())
    reg.register(PersonEnrichmentExecutionAdapter())


# Register defaults at import time
register_default_providers(execution_registry)


# ── Worker Entrypoint Function ───────────────────────────────────────────────

async def execute_task(
    task_id: uuid.UUID,
    session: Optional[AsyncSession] = None,
) -> ExecutionOutcome:
    """Service function to execute a single task by ID."""
    from app.core.database import AsyncSessionLocal
    from app.execution.executor import TaskExecutor

    executor = TaskExecutor(execution_registry)

    if session is not None:
        return await executor.execute(task_id, session)

    async with AsyncSessionLocal() as fresh_session:
        return await executor.execute(task_id, fresh_session)
