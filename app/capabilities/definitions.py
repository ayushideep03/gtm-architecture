"""
Built-in capability definitions.

These are contracts, not workflow: nothing here says in which order capabilities
run or for which leads. Oxygen decides that. Repeatability values are defaults
that config can override per capability or per tenant.

To add a capability: define its input model and a CapabilitySpec here (or in a
plugin module registered with the catalogue). Guardrails, Oxygen and the
provider selector pick it up from the metadata automatically.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.capabilities.invokers import (
    invoke_company_enrichment,
    invoke_find_leads,
    invoke_person_enrichment,
)
from app.capabilities.spec import CapabilitySpec
from app.core.gtm_config import RepeatabilityConfig, SideEffect
from app.execution.interfaces import ExecutionProvider
from app.integrations.enrichment_provider import (
    CompanyEnrichmentProvider,
    PersonEnrichmentProvider,
)
from app.integrations.lead_source import LeadSourceProvider
from app.integrations.lead_source_record import LeadSearchCriteria

DAY = 86_400.0


class _Params(BaseModel):
    # Unknown keys are kept: providers may accept vendor-specific extras, and
    # test hooks like force_fail must reach mock providers.
    model_config = ConfigDict(extra="allow")


# ── Input models ──────────────────────────────────────────────────────────────

class FindLeadsInput(LeadSearchCriteria):
    model_config = ConfigDict(extra="allow")


class EnrichCompanyInput(_Params):
    company_id: Optional[uuid.UUID] = None
    domain: Optional[str] = None

    @model_validator(mode="after")
    def _need_identifier(self):
        if not (self.company_id or self.domain):
            raise ValueError("enrich_company requires 'company_id' or 'domain'")
        return self


class EnrichPersonInput(_Params):
    person_id: Optional[uuid.UUID] = None
    email: Optional[str] = None

    @model_validator(mode="after")
    def _need_identifier(self):
        if not (self.person_id or self.email):
            raise ValueError("enrich_person requires 'person_id' or 'email'")
        return self


class QualifyLeadInput(_Params):
    lead_id: uuid.UUID
    objective: Optional[str] = None


class ResearchProspectInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    domain: Optional[str] = None
    name: Optional[str] = None
    focus: list[str] = Field(default_factory=list)


class DraftOutreachInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    channel: str = "email"                        # validated against oxygen.outreach.channels
    angle: Optional[str] = None
    research_task_id: Optional[uuid.UUID] = None
    follow_up_of: Optional[uuid.UUID] = None      # previous draft/send this follows up
    step: Optional[int] = None                    # campaign sequence step this draft is for
    variant: int = 0                              # "regenerate": ask for a different version

    @model_validator(mode="after")
    def _channel_allowed(self):
        from app.core.gtm_config import get_gtm_config
        allowed = get_gtm_config().oxygen.outreach.channels
        if self.channel not in allowed:
            raise ValueError(f"channel {self.channel!r} not in configured channels {allowed}")
        return self


class SendEmailInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    draft_task_id: Optional[uuid.UUID] = None
    recipient_email: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None


class DraftReplyInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    reply_interaction_id: Optional[uuid.UUID] = None   # defaults to the latest inbound reply


class BookMeetingInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    topic: Optional[str] = None
    scheduled_for: Optional[str] = None
    duration_minutes: int = 30


class AnalyzeIntentInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    focus: Optional[str] = None


class RepTaskInput(_Params):
    lead_id: Optional[uuid.UUID] = None
    channel: str                                  # e.g. linkedin | phone
    instructions: str = Field(min_length=10)
    draft_task_id: Optional[uuid.UUID] = None     # prepared message / call script for the rep
    due_in_hours: float = 24.0


class CrmUpdateInput(_Params):
    crm_action: str  # validated by the CRM provider, which owns its supported actions
    status: Optional[str] = None


# ── Specs ─────────────────────────────────────────────────────────────────────

BUILTIN_CAPABILITIES: list[CapabilitySpec] = [
    CapabilitySpec(
        name="find_leads",
        description="Search a lead source for prospects matching criteria (titles, domain, location, keywords).",
        category="lead_source",
        input_model=FindLeadsInput,
        side_effect=SideEffect.READ_ONLY,
        provider_interfaces=(LeadSourceProvider,),
        invoker=invoke_find_leads,
    ),
    CapabilitySpec(
        name="enrich_company",
        description="Fill in firmographic and technographic data for the lead's company (size, industry, funding, tech stack).",
        category="enrichment",
        input_model=EnrichCompanyInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="company",
        requires=["company"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=3, cooldown_s=7 * DAY),
        aliases=["company_enrichment"],
        provider_interfaces=(CompanyEnrichmentProvider, ExecutionProvider),
        invoker=invoke_company_enrichment,
    ),
    CapabilitySpec(
        name="enrich_person",
        description="Fill in professional details for the contact (seniority, department, location).",
        category="enrichment",
        input_model=EnrichPersonInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="person",
        requires=["person"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=3, cooldown_s=7 * DAY),
        aliases=["person_enrichment"],
        provider_interfaces=(PersonEnrichmentProvider, ExecutionProvider),
        invoker=invoke_person_enrichment,
    ),
    CapabilitySpec(
        name="qualify_lead",
        description="Assess the lead against the campaign's ideal customer profile and record a score with rationale.",
        category="qualification",
        input_model=QualifyLeadInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["lead"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=3, cooldown_s=1 * DAY),
        aliases=["lead_qualification"],
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="research_prospect",
        description="Research the prospect and their company (news, initiatives, pain points) and return structured, cited findings.",
        category="research",
        input_model=ResearchProspectInput,
        side_effect=SideEffect.READ_ONLY,
        target_type="lead",
        requires=["lead"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=3, cooldown_s=1 * DAY),
        aliases=["prospect_research"],
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="draft_outreach",
        description="Write a personalised outreach message for the contact using verified lead context and research.",
        category="outreach",
        input_model=DraftOutreachInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["person"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=10),
        aliases=["outreach_drafting"],
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="draft_reply",
        description="Answer a prospect's question or objection using ONLY the approved knowledge base; "
                    "questions it cannot answer from the knowledge base are escalated to a person, never guessed.",
        category="outreach",
        input_model=DraftReplyInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["person"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=6),
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="send_email",
        description="Send an approved outreach email to the contact.",
        category="outreach",
        input_model=SendEmailInput,
        side_effect=SideEffect.EXTERNAL_COMMUNICATION,
        target_type="lead",
        requires=["person.email"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=4, cooldown_s=2 * DAY),
        idempotency_fields=["draft_task_id"],
        aliases=["send_outreach_simulation", "outreach_send"],
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="book_meeting",
        description="Schedule a meeting with the contact on the calendar.",
        category="meetings",
        input_model=BookMeetingInput,
        side_effect=SideEffect.EXTERNAL_COMMUNICATION,
        target_type="lead",
        requires=["person.email"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=2, cooldown_s=1 * DAY),
        aliases=["book_meeting_simulation", "meeting_booking"],
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="analyze_intent",
        description="Assess buying intent and timing from research findings, interactions and facts; returns cited signals and counter-signals.",
        category="intent",
        input_model=AnalyzeIntentInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["lead"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=5, cooldown_s=1 * DAY),
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="create_rep_task",
        description="Hand a channel action to a human rep (e.g. send a prepared LinkedIn message or make a call with a prepared script). Oxygen prepares, a person executes.",
        category="engagement",
        input_model=RepTaskInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["person"],
        repeatability=RepeatabilityConfig(mode="repeatable", max_per_lead=6, cooldown_s=1 * DAY),
        provider_interfaces=(ExecutionProvider,),
    ),
    CapabilitySpec(
        name="crm_update",
        description="Update the lead's CRM record (status, notes, interaction log, meeting outcomes).",
        category="crm",
        input_model=CrmUpdateInput,
        side_effect=SideEffect.INTERNAL_WRITE,
        target_type="lead",
        requires=["lead"],
        repeatability=RepeatabilityConfig(mode="repeatable"),
        provider_interfaces=(ExecutionProvider,),
    ),
]
