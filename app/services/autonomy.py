"""
Autonomous execution: the continuous, event-driven Oxygen loop.

    events ──► reactions (config: which events wake Oxygen) ──► work_items (evaluate/recheck)
                                                                     │  claimed with leases
                                                                     ▼
    pending tasks ◄── Oxygen cycle (decide → guardrails → task / approval / wait / stop)
         │
         ▼ executor (retries, failover, compliance gates) ──► events ──► …

One ``tick`` performs maintenance (stale-task recovery, approval expiry, queue
retirement), turns new events into work items, runs due Oxygen cycles and
executes due tasks (including tasks a human just approved). Safety rails:
a global pause flag (Redis, shared by all workers), a per-prospect daily cap on
Oxygen cycles, one open work item per (lead, kind), and all the existing
guardrails / approval / compliance gates on every action.

Run continuously with ``python -m app.worker``; or call one tick from the API
(``POST /api/v1/autonomy/tick``) for demos and tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, tuple_

from app.core.database import AsyncSessionLocal
from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.execution.models import ConcurrentExecutionError, InvalidStateTransitionError
from app.models.base import Event, EventProcessingState, Task
from app.models.memory import AgentDecision, WorkItem

logger = get_logger(__name__)

REACTIONS_CONSUMER = "oxygen_reactions"
EVALUATION_KINDS = ["evaluate", "recheck"]
_memory_pause: dict[str, bool] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Pause switch (shared across workers) ─────────────────────────────────────

def _pause_key() -> str:
    return f"{get_gtm_config().runtime.key_prefix}:autonomy:paused"


async def is_paused() -> bool:
    if not get_gtm_config().autonomy.enabled:
        return True
    try:
        from app.core.redis import get_loop_redis
        return bool(await get_loop_redis().exists(_pause_key()))
    except Exception:  # noqa: BLE001 - Redis down: fall back to process-local flag
        return _memory_pause.get(_pause_key(), False)


async def set_paused(paused: bool, actor: str) -> None:
    try:
        from app.core.redis import get_loop_redis
        client = get_loop_redis()
        if paused:
            await client.set(_pause_key(), actor)
        else:
            await client.delete(_pause_key())
    except Exception:  # noqa: BLE001
        _memory_pause[_pause_key()] = paused
    async with AsyncSessionLocal() as s:
        s.add(Event(event_type="autonomy_paused" if paused else "autonomy_resumed", source="api",
                    payload=f'{{"actor": "{actor}"}}'))
        await s.commit()


# ── Event reactions ──────────────────────────────────────────────────────────

async def react_to_events(db, limit: int = 500) -> int:
    """Turn new events into evaluation work items according to autonomy.reactions."""
    from app.memory.records import schedule_work
    rules = get_gtm_config().autonomy.reactions
    # Create the cursor row race-free (two workers can start on an empty database), then lock it:
    # only one worker turns a given batch of events into work at a time.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    await db.execute(pg_insert(EventProcessingState).values(consumer_name=REACTIONS_CONSUMER, processed_count=0)
                     .on_conflict_do_nothing(index_elements=["consumer_name"]))
    state = await db.get(EventProcessingState, REACTIONS_CONSUMER, with_for_update=True, populate_existing=True)
    stmt = select(Event).order_by(Event.occurred_at, Event.id).limit(limit)
    if state.last_processed_timestamp is not None:
        stmt = stmt.where(tuple_(Event.occurred_at, Event.id) >
                          tuple_(state.last_processed_timestamp, state.last_processed_event_id))
    events = (await db.execute(stmt)).scalars().all()
    scheduled = 0
    for ev in events:
        rule = rules.get(ev.event_type)
        if rule is not None and ev.lead_id is not None:
            await schedule_work(db, tenant_id=ev.tenant_id, lead_id=ev.lead_id, kind=rule.work_kind,
                                reason=f"New event: {ev.event_type}", priority=rule.priority,
                                due_at=_now() + timedelta(minutes=rule.delay_minutes), created_by="reactions",
                                payload={"event_id": str(ev.id), "event_type": ev.event_type})
            scheduled += 1
        state.last_processed_timestamp, state.last_processed_event_id = ev.occurred_at, ev.id
        state.processed_count += 1
    await db.flush()
    return scheduled


# ── One Oxygen cycle for a lead ──────────────────────────────────────────────

async def run_lead_cycle(lead_id: uuid.UUID, *, orchestrator=None, executor=None) -> dict[str, Any]:
    """Decide; if a task was created and needs no approval, execute it. Returns a Control-Centre view."""
    from app.execution.executor import TaskExecutor
    from app.oxygen.orchestrator import OxygenOrchestrator

    orchestrator = orchestrator or OxygenOrchestrator()
    async with AsyncSessionLocal() as s:
        outcome = await orchestrator.orchestrate(s, lead_id)
        if outcome.status == "error":
            return {"lead_id": str(lead_id), "status": "error", "error": outcome.error}
        await s.commit()
        record = await s.get(AgentDecision, outcome.decision_record_id) if outcome.decision_record_id else None
        guardrails = record.guardrail_result if record else None

    execution = None
    if outcome.task_created and outcome.task_id and not outcome.requires_approval:
        async with AsyncSessionLocal() as s:
            try:
                result = await (executor or TaskExecutor()).execute(outcome.task_id, s)
                execution = result.model_dump(mode="json")
            except (ConcurrentExecutionError, InvalidStateTransitionError) as exc:
                execution = {"skipped": str(exc)}
    return {
        "lead_id": str(lead_id),
        "status": outcome.status,
        "decision": outcome.decision,
        "decision_record_id": str(outcome.decision_record_id) if outcome.decision_record_id else None,
        "guardrails": guardrails,
        "task_id": str(outcome.task_id) if outcome.task_id else None,
        "approval_id": str(outcome.approval_id) if outcome.approval_id else None,
        "execution": execution,
        "next": NEXT_STEP.get(outcome.status, "Oxygen will re-evaluate when something new happens."),
    }


NEXT_STEP = {
    "awaiting_approval": "A human must approve or edit the proposed message in the Approval Centre.",
    "waiting": "Oxygen scheduled a re-evaluation.",
    "escalated": "Queued for human review.",
    "stopped": "Oxygen stopped pursuing this prospect.",
    "blocked": "Guardrails blocked the proposal; Oxygen will reconsider on the next cycle.",
    "no_action": "Nothing to do until new information arrives.",
    "success": "Task executed; Oxygen re-evaluates with the result.",
}


async def _cycles_today(lead_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(func.count(AgentDecision.id)).where(
            AgentDecision.lead_id == lead_id, AgentDecision.created_at >= _now() - timedelta(days=1)))).scalar() or 0


async def poll_inboxes() -> list[dict[str, Any]]:
    """Read every available ``receive_email`` provider (e.g. IMAP) into the inbound pipeline."""
    from app.providers import get_provider_registry
    out = []
    for rec in get_provider_registry().candidates("receive_email"):
        if not rec.available or not rec.config.enabled or not hasattr(rec.instance, "poll"):
            continue
        try:
            async with AsyncSessionLocal() as s:
                out.append(await rec.instance.poll(s))
        except Exception as exc:  # noqa: BLE001 - a mailbox outage must not stop the loop
            logger.warning("Inbox %s poll failed: %s", rec.name, exc)
            out.append({"provider": rec.name, "error": str(exc)[:200]})
    return out


# ── Tick ─────────────────────────────────────────────────────────────────────

async def tick(worker: str = "worker", *, orchestrator=None, executor=None) -> dict[str, Any]:
    from app.compliance.approvals import expire_stale
    from app.execution.executor import TaskExecutor
    from app.execution.recovery import recover_stale_tasks
    from app.memory.records import claim_due, complete_work, retire_exhausted, schedule_work

    report: dict[str, Any] = {"paused": False, "recovered": 0, "approvals_expired": 0, "reactions": 0,
                              "attributions": 0, "inbox": [],
                              "cycles": [], "tasks_executed": 0, "capped": 0}
    if await is_paused():
        report["paused"] = True
        return report
    cfg = get_gtm_config().autonomy
    executor = executor or TaskExecutor()

    async with AsyncSessionLocal() as s:
        stale = await recover_stale_tasks(s)
        report["recovered"] = len(stale.resumed) + len(stale.flagged_for_review)
        report["approvals_expired"] = await expire_stale(s)
        await retire_exhausted(s)
        await s.commit()

    report["inbox"] = await poll_inboxes()

    async with AsyncSessionLocal() as s:
        report["reactions"] = await react_to_events(s)
        await s.commit()

    async with AsyncSessionLocal() as s:
        from app.revops.outcomes import attribute_outcomes
        report["attributions"] = await attribute_outcomes(s)
        await s.commit()

    async with AsyncSessionLocal() as s:
        items = await claim_due(s, worker=worker, limit=cfg.max_work_items_per_tick, lease_s=cfg.lease_s,
                                kinds=EVALUATION_KINDS)
        claimed = [(i.id, i.lead_id, i.tenant_id) for i in items]
        await s.commit()

    for item_id, lead_id, tenant_id in claimed:
        if await _cycles_today(lead_id) >= cfg.max_cycles_per_lead_per_day:
            outcome = "capped"
            report["capped"] += 1
            async with AsyncSessionLocal() as s:
                await schedule_work(s, tenant_id=tenant_id, lead_id=lead_id, kind="recheck",
                                    reason="Daily evaluation cap reached; resume tomorrow",
                                    due_at=_now() + timedelta(days=1), created_by="autonomy")
                await s.commit()
        else:
            try:
                from app.core.tenancy import tenant_scope
                with tenant_scope(tenant_id):
                    cycle = await run_lead_cycle(lead_id, orchestrator=orchestrator, executor=executor)
            except Exception as exc:  # noqa: BLE001 - one prospect's failure must not stop the others
                logger.exception("Oxygen cycle failed for lead %s", lead_id)
                report["cycles"].append({"lead_id": str(lead_id), "status": "error", "error": str(exc)[:200]})
                continue   # the item keeps its lease; it is retried after expiry (attempts are capped)
            outcome = cycle["status"]
            report["cycles"].append({"lead_id": str(lead_id), "status": outcome,
                                     "action": getattr(cycle.get("decision"), "action", None)})
        async with AsyncSessionLocal() as s:
            item = await s.get(WorkItem, item_id, with_for_update=True)
            if item is not None and item.finished_at is None:
                await complete_work(s, item, outcome)
            await s.commit()

    # Due tasks: approved actions, scheduled retries, anything left pending.
    async with AsyncSessionLocal() as s:
        ids = (await s.execute(
            select(Task.id).where(Task.status == "pending",
                                  or_(Task.next_attempt_at.is_(None), Task.next_attempt_at <= _now()))
            .order_by(Task.priority.desc(), Task.created_at).limit(cfg.max_tasks_per_tick)
            .with_for_update(skip_locked=True))).scalars().all()
        await s.commit()
    for task_id in ids:
        async with AsyncSessionLocal() as s:
            try:
                await executor.execute(task_id, s)
                report["tasks_executed"] += 1
            except (ConcurrentExecutionError, InvalidStateTransitionError):
                continue
    return report
