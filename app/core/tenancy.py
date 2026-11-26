"""
Tenancy primitives.

Every tenant-owned row carries ``tenant_id``. A single default organisation is
seeded by migration 0008 so the system works out of the box; additional
organisations are added as rows in ``organizations`` and per-tenant config
overrides under ``tenants:`` in the GTM config.
"""

import uuid
from typing import Any, Optional

# Seeded by migration 0008. Referenced by model defaults so existing callers that
# don't pass a tenant land in the default organisation.
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def tenant_of(*entities: Any) -> uuid.UUID:
    """Return the first non-empty tenant_id found on the given entities."""
    for entity in entities:
        tid: Optional[uuid.UUID] = getattr(entity, "tenant_id", None) if entity is not None else None
        if tid:
            return tid
    return DEFAULT_TENANT_ID
