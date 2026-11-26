"""
Task recovery.

Recovery always reuses the existing task row (same id, same idempotency key),
so recovering never creates duplicate records, and completed work is never
re-executed.

- retry_failed_task: explicit, audited retry of a failed task. Tasks flagged
  ``needs_review`` (external action with unknown outcome) require the caller to
  confirm they reconciled with the provider (``acknowledge_review=True``);
  otherwise the retry is refused, because it could repeat an email/booking.

- recover_stale_tasks: in_progress tasks whose lease expired (worker crashed or
  was killed). Capabilities whose side effect is listed in
  execution.recovery.auto_resume_side_effects go back to pending; anything else
  (e.g. send_email) is failed with needs_review for a human to reconcile.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities import get_capability_catalogue
from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.execution.models import InvalidStateTransitionError, TaskNotFoundError, TaskStatus, validate_transition
from app.models.base import Event, Task

logger = get_logger(__name__)


class RecoveryRefused(InvalidStateTransitionError):
    """Raised when recovering a task would risk repeating an external action."""


@dataclass
class StaleRecoveryReport:
    resumed: list[uuid.UUID] = field(default_factory=list)
    flagged_for_review: list[uuid.UUID] = field(default_factory=list)


async def retry_failed_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    requested_by: str,
    reason: str = "",
    acknowledge_review: bool = False,
    extra_attempts: Optional[int] = None,
) -> Task:
    task = (await session.execute(select(Task).where(Task.id == task_id).with_for_update())).scalar_one_or_none()
    if task is None:
        raise TaskNotFoundError(f"Task '{task_id}' not found")
    if task.status != TaskStatus.FAILED.value or not validate_transition(task.status, TaskStatus.PENDING.value):
        raise InvalidStateTransitionError(f"Only failed tasks can be retried (task is '{task.status}')")
    if task.needs_review and not acknowledge_review:
        raise RecoveryRefused(
            "This task's external action may already have happened. Confirm with the provider that it did not, "
            "then retry with acknowledge_review=true.")

    cfg = get_gtm_config().for_tenant(task.tenant_id)
    grant = extra_attempts if extra_attempts is not None else cfg.execution.retry.max_attempts
    task.status = TaskStatus.PENDING.value
    task.max_attempts = (task.attempt_count or 0) + max(grant, 1)
    task.next_attempt_at = None
    task.completed_at = None
    task.needs_review = False
    task.updated_at = datetime.now(timezone.utc)
    session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="task_retry_requested",
                      source="recovery", payload=json.dumps({
                          "task_id": str(task.id), "task_type": task.task_type, "requested_by": requested_by,
                          "reason": reason, "review_acknowledged": acknowledge_review,
                          "attempt_count": task.attempt_count, "max_attempts": task.max_attempts,
                          "previous_error": task.last_error_code,
                      })))
    await session.flush()
    logger.info("Task [%s] re-queued by %s (%s)", task.id, requested_by, reason)
    return task


async def recover_stale_tasks(session: AsyncSession, now: Optional[datetime] = None) -> StaleRecoveryReport:
    now = now or datetime.now(timezone.utc)
    catalogue = get_capability_catalogue()
    report = StaleRecoveryReport()
    stmt = (select(Task)
            .where(Task.status == TaskStatus.IN_PROGRESS.value, Task.lease_expires_at.is_not(None),
                   Task.lease_expires_at < now)
            .with_for_update(skip_locked=True))
    for task in (await session.execute(stmt)).scalars().all():
        cfg = get_gtm_config().for_tenant(task.tenant_id)
        spec = catalogue.get(task.task_type, task.tenant_id)
        safe = spec is not None and spec.side_effect in cfg.execution.recovery.auto_resume_side_effects
        common = {"task_id": str(task.id), "task_type": task.task_type,
                  "lease_expired_at": task.lease_expires_at.isoformat(), "attempt_count": task.attempt_count}
        task.lease_expires_at = None
        task.updated_at = now
        if safe:
            task.status = TaskStatus.PENDING.value
            task.next_attempt_at = now
            session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="task_recovered",
                              source="recovery", payload=json.dumps(common)))
            report.resumed.append(task.id)
        else:
            task.status = TaskStatus.FAILED.value
            task.needs_review = True
            task.last_error_code = "INTERRUPTED"
            task.error_message = "Interrupted during an external action; outcome unknown"
            task.completed_at = now
            session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="task_needs_review",
                              source="recovery", payload=json.dumps({
                                  **common, "reason": "Interrupted external action; reconcile before retrying"})))
            report.flagged_for_review.append(task.id)
    await session.flush()
    if report.resumed or report.flagged_for_review:
        logger.warning("Stale task recovery: %d resumed, %d flagged for review",
                       len(report.resumed), len(report.flagged_for_review))
    return report
