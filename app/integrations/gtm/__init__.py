"""GTM Capability Providers for research, communication, meetings, and CRM operations."""

from app.execution.registry import ExecutionRegistry
from app.integrations.gtm.crm import CRMActionProvider
from app.integrations.gtm.meetings import MockMeetingBookingProvider
from app.integrations.gtm.outreach import (
    MockOutreachDraftingProvider,
    MockOutreachSendProvider,
)
from app.integrations.gtm.research import MockProspectResearchProvider


def register_gtm_capability_providers(registry: ExecutionRegistry) -> None:
    """
    Explicitly register the mock GTM providers into a given registry.

    Not called automatically: the process-wide registry is populated from
    configuration (config/providers/mocks.yaml in development/test profiles).
    """
    registry.register(MockProspectResearchProvider())
    registry.register(MockOutreachDraftingProvider())
    registry.register(MockOutreachSendProvider())
    registry.register(MockMeetingBookingProvider())
    registry.register(CRMActionProvider())



__all__ = [
    "MockProspectResearchProvider",
    "MockOutreachDraftingProvider",
    "MockOutreachSendProvider",
    "MockMeetingBookingProvider",
    "CRMActionProvider",
    "register_gtm_capability_providers",
]
