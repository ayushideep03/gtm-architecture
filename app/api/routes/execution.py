"""API routes for the Execution Layer.

Endpoints:
    POST /api/v1/execution/tasks/{task_id}/execute — execute task via TaskExecutor
    GET  /api/v1/execution/providers               — list registered execution providers
    GET  /api/v1/execution/tasks/{task_id}         — fetch task status and details
    POST /api/v1/execution/tasks/{task_id}/retry   — explicitly re-queue a failed task (audited)
    POST /api/v1/execution/recover-stale           — requeue / flag tasks whose lease expired
"""

import json
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.execution import (
    ExecutionOutcomeResponse,
    ExecutionProviderListResponse,
    ExecutionProviderResponse,
    StaleRecoveryResponse,
    TaskDetailResponse,
    TaskRetryRequest,
)
from app.execution.recovery import RecoveryRefused, recover_stale_tasks, retry_failed_task
from app.core.database import get_db
from app.execution.executor import TaskExecutor
from app.execution.models import (
    ConcurrentExecutionError,
    InvalidStateTransitionError,
    TaskNotFoundError,
)
from app.execution.registry import execution_registry
from app.models.base import Task

router = APIRouter(prefix="/execution", tags=["execution"])
_executor = TaskExecutor(execution_registry)


@router.post(
    "/tasks/{task_id}/execute",
    response_model=ExecutionOutcomeResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute a task",
    description="Loads the task, transitions it through in_progress, invokes the provider, and persists the result.",
)
async def execute_task_route(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExecutionOutcomeResponse:
    """Execute a task by UUID."""
    try:
        outcome = await _executor.execute(task_id, db)
    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ConcurrentExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return ExecutionOutcomeResponse(
        task_id=outcome.task_id,
        success=outcome.success,
        task_status=outcome.task_status,
        result=outcome.result,
        event_type=outcome.event_type,
        error_message=outcome.error_message,
        already_executed=outcome.already_executed,
    )


@router.get(
    "/providers",
    response_model=ExecutionProviderListResponse,
    status_code=status.HTTP_200_OK,
    summary="List execution providers",
    description="Returns every configured provider, the capabilities it serves, and whether it is usable.",
)
async def list_providers_route() -> ExecutionProviderListResponse:
    """List all providers in the unified registry (including unavailable ones, with reasons)."""
    records = execution_registry.records()
    return ExecutionProviderListResponse(
        count=len(records),
        providers=[
            ExecutionProviderResponse(
                provider_name=r.name,
                supported_task_types=r.capabilities,
                available=r.available,
                unavailable_reason=r.unavailable_reason,
                enabled=r.config.enabled,
                mode=r.config.mode,
                priority=r.config.priority,
                cost_per_call=r.config.cost_per_call,
                tags=r.config.tags,
            )
            for r in records
        ],
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task details",
    description="Fetch a task by ID including its current status, payload, result, and timestamps.",
)
async def get_task_route(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TaskDetailResponse:
    """Fetch task details by UUID."""
    stmt = select(Task).where(Task.id == task_id)
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    parsed_payload: dict[str, Any] | None = None
    if task.payload:
        try:
            parsed_payload = json.loads(task.payload)
        except Exception:
            parsed_payload = {"raw": task.payload}

    parsed_result: dict[str, Any] | None = None
    if task.result:
        try:
            parsed_result = json.loads(task.result)
        except Exception:
            parsed_result = {"raw": task.result}

    return TaskDetailResponse(
        id=task.id,
        lead_id=task.lead_id,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        payload=parsed_payload,
        result=parsed_result,
        error_message=task.error_message,
        due_at=task.due_at,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        next_attempt_at=task.next_attempt_at,
        last_error_code=task.last_error_code,
        needs_review=task.needs_review,
        idempotency_key=task.idempotency_key,
    )


@router.post(
    "/tasks/{task_id}/retry",
    response_model=TaskDetailResponse,
    summary="Re-queue a failed task",
    description="Moves a failed task back to pending (same row, same idempotency key). Tasks flagged "
                "needs_review require acknowledge_review=true after reconciling with the provider.",
)
async def retry_task_route(
    task_id: uuid.UUID, body: TaskRetryRequest, db: AsyncSession = Depends(get_db)
) -> TaskDetailResponse:
    try:
        await retry_failed_task(db, task_id, requested_by=body.requested_by, reason=body.reason,
                                acknowledge_review=body.acknowledge_review, extra_attempts=body.extra_attempts)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RecoveryRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await db.commit()
    return await get_task_route(task_id, db)


@router.post(
    "/recover-stale",
    response_model=StaleRecoveryResponse,
    summary="Recover tasks whose execution lease expired",
)
async def recover_stale_route(db: AsyncSession = Depends(get_db)) -> StaleRecoveryResponse:
    report = await recover_stale_tasks(db)
    await db.commit()
    return StaleRecoveryResponse(resumed=report.resumed, flagged_for_review=report.flagged_for_review)
