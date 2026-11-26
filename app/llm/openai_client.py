"""OpenAI adapter (official ``openai`` SDK): strict JSON-schema responses via Chat Completions."""

from __future__ import annotations

import json
from typing import Any, Optional

import openai
from openai import AsyncOpenAI

from app.llm.base import LLMError, LLMRefusal


class OpenAIJSONClient:
    vendor = "openai"

    def __init__(self, api_key: str, base_url: Optional[str] = None, organization: Optional[str] = None) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._organization = organization

    def _client(self, timeout_s: float, max_retries: int) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, organization=self._organization,
                           timeout=timeout_s, max_retries=max_retries)

    async def complete_json(self, *, model, system, user, schema, schema_name, max_output_tokens, timeout_s,
                            temperature, effort, refusal_fallbacks, max_retries) -> tuple[dict, dict, str]:
        client = self._client(timeout_s, max_retries)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
            "max_completion_tokens": max_output_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            resp = await client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise LLMError(f"OpenAI rate limited: {exc}", retryable=True, vendor=self.vendor) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError(f"OpenAI credentials rejected: {exc}", retryable=True, vendor=self.vendor) from exc
        except openai.BadRequestError as exc:
            raise LLMError(f"OpenAI rejected the request: {exc}", retryable=True, vendor=self.vendor) from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc}", retryable=True, vendor=self.vendor) from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"OpenAI connection error: {exc}", retryable=True, vendor=self.vendor) from exc
        finally:
            await client.close()

        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMRefusal(f"OpenAI refused: {choice.message.refusal}", vendor=self.vendor)
        if choice.finish_reason == "length":
            raise LLMError("OpenAI output truncated (max tokens)", retryable=True, vendor=self.vendor)
        try:
            data = json.loads(choice.message.content or "")
        except ValueError as exc:
            raise LLMError("OpenAI returned invalid JSON", retryable=True, vendor=self.vendor) from exc
        usage = resp.usage.model_dump() if resp.usage else {}
        return data, usage, resp.model
