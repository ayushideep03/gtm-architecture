"""
Vendor-neutral LLM interface for structured (JSON-schema) generation.

Oxygen never talks to a vendor SDK directly; it asks the LLMRouter for a JSON
object matching a schema under a named profile (e.g. "planner"). Profiles map
to vendor + model in config, with fallback profiles, so switching OpenAI <->
Anthropic (or adding another vendor) is a config change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class LLMError(Exception):
    """A call failed. ``retryable`` failures may succeed on another profile/vendor."""

    def __init__(self, message: str, *, retryable: bool = True, vendor: Optional[str] = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.vendor = vendor


class LLMUnavailable(LLMError):
    """No usable vendor (e.g. API key not configured)."""

    def __init__(self, message: str, vendor: Optional[str] = None) -> None:
        super().__init__(message, retryable=True, vendor=vendor)


class LLMRefusal(LLMError):
    """The model declined the request."""

    def __init__(self, message: str, vendor: Optional[str] = None) -> None:
        super().__init__(message, retryable=True, vendor=vendor)


@dataclass
class LLMResult:
    data: dict[str, Any]
    vendor: str
    model: str
    profile: str
    latency_ms: float
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)   # profiles tried before this one


class JSONCompletionClient(Protocol):
    """One vendor adapter."""

    vendor: str

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        timeout_s: float,
        temperature: Optional[float],
        effort: Optional[str],
        refusal_fallbacks: Optional[str],
        max_retries: int,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Return (parsed JSON object, usage dict, model actually used)."""
        ...
