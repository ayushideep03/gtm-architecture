"""Provider-agnostic execution interfaces."""

from abc import ABC, abstractmethod
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import ExecutionContext, ExecutionResult


class ExecutionProvider(ABC):
    """Abstract base class for all execution providers and adapters."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'mock_lead_qualification')."""
        pass

    @property
    @abstractmethod
    def supported_task_types(self) -> list[str]:
        """List of task types this provider can handle (e.g. ['qualify_lead'])."""
        pass

    @abstractmethod
    async def execute(
        self,
        context: ExecutionContext,
        session: Optional[AsyncSession] = None,
    ) -> ExecutionResult:
        """Execute the task and return a standardized ExecutionResult.

        Args:
            context: Typed execution context containing task ID, payload, etc.
            session: Optional active database session for DB-backed operations.

        Returns:
            ExecutionResult describing success/failure, output data, or errors.
        """
        pass
