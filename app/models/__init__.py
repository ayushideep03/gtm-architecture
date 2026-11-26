"""
Models package.

Import all ORM models here so Alembic's autogenerate can discover them
when it imports this package via env.py.
"""

from app.models.base import (  # noqa: F401
    Base,
    Company,
    Event,
    Interaction,
    Lead,
    Organization,
    Person,
    SourceRecord,
    Task,
    EventProcessingState,
)

from app.models.memory import (  # noqa: F401,E402
    AgentDecision,
    Approval,
    ContactConsent,
    Fact,
    FactEvidence,
    OutcomeAttribution,
    ResearchFinding,
    Suppression,
    WorkItem,
)

from app.models.pipeline import (  # noqa: F401,E402
    AppUser,
    Campaign,
    CampaignAccount,
    DemoClock,
    Draft,
    FollowUp,
    Meeting,
)

__all__ = [
    "AppUser", "Campaign", "CampaignAccount", "DemoClock", "Draft", "FollowUp", "Meeting",
    "AgentDecision",
    "Approval",
    "ContactConsent",
    "Fact",
    "FactEvidence",
    "OutcomeAttribution",
    "ResearchFinding",
    "Suppression",
    "WorkItem",
    "Base",
    "Company",
    "Event",
    "Interaction",
    "Lead",
    "Organization",
    "Person",
    "SourceRecord",
    "Task",
    "EventProcessingState",
]
