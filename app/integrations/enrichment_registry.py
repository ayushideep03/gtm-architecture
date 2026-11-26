"""
Enrichment provider registry.

Maps provider name strings to CompanyEnrichmentProvider and
PersonEnrichmentProvider instances.

A single provider name (e.g. "mock_enrichment") may implement both
CompanyEnrichmentProvider and PersonEnrichmentProvider; the registry
stores them in separate lookup tables.

Usage::

    from app.integrations.enrichment_registry import enrichment_registry
    co_provider = enrichment_registry.get_company_provider("mock_enrichment")
    result = await co_provider.enrich_company("quantum.io")
"""

from app.integrations.enrichment_provider import (
    CompanyEnrichmentProvider,
    EnrichmentProviderError,
    PersonEnrichmentProvider,
)


class EnrichmentRegistry:
    """
    Registry mapping provider names to enrichment provider instances.

    Company and person providers are stored in separate lookup tables
    because a provider may implement one but not the other.
    """

    def __init__(self) -> None:
        self._company: dict[str, CompanyEnrichmentProvider] = {}
        self._person: dict[str, PersonEnrichmentProvider] = {}

    def register_company(self, provider: CompanyEnrichmentProvider) -> None:
        """Register a company enrichment provider."""
        self._company[provider.provider_name] = provider

    def register_person(self, provider: PersonEnrichmentProvider) -> None:
        """Register a person enrichment provider."""
        self._person[provider.provider_name] = provider

    def get_company_provider(self, name: str) -> CompanyEnrichmentProvider:
        """
        Return the company enrichment provider with the given name.

        Raises:
            EnrichmentProviderError: If the name is not registered.
        """
        provider = self._company.get(name)
        if provider is None:
            available = sorted(self._company.keys())
            raise EnrichmentProviderError(
                name,
                f"Unknown company enrichment provider {name!r}. "
                f"Available: {available}",
            )
        return provider

    def get_person_provider(self, name: str) -> PersonEnrichmentProvider:
        """
        Return the person enrichment provider with the given name.

        Raises:
            EnrichmentProviderError: If the name is not registered.
        """
        provider = self._person.get(name)
        if provider is None:
            available = sorted(self._person.keys())
            raise EnrichmentProviderError(
                name,
                f"Unknown person enrichment provider {name!r}. "
                f"Available: {available}",
            )
        return provider

    def list_providers(self) -> dict[str, list[str]]:
        """Return all registered provider names by category."""
        return {
            "company": sorted(self._company.keys()),
            "person": sorted(self._person.keys()),
        }


class UnifiedEnrichmentRegistry:
    """
    Enrichment view over the unified provider registry (capabilities
    ``enrich_company`` / ``enrich_person``). Same lookup interface as
    EnrichmentRegistry; providers come from configuration.
    """

    def _unified(self):
        from app.providers import get_provider_registry
        return get_provider_registry()

    def _get(self, capability: str, name: str):
        record = self._unified().record(name)
        if record is None or capability not in record.capabilities:
            available = sorted(r.name for r in self._unified().candidates(capability) if r.available)
            raise EnrichmentProviderError(
                name, f"Unknown {capability} provider {name!r}. Available: {available}"
            )
        if not record.available:
            raise EnrichmentProviderError(name, f"Provider unavailable: {record.unavailable_reason}")
        return record.instance

    def register_company(self, provider: CompanyEnrichmentProvider) -> None:
        self._unified().register(provider, capabilities=["enrich_company"])

    def register_person(self, provider: PersonEnrichmentProvider) -> None:
        self._unified().register(provider, capabilities=["enrich_person"])

    def get_company_provider(self, name: str) -> CompanyEnrichmentProvider:
        return self._get("enrich_company", name)

    def get_person_provider(self, name: str) -> PersonEnrichmentProvider:
        return self._get("enrich_person", name)

    def list_providers(self) -> dict[str, list[str]]:
        reg = self._unified()
        return {
            "company": sorted(r.name for r in reg.candidates("enrich_company") if r.available),
            "person": sorted(r.name for r in reg.candidates("enrich_person") if r.available),
        }


# Singleton used by EnrichmentService and the enrichment API.
enrichment_registry = UnifiedEnrichmentRegistry()
