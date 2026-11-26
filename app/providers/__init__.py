"""
Unified provider layer: registry, config loader, selection and runtime signals.

    registry = get_provider_registry()   # loaded lazily from GTM config
    selector = get_provider_selector()   # health, rate limits, reliability stats
"""

from __future__ import annotations

from typing import Optional

from app.providers.registry import ProviderRecord, ProviderRegistry
from app.providers.selection import (
    Candidate,
    ProviderSelector,
    Rejection,
    SelectionRequest,
    SelectionResult,
)

_registry: Optional[ProviderRegistry] = None
_selector: Optional[ProviderSelector] = None


def _load(registry: ProviderRegistry) -> None:
    from app.core.gtm_config import get_gtm_config
    from app.providers.loader import load_providers_from_config
    load_providers_from_config(registry, get_gtm_config())


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry(loader=_load)
    return _registry


def get_provider_selector() -> ProviderSelector:
    global _selector
    if _selector is None:
        from app.core.gtm_config import get_gtm_config
        from app.providers.runtime import EventProviderStats, build_runtime_state
        from app.revops.insights import OutcomeQualityStats
        cfg = get_gtm_config()
        health, limiter = build_runtime_state(cfg)
        _selector = ProviderSelector(
            get_provider_registry(),
            stats=EventProviderStats(ttl_s=cfg.selection.stats_cache_ttl_s),
            health=health,
            rate_limiter=limiter,
            outcomes=OutcomeQualityStats(),
        )
    return _selector


def reload_providers() -> None:
    """Reload config + providers in place and reset runtime state (tests, admin reload)."""
    global _selector
    from app.core.gtm_config import reload_gtm_config
    reload_gtm_config()
    get_provider_registry().reload()
    _selector = None


__all__ = [
    "Candidate",
    "ProviderRecord",
    "ProviderRegistry",
    "ProviderSelector",
    "Rejection",
    "SelectionRequest",
    "SelectionResult",
    "get_provider_registry",
    "get_provider_selector",
    "reload_providers",
]
