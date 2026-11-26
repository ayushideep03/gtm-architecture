"""Execution layer coordinating tasks, providers, and lifecycle transitions.

Exports are resolved lazily (PEP 562) so importing a submodule such as
``app.execution.interfaces`` does not pull in the executor, registry and
provider bootstrap. Providers are no longer registered as an import side
effect; they are loaded from configuration by ``app.providers``.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "TaskExecutor": "app.execution.executor",
    "ExecutionProvider": "app.execution.interfaces",
    "ExecutionContext": "app.execution.models",
    "ExecutionResult": "app.execution.models",
    "ExecutionOutcome": "app.execution.models",
    "TaskStatus": "app.execution.models",
    "ExecutionError": "app.execution.models",
    "TaskNotFoundError": "app.execution.models",
    "InvalidStateTransitionError": "app.execution.models",
    "UnknownTaskTypeError": "app.execution.models",
    "ProviderNotFoundError": "app.execution.models",
    "ConcurrentExecutionError": "app.execution.models",
    "validate_transition": "app.execution.models",
    "ExecutionRegistry": "app.execution.registry",
    "execution_registry": "app.execution.registry",
    "MockLeadQualificationProvider": "app.execution.workers",
    "CompanyEnrichmentExecutionAdapter": "app.execution.workers",
    "PersonEnrichmentExecutionAdapter": "app.execution.workers",
    "execute_task": "app.execution.workers",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'app.execution' has no attribute {name!r}")
    return getattr(import_module(module), name)
