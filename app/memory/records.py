"""
Research findings, decision history and the work queue.

- Findings: synthesised research (briefs, intent signals, news, objections),
  with citations and freshness (stale_after from memory.finding_ttl_days).
- Decisions: every proposal Oxygen made (rules or LLM), its reasoning,
  evidence references, confidence, risks, guardrail result and final status.
- Work queue: when Oxygen should look at a lead again. One open item per
  (tenant, kind, lead); claimed with FOR UPDATE SKIP LOCKED and a lease, so
  several workers can drain it safely. Scheduled rechecks are items with a
  future due_at and a human-readable reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import get_gtm_config
from app.core.logging import get_logger
from app.models.memory import AgentDecision, ResearchFinding, WorkItem

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Findings ──────────────────────────────────────────────────────────────────

async def record_finding(
    db: AsyncSession, *, tenant_id: uuid.UUID, lead_id: Optional[uuid.UUID], entity_type: str,
    entity_id: uuid.UUID, kind: str, summary: str, body: Optional[dict] = None,
    citations: Optional[list[dict]] = None, confidence: Optional[float] = None, source: Optional[str] = None,
    source_task_id: Optional[uuid.UUID] = None,
) -> ResearchFinding:
    ttl = get_gtm_config().for_tenant(tenant_id).memory.finding_ttl_days.get(kind)
    finding = ResearchFinding(
        tenant_id=tenant_id, lead_id=lead_id, entity_type=entity_type, entity_id=entity_id, kind=kind,
        summary=summary, body=body, citations=citations or [], confidence=confidence, source=source,
        source_task_id=source_task_id, stale_after=_now() + timedelta(days=ttl) if ttl else None,
    )
    db.add(finding)
    await db.flush()
    return finding


async def findings_for_lead(db: AsyncSession, lead_id: uuid.UUID, include_stale: bool = False) -> list[ResearchFinding]:
    stmt = select(ResearchFinding).where(ResearchFinding.lead_id == lead_id)
    if not include_stale:
        stmt = stmt.where(or_(ResearchFinding.stale_after.is_(None), ResearchFinding.stale_after > _now()))
    return list((await db.execute(stmt.order_by(ResearchFinding.created_at.desc()))).scalars().all())


# ── Decisions ─────────────────────────────────────────────────────────────────

async def record_decision(db: AsyncSession, **fields: Any) -> AgentDecision:
    decision = AgentDecision(**fields)
    db.add(decision)
    await db.flush()
    return decision


async def set_decision_status(db: AsyncSession, decision_id: Optional[uuid.UUID], status: str) -> None:
    if decision_id is None:
        return
    await db.execute(update(AgentDecision).where(AgentDecision.id == decision_id)
                     .values(status=status, updated_at=_now()))


async def decisions_for_lead(db: AsyncSession, lead_id: uuid.UUID, limit: int = 50) -> list[AgentDecision]:
    stmt = (select(AgentDecision).where(AgentDecision.lead_id == lead_id)
            .order_by(AgentDecision.created_at.desc()).limit(limit))
    return list((await db.execute(stmt)).scalars().all())


# ── Work queue ────────────────────────────────────────────────────────────────

async def schedule_work(
    db: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID, kind: str, reason: str,
    due_at: Optional[datetime] = None, priority: int = 5, payload: Optional[dict] = None,
    created_by: str = "oxygen", max_attempts: int = 3,
) -> WorkItem:
    """Upsert: one open item per (tenant, kind, lead). An earlier due date wins."""
    due = due_at or _now()
    existing = (await db.execute(select(WorkItem).where(
        WorkItem.tenant_id == tenant_id, WorkItem.kind == kind, WorkItem.lead_id == lead_id,
        WorkItem.finished_at.is_(None)).with_for_update())).scalar_one_or_none()
    if existing is not None:
        if due < existing.due_at:
            existing.due_at = due
        existing.reason = reason
        existing.priority = max(existing.priority, priority)
        if payload:
            existing.payload = {**(existing.payload or {}), **payload}
        await db.flush()
        return existing
    item = WorkItem(tenant_id=tenant_id, lead_id=lead_id, kind=kind, reason=reason, due_at=due,
                    priority=priority, payload=payload, created_by=created_by, max_attempts=max_attempts)
    db.add(item)
    await db.flush()
    return item


async def claim_due(db: AsyncSession, *, worker: str, limit: int = 10, lease_s: float = 300,
                    kinds: Optional[list[str]] = None) -> list[WorkItem]:
    """Lease due items (FOR UPDATE SKIP LOCKED); attempts are counted on claim."""
    now = _now()
    stmt = (select(WorkItem)
            .where(WorkItem.finished_at.is_(None), WorkItem.due_at <= now,
                   or_(WorkItem.leased_until.is_(None), WorkItem.leased_until < now),
                   WorkItem.attempts < WorkItem.max_attempts)
            .order_by(WorkItem.priority.desc(), WorkItem.due_at)
            .limit(limit).with_for_update(skip_locked=True))
    if kinds:
        stmt = stmt.where(WorkItem.kind.in_(kinds))
    items = list((await db.execute(stmt)).scalars().all())
    for item in items:
        item.leased_until = now + timedelta(seconds=lease_s)
        item.leased_by = worker
        item.attempts += 1
    await db.flush()
    return items


async def complete_work(db: AsyncSession, item: WorkItem, outcome: str) -> None:
    item.finished_at = _now()
    item.outcome = outcome
    item.leased_until = None
    await db.flush()


async def retire_exhausted(db: AsyncSession) -> int:
    result = await db.execute(update(WorkItem)
                              .where(WorkItem.finished_at.is_(None), WorkItem.attempts >= WorkItem.max_attempts,
                                     or_(WorkItem.leased_until.is_(None), WorkItem.leased_until < _now()))
                              .values(finished_at=_now(), outcome="exhausted"))
    return result.rowcount or 0


async def open_work_for_lead(db: AsyncSession, lead_id: uuid.UUID) -> list[WorkItem]:
    stmt = select(WorkItem).where(WorkItem.lead_id == lead_id, WorkItem.finished_at.is_(None)).order_by(WorkItem.due_at)
    return list((await db.execute(stmt)).scalars().all())
