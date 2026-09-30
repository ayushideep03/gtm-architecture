"""
Agents package.

Agents are autonomous decision-makers that consume Events, execute Tasks,
and produce new Events / state changes.

The Oxygen Orchestrator (to be built) lives here and coordinates:
    LeadSourceAgent      — pulls leads from configured sources
    EnrichmentAgent      — enriches raw leads with firmographic data
    QualificationAgent   — scores and qualifies leads
    OutreachAgent        — crafts and sends personalised outreach
    FollowUpAgent        — monitors replies and triggers follow-ups
    MeetingBookingAgent  — handles calendar scheduling

Agents communicate via Events (database) and Tasks (database + Redis queue).
They MUST use the integrations layer for all external calls.
"""
