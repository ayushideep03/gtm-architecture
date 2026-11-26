"""
Execution registry (compatibility module).

Execution now uses the unified provider registry in ``app.providers``: many
providers per capability, loaded from configuration, chosen per call by the
ProviderSelector. ``ExecutionRegistry`` is kept as the same class so existing
code and tests that build a private registry keep working, and
``execution_registry`` is the process-wide, config-loaded registry.
"""

from app.providers import get_provider_registry
from app.providers.registry import ProviderRegistry


class ExecutionRegistry(ProviderRegistry):
    """A standalone registry (no config loader) — useful for tests and embedding."""


execution_registry: ProviderRegistry = get_provider_registry()

__all__ = ["ExecutionRegistry", "execution_registry"]
