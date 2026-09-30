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
from app.integrations.mock_enrichment import (
    MockCompanyEnrichmentProvider,
    MockPersonEnrichmentProvider,
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


def _build_default_enrichment_registry() -> EnrichmentRegistry:
    """Build and return the default enrichment registry."""
    registry = EnrichmentRegistry()
    mock = MockCompanyEnrichmentProvider()
    mock_person = MockPersonEnrichmentProvider()
    registry.register_company(mock)
    registry.register_person(mock_person)
    # Future real providers registered here:
    # registry.register_company(ClearbitCompanyProvider())
    # registry.register_person(ClearbitPersonProvider())
    return registry


# Module-level singleton
enrichment_registry: EnrichmentRegistry = _build_default_enrichment_registry()
