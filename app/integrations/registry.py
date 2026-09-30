"""
Provider registry.

Maps provider name strings → LeadSourceProvider instances.

Rules:
- The ingestion service and API routes call registry.get(name).
- They never import concrete provider classes directly.
- Adding a new provider: register it here only. No other file changes needed.

Usage::

    from app.integrations.registry import provider_registry
    provider = provider_registry.get("mock_apollo")
    records = await provider.search_leads(criteria)
"""

from app.integrations.lead_source import LeadSourceProvider, ProviderError
from app.integrations.mock_apollo import MockApolloProvider
from app.integrations.mock_scout import MockScoutProvider


class ProviderRegistry:
    """
    Simple registry mapping provider name → provider instance.

    Providers are registered as singletons (one instance per registry).
    This is fine for stateless providers. Stateful providers should
    override this with a factory function.
    """

    def __init__(self) -> None:
        self._providers: dict[str, LeadSourceProvider] = {}

    def register(self, provider: LeadSourceProvider) -> None:
        """Register a provider instance under its canonical name."""
        self._providers[provider.provider_name] = provider

    def get(self, name: str) -> LeadSourceProvider:
        """
        Return the provider with the given name.

        Raises:
            ProviderError: If the name is not registered.
        """
        provider = self._providers.get(name)
        if provider is None:
            available = sorted(self._providers.keys())
            raise ProviderError(
                name,
                f"Unknown provider {name!r}. Available providers: {available}",
            )
        return provider

    def list_providers(self) -> list[str]:
        """Return sorted list of all registered provider names."""
        return sorted(self._providers.keys())


def _build_default_registry() -> ProviderRegistry:
    """Build and return the default registry with all built-in providers."""
    registry = ProviderRegistry()
    registry.register(MockApolloProvider())
    registry.register(MockScoutProvider())
    # Future providers registered here:
    # registry.register(ApolloProvider())
    # registry.register(ScoutProvider())
    # registry.register(WebSearchProvider())
    return registry


# Module-level singleton — import and use directly
provider_registry: ProviderRegistry = _build_default_registry()
