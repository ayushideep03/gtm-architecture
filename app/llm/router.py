"""
LLM router: resolve a named profile (planner, writer, ...) to a vendor + model
from config, call it, and fall back through ``fallback_profiles`` on failure.

Adding a vendor = implement JSONCompletionClient and register a factory here
(or pass ``clients=`` for tests). API keys come from the env var named in
``llm.vendors.<vendor>.api_key_env``; a missing key makes that vendor
unavailable rather than crashing.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from app.core.gtm_config import GTMConfig, get_gtm_config
from app.core.logging import get_logger
from app.llm.base import JSONCompletionClient, LLMError, LLMResult, LLMUnavailable

logger = get_logger(__name__)


def _openai_factory(vendor_cfg, api_key: str) -> JSONCompletionClient:
    from app.llm.openai_client import OpenAIJSONClient
    org = os.environ.get(vendor_cfg.organization_env) if vendor_cfg.organization_env else None
    return OpenAIJSONClient(api_key=api_key, base_url=vendor_cfg.base_url, organization=org)


def _anthropic_factory(vendor_cfg, api_key: str) -> JSONCompletionClient:
    from app.llm.anthropic_client import AnthropicJSONClient
    return AnthropicJSONClient(api_key=api_key, base_url=vendor_cfg.base_url)


def _demo_factory(vendor_cfg, api_key: str) -> JSONCompletionClient:
    from app.llm.demo_client import ScriptedDemoClient   # labelled scripted playbook, demo profile only
    return ScriptedDemoClient()


VENDOR_FACTORIES: dict[str, Callable[..., JSONCompletionClient]] = {
    "openai": _openai_factory,
    "anthropic": _anthropic_factory,
    "demo": _demo_factory,
}


class LLMRouter:
    def __init__(
        self,
        config_getter: Callable[[], GTMConfig] = get_gtm_config,
        clients: Optional[dict[str, JSONCompletionClient]] = None,
    ) -> None:
        self._config_getter = config_getter
        self._clients = dict(clients or {})   # injected clients (tests) bypass credentials

    def _client(self, vendor: str) -> JSONCompletionClient:
        if vendor in self._clients:
            return self._clients[vendor]
        cfg = self._config_getter().llm
        vendor_cfg = cfg.vendors.get(vendor)
        factory = VENDOR_FACTORIES.get(vendor)
        if vendor_cfg is None or factory is None:
            raise LLMUnavailable(f"LLM vendor {vendor!r} is not configured", vendor=vendor)
        key = os.environ.get(vendor_cfg.api_key_env or "", "")
        if vendor_cfg.api_key_env and not key:
            raise LLMUnavailable(f"{vendor_cfg.api_key_env} is not set", vendor=vendor)
        return factory(vendor_cfg, key)

    def status(self, profile: Optional[str] = None) -> dict[str, Any]:
        """Which profiles could run right now (without calling them)."""
        cfg = self._config_getter().llm
        chain = self._chain(profile or cfg.default_profile)
        rows = []
        for name in chain:
            p = cfg.profile(name)
            try:
                self._client(p.provider)
                rows.append({"profile": name, "vendor": p.provider, "model": p.model, "available": True})
            except LLMUnavailable as exc:
                rows.append({"profile": name, "vendor": p.provider, "model": p.model, "available": False,
                             "reason": str(exc)})
        return {"profiles": rows, "available": any(r["available"] for r in rows)}

    def _chain(self, profile: str) -> list[str]:
        cfg = self._config_getter().llm
        seen, order, queue = set(), [], [profile]
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            order.append(name)
            queue.extend(cfg.profile(name).fallback_profiles)
        return order

    async def complete_json(self, *, profile: str, system: str, user: str, schema: dict[str, Any],
                            schema_name: str) -> LLMResult:
        cfg = self._config_getter().llm
        attempts: list[dict[str, Any]] = []
        last: Optional[LLMError] = None
        for name in self._chain(profile):
            p = cfg.profile(name)
            started = time.perf_counter()
            try:
                client = self._client(p.provider)
                data, usage, model_used = await client.complete_json(
                    model=p.model, system=system, user=user, schema=schema, schema_name=schema_name,
                    max_output_tokens=p.max_output_tokens, timeout_s=p.timeout_s, temperature=p.temperature,
                    effort=p.effort, refusal_fallbacks=p.refusal_fallbacks, max_retries=p.max_retries,
                )
                return LLMResult(data=data, vendor=p.provider, model=model_used or p.model, profile=name,
                                 latency_ms=round((time.perf_counter() - started) * 1000, 1), usage=usage,
                                 attempts=attempts)
            except LLMError as exc:
                last = exc
                attempts.append({"profile": name, "vendor": p.provider, "model": p.model,
                                 "error": str(exc), "type": type(exc).__name__})
                logger.warning("LLM profile %r (%s/%s) failed: %s", name, p.provider, p.model, exc)
                if not exc.retryable:
                    break
        summary = "; ".join(f"{a['profile']}: {a['error']}" for a in attempts)
        if all(a["type"] == "LLMUnavailable" for a in attempts):
            raise LLMUnavailable(f"No LLM available ({summary})")
        raise LLMError(f"All LLM profiles failed ({summary})", retryable=False) from last
