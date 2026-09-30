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


def _build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityContract(
            name="company_enrichment",
            description="Enriches company firmographic and technographic data using domain.",
            input_contract={"company_id": "UUID", "domain": "str"},
            is_available=True,
        )
    )
    registry.register(
        CapabilityContract(
            name="person_enrichment",
            description="Enriches person contact and professional title/seniority data using email.",
            input_contract={"person_id": "UUID", "email": "str"},
            is_available=True,
        )
    )
    registry.register(
        CapabilityContract(
            name="lead_enrichment",
            description="Coordinates company and person enrichment for a given lead.",
            input_contract={"lead_id": "UUID"},
            is_available=True,
        )
    )
    registry.register(
        CapabilityContract(
            name="lead_qualification",
            description="Analyzes enriched lead signals to determine ICP qualification and routing.",
            input_contract={"lead_id": "UUID"},
            is_available=True,
        )
    )
    return registry


capability_registry: CapabilityRegistry = _build_default_registry()
