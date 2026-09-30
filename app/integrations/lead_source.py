"""
Lead source provider interface.

All lead source providers MUST implement LeadSourceProvider.

Design principles:
- The ingestion service depends ONLY on this interface.
- Apollo, Scout, WebSearch, Manual import, etc. are all concrete
  implementations of this single interface.
- Adding a new provider requires zero changes to the ingestion service.

Usage pattern::

    provider = registry.get("mock_apollo")
    records: list[LeadSourceRecord] = await provider.search_leads(criteria)
    await ingestion_service.ingest(records)
"""

from abc import ABC, abstractmethod
from typing import Any

from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord


class LeadSourceProvider(ABC):
    """
    Abstract base class for all lead source providers.

    Every provider — whether it wraps a real API (Apollo, Scout) or is a
    mock — must implement this interface. The rest of the application
    must never depend on provider-specific details.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Canonical name of this provider.

        Used for:
        - Source tracking on Lead and Event records
        - Logging and debugging
        - Provider registry keys

        Examples: "mock_apollo", "apollo", "scout", "mock_scout", "manual"
        """
        ...

    @abstractmethod
    async def search_leads(self, criteria: LeadSearchCriteria) -> list[LeadSourceRecord]:
        """
        Search for leads matching the given criteria.

        Args:
            criteria: Flexible search parameters. Providers should handle
                      the fields they support and ignore the rest.

        Returns:
            A list of normalized LeadSourceRecord objects. The list may be
            empty if no results match. Providers MUST NOT raise for
            empty results — only for genuine errors.

        Raises:
            ProviderError: On any provider-side failure (network, auth, etc.).
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """
        Optional: verify provider connectivity.

        Returns a dict with at least {"status": "ok"} or {"status": "error"}.
        Default implementation returns "ok" — override for real providers.
        """
        return {"status": "ok", "provider": self.provider_name}


class ProviderError(Exception):
    """
    Raised when a provider fails to return results.

    Callers should catch this and return an appropriate HTTP error
    rather than letting it propagate as a 500.
    """

    def __init__(self, provider_name: str, message: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"[{provider_name}] {message}")
