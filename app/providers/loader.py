"""
Load providers into the registry from GTM configuration.

Every provider entry names a factory ("module:Class"). Construction:
    - Class.from_config(provider_config, credentials) if defined, else
    - Class(**settings, **credentials) if either is non-empty, else
    - Class()

A provider that fails to import/construct, or whose credentials are missing,
is still registered but marked unavailable with the reason, so it shows up in
diagnostics and is never selected.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from app.core.gtm_config import GTMConfig, ProviderConfig
from app.core.logging import get_logger
from app.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class ProviderUnavailable(Exception):
    """Raised by a provider factory when a known precondition is missing (e.g. no API key).
    Logged as a single line, not a traceback: this is configuration, not a bug."""


def import_factory(path: str) -> Any:
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"factory {path!r} must look like 'package.module:ClassName'")
    return getattr(import_module(module_name), attr)


def build_provider(pc: ProviderConfig) -> tuple[Any, str | None]:
    """Return (instance or None, reason-if-unavailable)."""
    credentials, missing = pc.resolve_credentials()
    if missing:
        return None, f"missing credentials (env): {', '.join(missing)}"
    try:
        factory = import_factory(pc.factory)
        if hasattr(factory, "from_config"):
            return factory.from_config(pc, credentials), None
        kwargs = {**pc.settings, **credentials}
        return (factory(**kwargs) if kwargs else factory()), None
    except ProviderUnavailable as exc:
        logger.info("Provider %r unavailable: %s", pc.name, exc)
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad provider must not break startup
        logger.exception("Failed to construct provider %r from %r", pc.name, pc.factory)
        return None, f"construction failed: {exc}"


def load_providers_from_config(registry: ProviderRegistry, config: GTMConfig) -> None:
    for pc in config.providers:
        instance, reason = build_provider(pc)
        declared = getattr(instance, "provider_name", None)
        if instance is not None and declared and declared != pc.name:
            logger.warning("Provider config name %r differs from instance name %r; using %r",
                           pc.name, declared, pc.name)
        registry.register(
            instance, config=pc, name=pc.name, capabilities=pc.capabilities,
            available=instance is not None, unavailable_reason=reason,
        )
    logger.info("Loaded %d provider(s) for profile %r", len(config.providers), config.profile)
