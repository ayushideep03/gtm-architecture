"""GTM Capability Providers for research, communication, meetings, and CRM operations."""

from app.execution.registry import ExecutionRegistry, execution_registry
from app.integrations.gtm.crm import CRMActionProvider
from app.integrations.gtm.meetings import MockMeetingBookingProvider
from app.integrations.gtm.outreach import (
    MockOutreachDraftingProvider,
    MockOutreachSendProvider,
)
from app.integrations.gtm.research import MockProspectResearchProvider


def register_gtm_capability_providers(registry: ExecutionRegistry = execution_registry) -> None:
    """Register all GTM capability providers into the execution registry."""
    registry.register(MockProspectResearchProvider())
    registry.register(MockOutreachDraftingProvider())
    registry.register(MockOutreachSendProvider())
    registry.register(MockMeetingBookingProvider())
    registry.register(CRMActionProvider())


# Automatically register on import
register_gtm_capability_providers(execution_registry)

__all__ = [
    "MockProspectResearchProvider",
    "MockOutreachDraftingProvider",
    "MockOutreachSendProvider",
    "MockMeetingBookingProvider",
    "CRMActionProvider",
    "register_gtm_capability_providers",
]
