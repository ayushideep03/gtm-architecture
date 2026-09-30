"""Deterministic mock providers for outreach drafting and send simulation."""

from datetime import datetime, timezone
from typing import Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Interaction, Lead, Person


class MockOutreachDraftingProvider(ExecutionProvider):
    """Deterministic template-based email and message drafting provider."""

    @property
    def provider_name(self) -> str:
        return "mock_outreach_drafting"

    @property
    def supported_task_types(self) -> list[str]:
        return ["draft_outreach"]

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
                error_code="DRAFTING_FAILED",
                error_message=context.payload.get("error_message", "Outreach drafting failed"),
            )

        recipient_name = context.payload.get("first_name", "there")
        company_name = context.payload.get("company_name", "your team")

        if session and context.lead_id:
            lead = await session.get(Lead, context.lead_id)
            if lead and lead.person_id:
                person = await session.get(Person, lead.person_id)
                if person and person.first_name:
                    recipient_name = person.first_name

        subject = f"Scaling GTM automation at {company_name}"
        body = (
            f"Hi {recipient_name},\n\n"
            f"I noticed {company_name} is expanding sales infrastructure. We built an autonomous "
            f"orchestration pipeline that manages enrichment, deterministic guardrails, and execution.\n\n"
            f"Would you be open to a 10-minute briefing next week?\n\nBest,\nSales Engineering"
        )

        return ExecutionResult(
            success=True,
            status="completed",
            provider=self.provider_name,
            result={
                "subject": subject,
                "body": body,
                "template_version": "mock-v1",
                "is_draft": True,
            },
        )


class MockOutreachSendProvider(ExecutionProvider):
    """Safe outreach send simulation provider that never calls SMTP or external APIs."""

    @property
    def provider_name(self) -> str:
        return "mock_outreach_send"

    @property
    def supported_task_types(self) -> list[str]:
        return ["send_outreach_simulation"]

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
                error_code="SEND_SIMULATION_FAILED",
                error_message=context.payload.get("error_message", "Simulated delivery failed"),
            )

        recipient_email = context.payload.get("recipient_email") or context.payload.get("email")
        subject = context.payload.get("subject", "Automated Outreach")
        body = context.payload.get("body", "Outreach message body")

        if session and context.lead_id and not recipient_email:
            lead = await session.get(Lead, context.lead_id)
            if lead and lead.person_id:
                person = await session.get(Person, lead.person_id)
                if person and person.email:
                    recipient_email = person.email

        if not recipient_email:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MISSING_RECIPIENT",
                error_message="Cannot simulate outreach: missing recipient email",
            )

        simulated_message_id = f"sim-msg-{uuid.uuid4()}"

        # Record outbound interaction in CRM if session and lead exist
        if session and context.lead_id:
            interaction = Interaction(
                lead_id=context.lead_id,
                interaction_type="email_sent",
                direction="outbound",
                subject=subject,
                body=body,
                channel_message_id=simulated_message_id,
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(interaction)

        return ExecutionResult(
            success=True,
            status="completed",
            provider=self.provider_name,
            result={
                "sent": True,
                "mode": "simulation",
                "provider": self.provider_name,
                "message_id": simulated_message_id,
                "recipient": recipient_email,
                "subject": subject,
            },
        )
