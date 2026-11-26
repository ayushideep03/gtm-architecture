"""
Capability catalogue: the single source of truth for what actions exist.

Resolves canonical names and aliases, applies config policy (enable/disable,
descriptions, repeatability) per tenant, and validates parameters.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from app.capabilities.spec import CapabilitySpec
from app.core.gtm_config import GTMConfig, get_gtm_config


class CapabilityCatalogue:
    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._aliases: dict[str, str] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        if spec.name in self._specs or spec.name in self._aliases:
            raise ValueError(f"capability {spec.name!r} is already registered")
        for alias in spec.aliases:
            if alias in self._specs or alias in self._aliases:
                raise ValueError(f"capability alias {alias!r} clashes with an existing name")
        self._specs[spec.name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = spec.name

    # ── lookup ───────────────────────────────────────────────────────────────
    def canonical(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        if name in self._specs:
            return name
        return self._aliases.get(name)

    def get(
        self,
        name: Optional[str],
        tenant_id: Optional[uuid.UUID] = None,
        config: Optional[GTMConfig] = None,
    ) -> Optional[CapabilitySpec]:
        """Return the spec (with config policy applied) or None if unknown."""
        canonical = self.canonical(name)
        if canonical is None:
            return None
        cfg = (config or get_gtm_config()).for_tenant(tenant_id)
        return self._specs[canonical].with_policy(cfg.capabilities.get(canonical))

    def is_enabled(
        self,
        name: Optional[str],
        tenant_id: Optional[uuid.UUID] = None,
        config: Optional[GTMConfig] = None,
    ) -> bool:
        canonical = self.canonical(name)
        if canonical is None:
            return False
        cfg = (config or get_gtm_config()).for_tenant(tenant_id)
        policy = cfg.capabilities.get(canonical)
        return policy.enabled if policy is not None else True

    def requires_approval(
        self,
        name: Optional[str],
        tenant_id: Optional[uuid.UUID] = None,
        config: Optional[GTMConfig] = None,
    ) -> bool:
        """Approval need: explicit capability policy, else the side-effect policy."""
        spec = self.get(name, tenant_id, config)
        if spec is None:
            return True  # unknown actions are never auto-approved
        cfg = (config or get_gtm_config()).for_tenant(tenant_id)
        policy = cfg.capabilities.get(spec.name)
        if policy is not None and policy.requires_approval is not None:
            return policy.requires_approval
        return spec.side_effect in cfg.approval.required_for_side_effects

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[CapabilitySpec]:
        return [self._specs[n] for n in self.names()]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.canonical(name) is not None


_catalogue: Optional[CapabilityCatalogue] = None


def get_capability_catalogue() -> CapabilityCatalogue:
    """Process-wide catalogue of built-in capabilities (lazy to avoid import cycles)."""
    global _catalogue
    if _catalogue is None:
        from app.capabilities.definitions import BUILTIN_CAPABILITIES
        _catalogue = CapabilityCatalogue(BUILTIN_CAPABILITIES)
    return _catalogue
