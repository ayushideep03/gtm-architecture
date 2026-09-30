"""API routes for the Execution Layer.

Endpoints:
    POST /api/v1/execution/tasks/{task_id}/execute — execute task via TaskExecutor
    GET  /api/v1/execution/providers               — list registered execution providers
    GET  /api/v1/execution/tasks/{task_id}         — fetch task status and details
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
    TaskDetailResponse,
)
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
    description="Returns all registered execution providers and their supported task types.",
)
async def list_providers_route() -> ExecutionProviderListResponse:
    """List all registered execution providers."""
    providers = execution_registry.list_providers()
    return ExecutionProviderListResponse(
        count=len(providers),
        providers=[
            ExecutionProviderResponse(
                provider_name=p.provider_name,
                supported_task_types=p.supported_task_types,
            )
            for p in providers
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
    )
