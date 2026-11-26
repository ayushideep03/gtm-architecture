"""
Tenancy primitives.

Every tenant-owned row carries ``tenant_id``. A single default organisation is
seeded by migration 0008 so the system works out of the box; additional
organisations are added as rows in ``organizations`` and per-tenant config
overrides under ``tenants:`` in the GTM config.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

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


# ── Request / job tenant context ──────────────────────────────────────────────
# Set for the duration of an API request (from the authenticated principal) and
# around every unit of worker work done for a lead. Two effects:
#   1. new tenant-scoped rows default to this tenant (TenantScoped mixin);
#   2. every database transaction sets ``app.tenant_id``, which the row-level
#      security policies (migration 0012) enforce: only this tenant's rows are
#      visible or writable. Unset (e.g. the worker scanning queues) = all tenants.

current_tenant: ContextVar[Optional[uuid.UUID]] = ContextVar("oxygen_current_tenant", default=None)


def active_tenant() -> uuid.UUID:
    return current_tenant.get() or DEFAULT_TENANT_ID


@contextmanager
def tenant_scope(tenant_id: Optional[uuid.UUID]) -> Iterator[None]:
    token = current_tenant.set(tenant_id)
    try:
        yield
    finally:
        current_tenant.reset(token)
