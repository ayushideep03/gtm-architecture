"""
Services package.

Business logic lives here. Services are called by API route handlers
and by agent/worker code. They MUST NOT make direct HTTP calls to
external APIs — those go through the integrations layer.

Future services to add:
- LeadService       — CRUD + status transitions for leads
- CompanyService    — company deduplication, enrichment coordination
- PersonService     — contact management
- InteractionService — logging touchpoints
- TaskService       — task lifecycle management
- EventService      — event publishing and querying
"""
