"""
Unified provider registry.

One registry for every integration: lead sources, enrichment vendors, research,
LLM-backed writers, email, calendar, CRM. Each provider serves one or more
capabilities from the catalogue, and several providers may serve the same
capability. Which one runs is decided per call by the ProviderSelector.

Providers are registered from configuration (see app/providers/loader.py), never
as an import side effect. Tests and plugins may also register instances directly.

The legacy ExecutionRegistry API (get_provider_for_task_type, is_executable,
list_providers, get_provider) is preserved on this class so existing callers keep
working; ``app.execution.registry.ExecutionRegistry`` is now an alias of it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from app.capabilities.catalogue import CapabilityCatalogue, get_capability_catalogue
from app.core.gtm_config import ProviderConfig
from app.core.logging import get_logger
from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionContext, ExecutionResult

logger = get_logger(__name__)


@dataclass
class ProviderRecord:
    """A registered provider and the static facts about it."""

    name: str
    instance: Any
    capabilities: list[str]
    config: ProviderConfig
    available: bool = True
    unavailable_reason: Optional[str] = None
    _bindings: dict[str, ExecutionProvider] = field(default_factory=dict, repr=False)

    def binding(self, capability: str, catalogue: CapabilityCatalogue) -> Optional[ExecutionProvider]:
        """An ExecutionProvider that runs `capability` on this provider."""
        if self.instance is None:
            return None
        if isinstance(self.instance, ExecutionProvider):
            return self.instance
        if capability not in self._bindings:
            spec = catalogue.get(capability)
            if spec is None or spec.invoker is None:
                return None
            self._bindings[capability] = _InvokerBinding(self.name, self.instance, spec.invoker, capability)
        return self._bindings[capability]


class _InvokerBinding(ExecutionProvider):
    """Adapts a vendor-interface provider to the ExecutionProvider contract."""

    def __init__(self, name: str, instance: Any, invoker: Callable[..., Any], capability: str) -> None:
        self._name = name
        self._instance = instance
        self._invoker = invoker
        self._capability = capability

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def supported_task_types(self) -> list[str]:
        return [self._capability]

    async def execute(self, context: ExecutionContext, session=None) -> ExecutionResult:
        return await self._invoker(self._instance, context, session)


class ProviderRegistry:
    def __init__(
        self,
        catalogue: Optional[CapabilityCatalogue] = None,
        loader: Optional[Callable[["ProviderRegistry"], None]] = None,
    ) -> None:
        self._catalogue = catalogue
        self._loader = loader
        self._loaded = loader is None
        self._lock = threading.RLock()
        self._records: dict[str, ProviderRecord] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def catalogue(self) -> CapabilityCatalogue:
        return self._catalogue or get_capability_catalogue()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self._loaded = True  # set first: loader calls register()
                assert self._loader is not None
                self._loader(self)

    def reload(self) -> None:
        """Drop all providers and re-run the loader (e.g. after a config reload)."""
        with self._lock:
            self._records.clear()
            self._loaded = self._loader is None
        self._ensure_loaded()

    # ── registration ─────────────────────────────────────────────────────────
    def _infer_capabilities(self, instance: Any) -> list[str]:
        caps: list[str] = list(getattr(instance, "supported_task_types", None) or [])
        if not caps:
            for spec in self.catalogue.specs():
                vendor_ifaces = [i for i in spec.provider_interfaces if i is not ExecutionProvider]
                if any(isinstance(instance, iface) for iface in vendor_ifaces):
                    caps.append(spec.name)
        return caps

    def register(
        self,
        provider: Any,
        *,
        config: Optional[ProviderConfig] = None,
        name: Optional[str] = None,
        capabilities: Optional[Iterable[str]] = None,
        available: bool = True,
        unavailable_reason: Optional[str] = None,
    ) -> ProviderRecord:
        self._ensure_loaded()
        pname = name or (config.name if config else None) or getattr(provider, "provider_name", None)
        if not pname:
            raise ValueError("provider needs a name (config.name, name= or .provider_name)")
        raw_caps = list(capabilities or (config.capabilities if config else None) or self._infer_capabilities(provider))
        caps = [self.catalogue.canonical(c) or c for c in raw_caps]
        cfg = config or ProviderConfig(name=pname, factory=f"{type(provider).__module__}:{type(provider).__name__}", capabilities=caps)

        record = ProviderRecord(
            name=pname, instance=provider, capabilities=caps, config=cfg,
            available=available and provider is not None, unavailable_reason=unavailable_reason,
        )
        if record.available:
            problem = self._interface_problem(record)
            if problem:
                record.available, record.unavailable_reason = False, problem
                logger.error("Provider %r unavailable: %s", pname, problem)
        with self._lock:
            self._records[pname] = record
        for cap in caps:
            logger.info("Registered provider %r for capability %r", pname, cap)
        return record

    def _interface_problem(self, record: ProviderRecord) -> Optional[str]:
        for cap in record.capabilities:
            spec = self.catalogue.get(cap)
            if spec is None:
                continue  # ad-hoc capability (not in catalogue): executable, not plannable
            if spec.provider_interfaces and not any(isinstance(record.instance, i) for i in spec.provider_interfaces):
                expected = ", ".join(i.__name__ for i in spec.provider_interfaces)
                return f"does not implement {expected} required by capability {cap!r}"
        return None

    def unregister(self, provider_name: str) -> None:
        self._ensure_loaded()
        with self._lock:
            if self._records.pop(provider_name, None) is not None:
                logger.info("Unregistered provider %r", provider_name)

    # ── queries ──────────────────────────────────────────────────────────────
    def record(self, name: str) -> Optional[ProviderRecord]:
        self._ensure_loaded()
        return self._records.get(name)

    def records(self) -> list[ProviderRecord]:
        self._ensure_loaded()
        return sorted(self._records.values(), key=lambda r: r.name)

    def candidates(self, capability: str) -> list[ProviderRecord]:
        """All providers registered for a capability (alias-aware), available or not."""
        self._ensure_loaded()
        canonical = self.catalogue.canonical(capability) or capability
        return [r for r in self.records() if canonical in r.capabilities]

    def capabilities(self) -> list[str]:
        return sorted({c for r in self.records() for c in r.capabilities})

    # ── legacy ExecutionRegistry API ─────────────────────────────────────────
    def get_provider(self, provider_name: str) -> Optional[Any]:
        record = self.record(provider_name)
        return record.instance if record else None

    def get_provider_for_task_type(self, task_type: str) -> Optional[ExecutionProvider]:
        """Highest-priority available provider (no runtime health/stats; see ProviderSelector)."""
        available = [r for r in self.candidates(task_type) if r.available]
        if not available:
            return None
        best = sorted(available, key=lambda r: (-r.config.priority, r.name))[0]
        canonical = self.catalogue.canonical(task_type) or task_type
        return best.binding(canonical, self.catalogue)

    def is_executable(self, task_type: str) -> bool:
        return any(r.available for r in self.candidates(task_type))

    def list_providers(self) -> list[Any]:
        return [r.instance for r in self.records() if r.instance is not None]
