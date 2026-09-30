"""
Workers package.

Background workers execute Tasks created by agents.
They run outside the API process using a job queue (Redis-backed).

Planned workers:
    EnrichmentWorker    — enriches leads via EnrichmentProvider
    EmailWorker         — sends emails via EmailProvider
    LinkedInWorker      — executes LinkedIn actions via LinkedInProvider
    ScrapingWorker      — pulls new leads from LeadSource
    EventDispatchWorker — fans out events to interested agents

Workers MUST be idempotent — safe to retry on failure.
Each worker logs its start/completion as a Task status update.
"""
