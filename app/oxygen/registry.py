"""
Capability / Tool Registry for Oxygen.

Maintains the catalog of capabilities Oxygen can orchestrate.
Decouples Oxygen from concrete provider implementations.
"""

from typing import Optional

from app.oxygen.interfaces import CapabilityContract


class CapabilityRegistry:
    """Registry holding known capability contracts for Oxygen."""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityContract] = {}

    def register(self, capability: CapabilityContract) -> None:
        self._capabilities[capability.name] = capability

    def get(self, name: str) -> Optional[CapabilityContract]:
        return self._capabilities.get(name)

    def is_available(self, name: str) -> bool:
        cap = self._capabilities.get(name)
        return cap.is_available if cap else False

    def list_capabilities(self) -> list[CapabilityContract]:
        return [self._capabilities[k] for k in sorted(self._capabilities.keys())]


class CatalogueCapabilityView:
    """
    Oxygen's view of capabilities, backed by the unified capability catalogue
    and the provider registry. A capability is *available* when it is enabled
    for the tenant and at least one provider for it is currently eligible
    (configured, constructed, healthy, within rate limits).

    Nothing is hand-registered here: capabilities come from the catalogue and
    availability from configuration + runtime provider state.
    """

    def _catalogue(self):
        from app.capabilities import get_capability_catalogue
        return get_capability_catalogue()

    def _selector(self):
        from app.providers import get_provider_selector
        return get_provider_selector()

    def register(self, capability: CapabilityContract) -> None:
        raise TypeError(
            "Capabilities are defined in app.capabilities (catalogue) and providers in "
            "GTM config; use a standalone CapabilityRegistry for ad-hoc contracts."
        )

    def canonical(self, name: Optional[str]) -> Optional[str]:
        return self._catalogue().canonical(name)

    async def is_available(self, name: str, tenant_id=None) -> bool:
        from app.providers.selection import SelectionRequest
        catalogue = self._catalogue()
        canonical = catalogue.canonical(name)
        if canonical is None or not catalogue.is_enabled(canonical, tenant_id):
            return False
        return await self._selector().has_eligible(SelectionRequest(capability=canonical, tenant_id=tenant_id))

    async def get(self, name: str, tenant_id=None) -> Optional[CapabilityContract]:
        spec = self._catalogue().get(name, tenant_id)
        if spec is None:
            return None
        return CapabilityContract(
            name=spec.name,
            description=spec.description,
            input_contract=spec.input_schema(),
            is_available=await self.is_available(spec.name, tenant_id),
        )

    async def list_capabilities(self, tenant_id=None) -> list[CapabilityContract]:
        contracts = [await self.get(n, tenant_id) for n in self._catalogue().names()]
        return [c for c in contracts if c]


capability_registry = CatalogueCapabilityView()
