"""CRM action execution provider for controlled internal CRM mutations."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult
from app.models.base import Interaction, Lead, LEAD_STATUS_VALUES, INTERACTION_TYPE_VALUES


class CRMActionProvider(ExecutionProvider):
    """Executes validated and controlled CRM updates without arbitrary writes."""

    @property
    def provider_name(self) -> str:
        return "crm_action_provider"

    @property
    def supported_task_types(self) -> list[str]:
        return ["crm_update"]

    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        if not session:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="SESSION_REQUIRED",
                error_message="CRM action provider requires an active database session",
            )

        crm_action = context.payload.get("crm_action")
        if not crm_action:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MISSING_CRM_ACTION",
                error_message="crm_update task requires 'crm_action' in payload",
            )

        if not context.lead_id:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="MISSING_LEAD_ID",
                error_message="crm_update requires an associated lead_id",
            )

        lead = await session.get(Lead, context.lead_id)
        if not lead:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="LEAD_NOT_FOUND",
                error_message=f"Lead '{context.lead_id}' not found in database",
            )

        if crm_action == "update_lead_status":
            new_status = context.payload.get("status")
            if new_status not in LEAD_STATUS_VALUES:
                return ExecutionResult(
                    success=False,
                    status="failed",
                    provider=self.provider_name,
                    error_code="INVALID_STATUS",
                    error_message=f"Status '{new_status}' is invalid. Allowed: {LEAD_STATUS_VALUES}",
                )
            old_status = lead.status
            lead.status = new_status
            return ExecutionResult(
                success=True,
                status="completed",
                provider=self.provider_name,
                result={
                    "action": "update_lead_status",
                    "lead_id": str(lead.id),
                    "previous_status": old_status,
                    "new_status": new_status,
                },
            )

        elif crm_action == "append_interaction":
            interaction_type = context.payload.get("interaction_type", "note")
            if interaction_type not in INTERACTION_TYPE_VALUES:
                return ExecutionResult(
                    success=False,
                    status="failed",
                    provider=self.provider_name,
                    error_code="INVALID_INTERACTION_TYPE",
                    error_message=f"Interaction type '{interaction_type}' is invalid",
                )
            subject = context.payload.get("subject", "CRM Note")
            body = context.payload.get("body", "")
            direction = context.payload.get("direction", "outbound")
            interaction = Interaction(
                lead_id=lead.id,
                interaction_type=interaction_type,
                direction=direction if direction in ("inbound", "outbound") else "outbound",
                subject=subject,
                body=body,
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(interaction)
            await session.flush()
            return ExecutionResult(
                success=True,
                status="completed",
                provider=self.provider_name,
                result={
                    "action": "append_interaction",
                    "interaction_id": str(interaction.id),
                    "interaction_type": interaction_type,
                    "lead_id": str(lead.id),
                },
            )

        elif crm_action == "record_meeting_outcome":
            outcome_notes = context.payload.get("notes", "Meeting concluded.")
            lead.notes = f"{lead.notes or ''}\n[Meeting Outcome]: {outcome_notes}".strip()
            if context.payload.get("status"):
                if context.payload["status"] in LEAD_STATUS_VALUES:
                    lead.status = context.payload["status"]

            return ExecutionResult(
                success=True,
                status="completed",
                provider=self.provider_name,
                result={
                    "action": "record_meeting_outcome",
                    "lead_id": str(lead.id),
                    "notes_appended": outcome_notes,
                },
            )

        else:
            return ExecutionResult(
                success=False,
                status="failed",
                provider=self.provider_name,
                error_code="UNSUPPORTED_CRM_ACTION",
                error_message=f"CRM action '{crm_action}' is unsupported",
            )
