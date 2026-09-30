"""Execution layer coordinating tasks, providers, and lifecycle transitions."""

from app.execution.executor import TaskExecutor
from app.execution.interfaces import ExecutionProvider
from app.execution.models import (
    ConcurrentExecutionError,
    ExecutionContext,
    ExecutionError,
    ExecutionOutcome,
    ExecutionResult,
    InvalidStateTransitionError,
    ProviderNotFoundError,
    TaskNotFoundError,
    TaskStatus,
    UnknownTaskTypeError,
    validate_transition,
)
from app.execution.registry import ExecutionRegistry, execution_registry
from app.execution.workers import (
    CompanyEnrichmentExecutionAdapter,
    MockLeadQualificationProvider,
    PersonEnrichmentExecutionAdapter,
    execute_task,
)

__all__ = [
    "TaskExecutor",
    "ExecutionProvider",
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionOutcome",
    "TaskStatus",
    "ExecutionError",
    "TaskNotFoundError",
    "InvalidStateTransitionError",
    "UnknownTaskTypeError",
    "ProviderNotFoundError",
    "ConcurrentExecutionError",
    "validate_transition",
    "ExecutionRegistry",
    "execution_registry",
    "MockLeadQualificationProvider",
    "CompanyEnrichmentExecutionAdapter",
    "PersonEnrichmentExecutionAdapter",
    "execute_task",
]
