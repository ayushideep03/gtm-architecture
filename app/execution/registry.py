"""Execution Registry managing provider lookup and supported task types."""

from typing import Optional
from app.core.logging import get_logger
from app.execution.interfaces import ExecutionProvider

logger = get_logger(__name__)


class ExecutionRegistry:
    """Registry mapping task types to responsible ExecutionProviders."""

    def __init__(self) -> None:
        self._providers: dict[str, ExecutionProvider] = {}
        self._task_type_to_provider: dict[str, str] = {}

    def register(self, provider: ExecutionProvider) -> None:
        """Register an execution provider and index its supported task types."""
        name = provider.provider_name
        self._providers[name] = provider
        for tt in provider.supported_task_types:
            self._task_type_to_provider[tt] = name
            logger.info("Registered provider '%s' for task type '%s'", name, tt)

    def unregister(self, provider_name: str) -> None:
        """Remove a provider and its task mappings."""
        if provider_name in self._providers:
            del self._providers[provider_name]
            to_remove = [
                tt
                for tt, pname in self._task_type_to_provider.items()
                if pname == provider_name
            ]
            for tt in to_remove:
                del self._task_type_to_provider[tt]
            logger.info("Unregistered provider '%s'", provider_name)

    def get_provider(self, provider_name: str) -> Optional[ExecutionProvider]:
        """Look up provider by unique name."""
        return self._providers.get(provider_name)

    def get_provider_for_task_type(self, task_type: str) -> Optional[ExecutionProvider]:
        """Look up provider responsible for a given task type."""
        provider_name = self._task_type_to_provider.get(task_type)
        if provider_name:
            return self._providers.get(provider_name)
        return None

    def list_providers(self) -> list[ExecutionProvider]:
        """List all registered providers sorted by name."""
        return sorted(list(self._providers.values()), key=lambda p: p.provider_name)

    def is_executable(self, task_type: str) -> bool:
        """Return True if a provider exists for the specified task type."""
        return task_type in self._task_type_to_provider


# Global singleton registry instance
execution_registry = ExecutionRegistry()
