"""
Enrichment provider interfaces.

All enrichment providers MUST implement CompanyEnrichmentProvider or
PersonEnrichmentProvider as appropriate.

Design principles:
- Services depend only on these interfaces, never on concrete vendors.
- Adding a new enrichment provider requires zero changes to the service layer.
- Providers accept normalized identifiers (domain for companies, email for people).
- Providers return structured EnrichmentResult objects (defined in enrichment_record.py).

Usage pattern::

    provider = enrichment_registry.get_company_provider("mock_enrichment")
    result = await provider.enrich_company(domain="quantum.io")
    if result.found:
        # apply result.data to Company record
"""

from abc import ABC, abstractmethod
from typing import Any

from app.integrations.enrichment_record import (
    CompanyEnrichmentResult,
    PersonEnrichmentResult,
)


class CompanyEnrichmentProvider(ABC):
    """
    Abstract base class for company enrichment providers.

    A provider accepts a normalized company domain and returns structured
    enrichment data. Providers MUST NOT modify the database directly —
    they only return data; the enrichment service persists it.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Canonical name of this provider.

        Examples: "mock_enrichment", "clearbit", "apollo_enrichment"
        """
        ...

    @abstractmethod
    async def enrich_company(self, domain: str) -> CompanyEnrichmentResult:
        """
        Attempt to enrich a company identified by its normalized domain.

        Args:
            domain: Normalized company domain (e.g. "quantum.io").

        Returns:
            CompanyEnrichmentResult — always returned even when not found.
            Check result.found to determine whether data is available.

        Raises:
            EnrichmentProviderError: On provider-side failures only.
                An unknown domain MUST return result.found=False, not raise.
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """Optional connectivity check. Default returns ok."""
        return {"status": "ok", "provider": self.provider_name}


class PersonEnrichmentProvider(ABC):
    """
    Abstract base class for person enrichment providers.

    A provider accepts a normalized email address and returns structured
    enrichment data. Providers MUST NOT modify the database directly.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Canonical name of this provider."""
        ...

    @abstractmethod
    async def enrich_person(self, email: str) -> PersonEnrichmentResult:
        """
        Attempt to enrich a person identified by their normalized email.

        Args:
            email: Normalized email address (e.g. "alice.chen@quantum.io").

        Returns:
            PersonEnrichmentResult — always returned even when not found.
            Check result.found to determine whether data is available.

        Raises:
            EnrichmentProviderError: On provider-side failures only.
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """Optional connectivity check. Default returns ok."""
        return {"status": "ok", "provider": self.provider_name}


class EnrichmentProviderError(Exception):
    """
    Raised when an enrichment provider encounters a non-recoverable failure.

    Not-found results MUST be returned as EnrichmentResult(found=False),
    not raised as this exception.
    """

    def __init__(self, provider_name: str, message: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"[{provider_name}] {message}")
