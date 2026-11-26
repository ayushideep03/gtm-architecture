"""
Human approval workflow for external actions.

Flow:
  Oxygen proposes an action whose capability requires approval (by side-effect
  policy or explicit capability policy) -> the task is created in status
  'awaiting_approval' with an Approval row holding the proposed payload, the
  reasoning and the evidence.

  approve: the reviewer may edit the payload; the final payload is validated
  against the capability schema, compliance is re-checked, the payload's
  SHA-256 is recorded, and the task moves to 'pending'.
  reject / expire: the task is cancelled.

The executor refuses to run any approval-required task unless an approved
Approval exists and the task's current payload hashes to the approved hash —
so nothing can be changed or sent after the human decision.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities import get_capability_catalogue
from app.compliance.rules import check_contact_allowed
from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.models.base import Event, Lead, Task
from app.models.memory import AgentDecision, Approval

logger = get_logger(__name__)


class ApprovalError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _task_payload(task: Task) -> dict:
    try:
        return json.loads(task.payload) if task.payload else {}
    except ValueError:
        return {}


async def request_approval(
    db: AsyncSession, task: Task, *, reasoning: Optional[str] = None, evidence: Optional[list] = None,
    decision_id: Optional[uuid.UUID] = None,
) -> Approval:
    cfg = get_gtm_config().for_tenant(task.tenant_id)
    task.status = "awaiting_approval"
    approval = Approval(
        tenant_id=task.tenant_id, task_id=task.id, lead_id=task.lead_id, decision_id=decision_id,
        capability=task.task_type, original_payload=_task_payload(task), reasoning=reasoning,
        evidence=evidence or [], expires_at=_now() + timedelta(hours=cfg.approval.ttl_hours),
    )
    db.add(approval)
    await db.flush()
    db.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="approval_requested",
                 source="approvals", payload=json.dumps({
                     "approval_id": str(approval.id), "task_id": str(task.id), "capability": task.task_type,
                     "expires_at": approval.expires_at.isoformat()})))
    await db.flush()
    from app.pipeline.hooks import on_approval_requested
    await on_approval_requested(db, task)
    return approval


async def _load(db: AsyncSession, approval_id: uuid.UUID) -> tuple[Approval, Task]:
    approval = (await db.execute(select(Approval).where(Approval.id == approval_id).with_for_update())).scalar_one_or_none()
    if approval is None:
        raise ApprovalError("approval not found")
    task = (await db.execute(select(Task).where(Task.id == approval.task_id).with_for_update())).scalar_one()
    return approval, task


async def approve(
    db: AsyncSession, approval_id: uuid.UUID, *, reviewer: str, payload: Optional[dict] = None,
    note: Optional[str] = None,
) -> Approval:
    approval, task = await _load(db, approval_id)
    if approval.status != "pending" or task.status != "awaiting_approval":
        raise ApprovalError(f"approval is {approval.status}; task is {task.status}")
    if approval.expires_at and approval.expires_at < _now():
        await _close(db, approval, task, "expired", reviewer="system", note="expired before review")
        raise ApprovalError("approval expired")

    final = payload if payload is not None else approval.original_payload
    spec = get_capability_catalogue().get(task.task_type, task.tenant_id)
    if spec is not None:
        validation = spec.validate_parameters(final, {"lead_id": task.lead_id})
        if not validation.ok:
            raise ApprovalError("edited payload invalid: " + "; ".join(validation.errors))
    lead = await db.get(Lead, task.lead_id) if task.lead_id else None
    if lead is not None:
        status = await check_contact_allowed(db, lead)
        if not status.allowed:
            raise ApprovalError("compliance no longer allows this contact: " + "; ".join(status.reasons()))

    task.payload = json.dumps(final, default=str)
    task.status = "pending"
    approval.final_payload = final
    approval.payload_hash = payload_hash(final)
    approval.edited = payload is not None and payload != approval.original_payload
    approval.status, approval.decided_by, approval.decided_at, approval.reviewer_note = "approved", reviewer, _now(), note
    await _decision_status(db, approval.decision_id, "approved")
    db.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="approval_granted", source="approvals",
                 payload=json.dumps({"approval_id": str(approval.id), "task_id": str(task.id),
                                     "reviewer": reviewer, "edited": approval.edited, "note": note})))
    await db.flush()
    from app.pipeline.hooks import on_approval_decided
    await on_approval_decided(db, approval, task, "approved")
    return approval


async def reject(db: AsyncSession, approval_id: uuid.UUID, *, reviewer: str, note: Optional[str] = None) -> Approval:
    approval, task = await _load(db, approval_id)
    if approval.status != "pending":
        raise ApprovalError(f"approval is {approval.status}")
    await _close(db, approval, task, "rejected", reviewer=reviewer, note=note)
    return approval


async def _close(db, approval: Approval, task: Task, status: str, *, reviewer: str, note: Optional[str]) -> None:
    approval.status, approval.decided_by, approval.decided_at, approval.reviewer_note = status, reviewer, _now(), note
    task.status = "cancelled"
    task.completed_at = _now()
    task.error_message = f"approval {status}" + (f": {note}" if note else "")
    await _decision_status(db, approval.decision_id, "rejected" if status == "rejected" else "expired")
    db.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id,
                 event_type="approval_rejected" if status == "rejected" else "approval_expired",
                 source="approvals", payload=json.dumps({"approval_id": str(approval.id), "task_id": str(task.id),
                                                         "reviewer": reviewer, "note": note})))
    await db.flush()
    from app.pipeline.hooks import on_approval_decided
    await on_approval_decided(db, approval, task, status)


async def expire_stale(db: AsyncSession) -> int:
    rows = (await db.execute(select(Approval).where(Approval.status == "pending", Approval.expires_at < _now())
                             .with_for_update(skip_locked=True))).scalars().all()
    for approval in rows:
        task = await db.get(Task, approval.task_id)
        await _close(db, approval, task, "expired", reviewer="system", note="no decision before expiry")
    return len(rows)


async def verify_approved(db: AsyncSession, task: Task) -> tuple[bool, str]:
    """Executor check: approved, and the task payload is exactly what was approved."""
    approval = (await db.execute(select(Approval).where(Approval.task_id == task.id))).scalar_one_or_none()
    if approval is None:
        return False, "no approval exists for this external action"
    if approval.status != "approved":
        return False, f"approval is {approval.status}"
    if approval.payload_hash != payload_hash(_task_payload(task)):
        return False, "task payload changed after approval"
    return True, f"approved by {approval.decided_by}"


async def _decision_status(db, decision_id, status: str) -> None:
    if decision_id is None:
        return
    decision = await db.get(AgentDecision, decision_id)
    if decision is not None:
        decision.status = status
