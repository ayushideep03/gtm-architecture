"""Interfaces for Guardrail rules."""

from abc import ABC, abstractmethod
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.guardrails.models import GuardrailContext, GuardrailRule


class GuardrailRuleEvaluator(ABC):
    """Abstract base class for a deterministic guardrail rule evaluator."""

    @property
    @abstractmethod
    def rule(self) -> GuardrailRule:
        """Metadata for this rule."""
        pass

    @abstractmethod
    async def evaluate(
        self,
        context: GuardrailContext,
        session: Optional[AsyncSession] = None,
    ) -> tuple[bool, Optional[str]]:
        """Evaluate rule against context.

        Returns:
            (allowed, failure_reason)
            If allowed is True, failure_reason is None.
            If allowed is False, failure_reason explains the block.
        """
        pass
