"""
Events package.

Handles:
- Publishing domain events to the database (Event model)
- Webhooks: inbound webhooks from external systems (email replies,
  calendar confirmations, LinkedIn notifications)
- Event fan-out to Redis pub/sub for real-time agent notification

Inbound webhook endpoints will be registered here and mapped
to internal domain events. External systems MUST NOT know about
the internal event schema — adapters translate at the boundary.
"""
