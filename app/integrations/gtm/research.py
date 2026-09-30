"""Deterministic mock prospect research provider."""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Company, Lead, Person


class MockProspectResearchProvider(ExecutionProvider):
    """Deterministic mock provider for account and prospect research."""

    @property
    def provider_name(self) -> str:
        return "mock_prospect_research"

    @property
    def supported_task_types(self) -> list[str]:
        return ["research_prospect"]

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
                error_code="RESEARCH_FAILED",
                error_message=context.payload.get("error_message", "Simulated research failure"),
            )

        company_domain = context.payload.get("domain")
        contact_name = context.payload.get("name")

        if session and context.lead_id:
            lead = await session.get(Lead, context.lead_id)
            if lead and lead.person_id:
                person = await session.get(Person, lead.person_id)
                if person:
                    contact_name = contact_name or f"{person.first_name} {person.last_name or ''}".strip()
                    if person.company_id:
                        comp = await session.get(Company, person.company_id)
                        if comp:
                            company_domain = company_domain or comp.domain

        findings = {
            "target_account": company_domain or "unknown_domain",
            "key_contact": contact_name or "unknown_contact",
            "industry_fit": "High",
            "buying_signals": [
                "Recent leadership expansion in engineering",
                "Evaluating enterprise automation architecture",
            ],
            "pain_points": [
                "Manual sales development workflows",
                "Data silo fragmentation between CRM and enrichment sources",
            ],
            "recommended_angle": "Autonomous GTM orchestration with deterministic guardrails",
        }

        return ExecutionResult(
            success=True,
            status="completed",
            provider=self.provider_name,
            result={
                "summary": f"Deterministic mock research findings for {company_domain or 'prospect'}.",
                "structured_findings": findings,
                "provider_name": self.provider_name,
                "research_version": "mock-v1",
            },
        )
