"""Unified capability catalogue (vendor-neutral action contracts)."""

from app.capabilities.catalogue import CapabilityCatalogue, get_capability_catalogue
from app.capabilities.spec import CapabilitySpec, ParameterValidation

__all__ = [
    "CapabilityCatalogue",
    "CapabilitySpec",
    "ParameterValidation",
    "get_capability_catalogue",
]
