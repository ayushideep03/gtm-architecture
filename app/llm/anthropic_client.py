"""
Anthropic adapter (official ``anthropic`` SDK).

Structured output via ``output_config.format`` (JSON schema), reasoning depth via
``output_config.effort`` (set explicitly: Claude Opus 5.5 defaults to medium),
no sampling parameters (rejected by current models), and server-side refusal
fallbacks (``fallbacks``, beta ``server-side-fallback-2026-07-01``) when the
profile enables them. A final ``stop_reason == "refusal"`` is surfaced as
LLMRefusal so the router can try the next profile.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import anthropic
from anthropic import AsyncAnthropic

from app.llm.base import LLMError, LLMRefusal

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicJSONClient:
    vendor = "anthropic"

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def complete_json(self, *, model, system, user, schema, schema_name, max_output_tokens, timeout_s,
                            temperature, effort, refusal_fallbacks, max_retries) -> tuple[dict, dict, str]:
        client = AsyncAnthropic(api_key=self._api_key, base_url=self._base_url, timeout=timeout_s,
                                max_retries=max_retries)
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        try:
            if refusal_fallbacks:
                response = await client.beta.messages.create(
                    **request, betas=[FALLBACK_BETA], fallbacks=refusal_fallbacks)
            else:
                response = await client.messages.create(**request)
        except anthropic.RateLimitError as exc:
            raise LLMError(f"Anthropic rate limited: {exc}", retryable=True, vendor=self.vendor) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMError(f"Anthropic credentials rejected: {exc}", retryable=True, vendor=self.vendor) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Anthropic rejected the request: {exc}", retryable=True, vendor=self.vendor) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc}", retryable=True, vendor=self.vendor) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic connection error: {exc}", retryable=True, vendor=self.vendor) from exc
        finally:
            await client.close()

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusal(f"Anthropic declined (category={category})", vendor=self.vendor)
        if response.stop_reason == "max_tokens":
            raise LLMError("Anthropic output truncated (max_tokens)", retryable=True, vendor=self.vendor)
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise LLMError("Anthropic returned no text block", retryable=True, vendor=self.vendor)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise LLMError("Anthropic returned invalid JSON", retryable=True, vendor=self.vendor) from exc
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        return data, usage, response.model
