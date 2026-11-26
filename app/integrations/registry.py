"""
Provider registry.

Maps provider name strings → LeadSourceProvider instances.

Rules:
- The ingestion service and API routes call registry.get(name).
- They never import concrete provider classes directly.
- Adding a new provider: add it to the GTM config (config/gtm*.yaml). No code changes.

Usage::

    from app.integrations.registry import provider_registry
    provider = provider_registry.get("mock_apollo")
    records = await provider.search_leads(criteria)
"""

from app.integrations.lead_source import LeadSourceProvider, ProviderError


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


class UnifiedLeadSourceRegistry:
    """
    Lead-source view over the unified provider registry (capability ``find_leads``).

    Same interface as ProviderRegistry, but providers come from configuration
    instead of being hardcoded here. Mock sources are only present when a
    development/test profile enables them.
    """

    CAPABILITY = "find_leads"

    def _unified(self):
        from app.providers import get_provider_registry
        return get_provider_registry()

    def register(self, provider: LeadSourceProvider) -> None:
        self._unified().register(provider, capabilities=[self.CAPABILITY])

    def get(self, name: str) -> LeadSourceProvider:
        record = self._unified().record(name)
        if record is None or self.CAPABILITY not in record.capabilities:
            raise ProviderError(
                name, f"Unknown provider {name!r}. Available providers: {self.list_providers()}"
            )
        if not record.available:
            raise ProviderError(name, f"Provider {name!r} is unavailable: {record.unavailable_reason}")
        return record.instance

    def list_providers(self) -> list[str]:
        return sorted(r.name for r in self._unified().candidates(self.CAPABILITY) if r.available)


# Singleton used by the ingestion route and services.
provider_registry = UnifiedLeadSourceRegistry()
