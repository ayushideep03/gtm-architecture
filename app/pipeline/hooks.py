"""
Pipeline hooks: keep drafts, follow-ups and meetings in step with what actually happened
(task completed, approval requested / decided, reply classified). Called from the executor,
the approval workflow and the inbound pipeline, inside their transactions. A hook failure is
logged and never fails the action it observes.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger

logger = get_logger(__name__)


async def on_task_completed(db, task, capability: str, result: dict) -> None:
    try:
        async with db.begin_nested():   # savepoint: a hook error must not abort the caller's transaction
            await _task_completed(db, task, capability, result)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline hook on_task_completed failed for task %s", task.id)


async def _task_completed(db, task, capability: str, result: dict) -> None:
    if True:
        from app.models.base import Lead, Task
        from app.pipeline import drafts, followups
        if capability == "draft_outreach":
            await drafts.from_draft_task(db, task, result)
            await followups.on_draft(db, task.lead_id, task.id)
        elif capability == "draft_reply":
            await drafts.from_reply_task(db, task, result)
        elif capability == "send_email":
            payload = json.loads(task.payload or "{}")
            draft_task_id = payload.get("draft_task_id")
            await drafts.mark_by_source_task(db, draft_task_id, status="sent", send_task_id=task.id)
            source = await db.get(Task, __import__("uuid").UUID(draft_task_id)) if draft_task_id else None
            if source is None or source.task_type != "draft_reply":      # replies don't advance the sequence
                lead = await db.get(Lead, task.lead_id)
                if lead is not None:
                    await followups.on_email_sent(db, lead, task.id)


async def on_approval_requested(db, task) -> None:
    try:
        async with db.begin_nested():
            await _approval_requested(db, task)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline hook on_approval_requested failed for task %s", task.id)


async def _approval_requested(db, task) -> None:
    if True:
        from app.models.base import Lead
        from app.pipeline import drafts, meetings
        payload = json.loads(task.payload or "{}")
        if task.task_type == "send_email":
            await drafts.mark_by_source_task(db, payload.get("draft_task_id"), status="submitted", send_task_id=task.id)
        elif task.task_type == "book_meeting":
            lead = await db.get(Lead, task.lead_id)
            if lead is not None:
                await meetings.mark_pending_approval(db, lead, task.id)


async def on_approval_decided(db, approval, task, decision: str) -> None:
    """decision: approved | rejected | expired."""
    try:
        async with db.begin_nested():
            await _approval_decided(db, approval, task, decision)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline hook on_approval_decided failed for approval %s", approval.id)


async def _approval_decided(db, approval, task, decision: str) -> None:
    if True:
        from app.pipeline import drafts, meetings
        payload = json.loads(task.payload or "{}")
        if task.task_type == "send_email":
            values = {"status": "approved" if decision == "approved" else "rejected"}
            if decision == "approved" and approval.edited:
                values.update(subject=payload.get("subject"), body=payload.get("body") or "",
                              edited_by=approval.decided_by)
            await drafts.mark_by_source_task(db, payload.get("draft_task_id"), **values)
        elif task.task_type == "book_meeting" and decision != "approved":
            m = await meetings.open_meeting(db, task.lead_id)
            if m is not None:
                meetings._log(m, "requested", approval.decided_by or "system",
                              f"proposal {decision}; Oxygen will reconsider")


async def on_reply_classified(db, lead, category: str, when, follow_up_after_days=None) -> list[str]:
    actions: list[str] = []
    try:
        async with db.begin_nested():
            await _reply_classified(db, lead, category, when, follow_up_after_days, actions)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline hook on_reply_classified failed for lead %s", lead.id)
    return actions


async def _reply_classified(db, lead, category, when, follow_up_after_days, actions: list[str]) -> None:
    if True:
        from app.pipeline import followups, meetings
        actions.append(await followups.on_reply(db, lead, category, when, follow_up_after_days))
        if category in ("meeting_request", "positive_interest"):
            await meetings.request_meeting(db, lead, by="prospect", note=f"reply classified as {category}")
            actions.append("meeting request recorded")
        if category in ("not_interested", "unsubscribe"):
            lead.status = "closed_lost"
            actions.append("lead closed (prospect declined)")
