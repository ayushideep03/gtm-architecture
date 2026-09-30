"""Task Executor coordinating lifecycle transitions, execution, persistence, and events."""

from datetime import datetime, timezone
import json
from typing import Any, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.execution.models import (
    ConcurrentExecutionError,
    ExecutionContext,
    ExecutionOutcome,
    ExecutionResult,
    InvalidStateTransitionError,
    TaskNotFoundError,
    TaskStatus,
    validate_transition,
)
from app.execution.registry import ExecutionRegistry, execution_registry
from app.models.base import Event, Task

logger = get_logger(__name__)


class TaskExecutor:
    """Coordinates execution of a single Task against registered providers."""

    def __init__(self, registry: Optional[ExecutionRegistry] = None) -> None:
        self.registry = registry or execution_registry

    async def execute(
        self,
        task_id: uuid.UUID,
        session: AsyncSession,
    ) -> ExecutionOutcome:
        """Execute a single task with DB row locking and idempotent lifecycle transitions.

        Args:
            task_id: UUID of the task to execute.
            session: Active AsyncSession with transaction.

        Returns:
            ExecutionOutcome containing task status, result, and emitted event type.
        """
        # 1. Load Task with row-level lock (with_for_update)
        stmt = select(Task).where(Task.id == task_id).with_for_update()
        res = await session.execute(stmt)
        task = res.scalar_one_or_none()

        if task is None:
            raise TaskNotFoundError(f"Task '{task_id}' not found")

        # 2. Check current status & validate state transitions
        current_status = task.status

        # If already completed or done -> Idempotent return (do not re-execute provider)
        if current_status in (TaskStatus.COMPLETED.value, TaskStatus.DONE.value):
            logger.info("Task [%s] already completed; returning cached result.", task_id)
            cached_result = {}
            if task.result:
                try:
                    cached_result = json.loads(task.result)
                except Exception:
                    cached_result = {"raw": task.result}

            return ExecutionOutcome(
                task_id=task.id,
                success=True,
                task_status=current_status,
                result=cached_result,
                event_type="task_completed",
                already_executed=True,
            )

        # If in_progress -> Concurrent execution attempt
        if current_status == TaskStatus.IN_PROGRESS.value:
            raise ConcurrentExecutionError(
                f"Task '{task_id}' is already currently in progress"
            )

        # If failed -> Rejection (explicit retry policy required)
        if current_status == TaskStatus.FAILED.value:
            raise InvalidStateTransitionError(
                f"Task '{task_id}' is in 'failed' status and cannot transition without an explicit retry"
            )

        # Check allowed transition to in_progress
        if not validate_transition(current_status, TaskStatus.IN_PROGRESS.value):
            raise InvalidStateTransitionError(
                f"Cannot transition task from '{current_status}' to '{TaskStatus.IN_PROGRESS.value}'"
            )

        # 3. Resolve execution provider
        provider = self.registry.get_provider_for_task_type(task.task_type)
        if not provider:
            # Safe rejection of unknown task types
            logger.warning(
                "Task [%s] has unknown/unexecutable task_type: %s",
                task_id,
                task.task_type,
            )
            err_msg = f"Unknown task type '{task.task_type}' has no registered provider"
            task.status = TaskStatus.FAILED.value
            task.error_message = err_msg
            task.result = json.dumps({"error": err_msg, "code": "UNKNOWN_TASK_TYPE"})
            task.completed_at = datetime.now(timezone.utc)

            fail_event = Event(
                lead_id=task.lead_id,
                event_type="task_failed",
                source="task_executor",
                payload=json.dumps({
                    "task_id": str(task.id),
                    "lead_id": str(task.lead_id) if task.lead_id else None,
                    "task_type": task.task_type,
                    "provider": None,
                    "success": False,
                    "error": {"message": err_msg, "code": "UNKNOWN_TASK_TYPE"},
                }),
            )
            session.add(fail_event)
            await session.commit()

            return ExecutionOutcome(
                task_id=task.id,
                success=False,
                task_status=TaskStatus.FAILED.value,
                result={},
                event_type="task_failed",
                error_message=err_msg,
            )

        # 4. Mark Task in_progress and emit task_started event
        task.status = TaskStatus.IN_PROGRESS.value
        task.updated_at = datetime.now(timezone.utc)

        start_event = Event(
            lead_id=task.lead_id,
            event_type="task_started",
            source="task_executor",
            payload=json.dumps({
                "task_id": str(task.id),
                "lead_id": str(task.lead_id) if task.lead_id else None,
                "task_type": task.task_type,
                "provider": provider.provider_name,
            }),
        )
        session.add(start_event)
        await session.flush()

        # 5. Build ExecutionContext from task and payload
        payload_data: dict[str, Any] = {}
        if task.payload:
            try:
                payload_data = json.loads(task.payload)
            except Exception:
                payload_data = {"raw_payload": task.payload}

        context = ExecutionContext(
            task_id=task.id,
            task_type=task.task_type,
            lead_id=task.lead_id,
            payload=payload_data,
        )

        # 6. Execute Provider
        try:
            exec_result = await provider.execute(context, session=session)
        except Exception as exc:
            logger.exception(
                "Execution provider '%s' raised unhandled exception on task [%s]: %s",
                provider.provider_name,
                task_id,
                exc,
            )
            exec_result = ExecutionResult(
                success=False,
                status="failed",
                provider=provider.provider_name,
                error_code="PROVIDER_EXCEPTION",
                error_message=str(exc),
            )

        # 7. Apply result, finalize status, and emit completion/failure event
        now = datetime.now(timezone.utc)
        task.completed_at = now
        task.updated_at = now

        if exec_result.success:
            task.status = TaskStatus.COMPLETED.value
            task.result = json.dumps(exec_result.result, default=str)
            task.error_message = None

            completed_event = Event(
                lead_id=task.lead_id,
                event_type="task_completed",
                source=provider.provider_name,
                payload=json.dumps({
                    "task_id": str(task.id),
                    "lead_id": str(task.lead_id) if task.lead_id else None,
                    "task_type": task.task_type,
                    "provider": provider.provider_name,
                    "success": True,
                    "result": exec_result.result,
                }),
            )
            session.add(completed_event)
            event_type = "task_completed"
        else:
            task.status = TaskStatus.FAILED.value
            task.error_message = exec_result.error_message or "Execution failed"
            task.result = json.dumps(
                {
                    "error": exec_result.error_message,
                    "code": exec_result.error_code,
                },
                default=str,
            )

            failed_event = Event(
                lead_id=task.lead_id,
                event_type="task_failed",
                source=provider.provider_name,
                payload=json.dumps({
                    "task_id": str(task.id),
                    "lead_id": str(task.lead_id) if task.lead_id else None,
                    "task_type": task.task_type,
                    "provider": provider.provider_name,
                    "success": False,
                    "error": {
                        "message": exec_result.error_message,
                        "code": exec_result.error_code,
                    },
                }),
            )
            session.add(failed_event)
            event_type = "task_failed"

        # 8. Commit transaction safely
        await session.commit()

        return ExecutionOutcome(
            task_id=task.id,
            success=exec_result.success,
            task_status=task.status,
            result=exec_result.result if exec_result.success else {},
            event_type=event_type,
            error_message=task.error_message,
        )
