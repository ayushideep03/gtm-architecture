"""
Task Executor: lifecycle, provider selection, execution, retries and events.

Per execution:
  1. Lock the task row (SELECT ... FOR UPDATE); completed tasks return their
     cached result (never re-executed); pending tasks scheduled for later are
     deferred.
  2. Rank providers for the task's capability (ProviderSelector: tenant policy,
     health and rate limits shared via Redis, reliability, latency, cost).
  3. Run the best provider under its timeout; retry transient errors on the
     same provider (resilience.max_retries) and then fail over to the next.
  4. Outcome:
       success                      -> completed
       transient, attempts left     -> pending again, next_attempt_at = backoff
       ambiguous external action    -> failed + needs_review (never auto-retried)
       anything else                -> failed

Safety rule for external actions (side effect external_communication / spend):
if a provider times out or raises, the request may already have reached the
vendor. Unless that provider declares ``supports_idempotency`` (it de-duplicates
by the idempotency key we send), the task is NOT retried or failed over — it is
flagged for human review. This guarantees no email/meeting/spend is repeated.

Only infrastructure errors (timeouts, exceptions, rate limits, unavailability)
count against a provider's health; business rejections do not.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import random
import time
from typing import Any, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import GTMConfig, RetryPolicy, SideEffect, get_gtm_config
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
from app.providers import get_provider_registry, get_provider_selector
from app.providers.selection import Candidate, ProviderSelector, SelectionRequest

logger = get_logger(__name__)

# Error code -> retry category (matched against resilience.retry_on)
_CATEGORY = {
    "TIMEOUT": "timeout",
    "PROVIDER_EXCEPTION": "provider_exception",
    "RATE_LIMITED": "rate_limited",
    "UNAVAILABLE": "unavailable",
}
_EXTERNAL = {SideEffect.EXTERNAL_COMMUNICATION, SideEffect.SPEND}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """Exponential backoff with jitter for the n-th attempt (1-based)."""
    base = min(policy.backoff_base_s * (2 ** max(attempt - 1, 0)), policy.backoff_max_s)
    if policy.jitter and base > 0:
        base *= 1 + random.uniform(-policy.jitter, policy.jitter)
    return max(base, 0.0)


class TaskExecutor:
    """Coordinates execution of a single Task against registered providers."""

    def __init__(
        self,
        registry: Optional[ExecutionRegistry] = None,
        selector: Optional[ProviderSelector] = None,
    ) -> None:
        self.registry = registry or execution_registry
        self._selector = selector
        # A private registry (tests, embedding) gets its own in-memory selector;
        # the global registry uses the shared (Redis-backed) selector.
        if self._selector is None and self.registry is not get_provider_registry():
            self._selector = ProviderSelector(self.registry)

    @property
    def selector(self) -> ProviderSelector:
        return self._selector or get_provider_selector()

    # ── public API ───────────────────────────────────────────────────────────

    async def execute(self, task_id: uuid.UUID, session: AsyncSession, *, force: bool = False) -> ExecutionOutcome:
        """Execute one task. ``force`` ignores a future next_attempt_at (manual run)."""
        task = (await session.execute(select(Task).where(Task.id == task_id).with_for_update())).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError(f"Task '{task_id}' not found")

        status = task.status
        if status in (TaskStatus.COMPLETED.value, TaskStatus.DONE.value):
            logger.info("Task [%s] already completed; returning cached result.", task_id)
            return ExecutionOutcome(task_id=task.id, success=True, task_status=status,
                                    result=_loads(task.result), event_type="task_completed",
                                    already_executed=True)
        if status == TaskStatus.IN_PROGRESS.value:
            raise ConcurrentExecutionError(f"Task '{task_id}' is already currently in progress")
        if status == TaskStatus.FAILED.value:
            raise InvalidStateTransitionError(
                f"Task '{task_id}' is in 'failed' status and cannot transition without an explicit retry "
                f"(use the recovery API)")
        if not validate_transition(status, TaskStatus.IN_PROGRESS.value):
            raise InvalidStateTransitionError(f"Cannot transition task from '{status}' to 'in_progress'")
        if not force and task.next_attempt_at and task.next_attempt_at > _now():
            when, tid = task.next_attempt_at, task.id
            await session.rollback()  # release the row lock; nothing changed
            return ExecutionOutcome(task_id=tid, success=False, task_status=status,
                                    event_type="task_deferred",
                                    error_message=f"Scheduled for retry at {when.isoformat()}")

        cfg = get_gtm_config()
        catalogue = self.registry.catalogue
        capability = catalogue.canonical(task.task_type) or task.task_type
        tenant_id = task.tenant_id
        tenant_cfg = cfg.for_tenant(tenant_id)
        spec = catalogue.get(capability, tenant_id)
        retry_policy = self._retry_policy(tenant_cfg, capability)

        external = spec is not None and spec.side_effect in _EXTERNAL

        # Deterministic pre-execution gates for external actions (last line of defence):
        # approval bound to the exact payload, then compliance re-checked at send time.
        if external:
            blocked = await self._external_gates(task, session, capability, spec, tenant_cfg)
            if blocked is not None:
                return blocked

        ranking = await self.selector.rank(SelectionRequest(capability=capability, tenant_id=tenant_id),
                                           session=session)
        if not ranking.candidates:
            reasons = "; ".join(f"{r.provider}: {r.reason}" for r in ranking.rejected)
            if self.registry.candidates(capability):
                # Providers exist but none is usable right now (cooldown, rate limit...): transient.
                return await self._finish_failure(
                    task, session, capability, tenant_cfg, retry_policy,
                    ExecutionResult(success=False, status="failed", provider="none",
                                    error_code="UNAVAILABLE",
                                    error_message=f"No available provider for '{capability}' ({reasons})"),
                    used=None, attempts=[], ambiguous=False, selection=ranking.explain(), counted=False)
            err = f"Unknown task type '{task.task_type}' has no registered provider"
            return await self._finish_failure(
                task, session, capability, tenant_cfg, retry_policy,
                ExecutionResult(success=False, status="failed", provider="none",
                                error_code="UNKNOWN_TASK_TYPE", error_message=err),
                used=None, attempts=[], ambiguous=False, selection=ranking.explain(), counted=False)

        # Start: in_progress with a lease (stale-task recovery uses it)
        now = _now()
        task.status = TaskStatus.IN_PROGRESS.value
        task.attempt_count = (task.attempt_count or 0) + 1
        task.started_at = now
        task.lease_expires_at = now + timedelta(seconds=tenant_cfg.execution.lease_s)
        task.updated_at = now
        session.add(Event(lead_id=task.lead_id, tenant_id=tenant_id, event_type="task_started",
                          source="task_executor",
                          payload=json.dumps({
                              "task_id": str(task.id), "lead_id": _s(task.lead_id),
                              "task_type": task.task_type, "capability": capability,
                              "attempt": task.attempt_count,
                              "provider": ranking.candidates[0].name, "selection": ranking.explain(),
                          }, default=str)))
        # Commit the claim BEFORE calling the provider. If this process dies mid-call, the task
        # stays in_progress with a lease: other workers refuse it (ConcurrentExecutionError) and
        # stale recovery flags external actions for review instead of re-sending them. With only
        # a flush, a crash would roll the task back to 'pending' and the email would go out twice.
        await session.commit()

        result, used, attempts, ambiguous = await self._run(
            task, capability, _loads(task.payload), ranking.candidates, session, cfg, external)

        if result.success:
            if external:
                await self._record_touch(task, session, spec, result, used)
            return await self._finish_success(task, session, capability, result, used, attempts)
        return await self._finish_failure(task, session, capability, tenant_cfg, retry_policy, result,
                                          used=used, attempts=attempts, ambiguous=ambiguous,
                                          selection=None, counted=True)

    # ── provider loop ────────────────────────────────────────────────────────

    async def _run(
        self, task: Task, capability: str, payload: dict[str, Any], candidates: list[Candidate],
        session: AsyncSession, cfg: GTMConfig, external: bool,
    ) -> tuple[ExecutionResult, Optional[Candidate], list[dict[str, Any]], bool]:
        sel_cfg = cfg.selection
        ambiguous_codes = set(cfg.execution.recovery.ambiguous_error_codes)
        attempts: list[dict[str, Any]] = []
        result: Optional[ExecutionResult] = None
        used: Optional[Candidate] = None

        spend_limit = cfg.for_tenant(task.tenant_id).compliance.daily_spend_limit
        spent_today = await self._spent_today(session, task.tenant_id) if spend_limit is not None else 0.0

        for cand in candidates:
            pc = cand.config
            if spend_limit is not None and pc.cost_per_call > 0 and spent_today + pc.cost_per_call > spend_limit:
                attempts.append({"provider": cand.name, "success": False, "error_code": "SPEND_LIMIT", "duration_ms": 0})
                result = ExecutionResult(success=False, status="failed", provider=cand.name, error_code="SPEND_LIMIT",
                                         error_message=f"Daily spend limit {spend_limit} would be exceeded")
                continue
            binding = cand.record.binding(capability, self.registry.catalogue)
            if binding is None:
                attempts.append({"provider": cand.name, "success": False, "error_code": "NO_BINDING", "duration_ms": 0})
                continue

            for try_no in range(pc.resilience.max_retries + 1):
                if not await self.selector.rate_limiter.try_acquire(cand.name, pc.rate_limit):
                    attempts.append({"provider": cand.name, "success": False,
                                     "error_code": "RATE_LIMITED", "duration_ms": 0})
                    result = ExecutionResult(success=False, status="failed", provider=cand.name,
                                             error_code="RATE_LIMITED", error_message="Local rate limit reached")
                    break  # try the next provider

                context = ExecutionContext(
                    task_id=task.id, task_type=capability, lead_id=task.lead_id, capability=capability,
                    payload=payload, attempt=task.attempt_count,
                    metadata={"provider": cand.name, "mode": pc.mode, "tenant_id": str(task.tenant_id),
                              "idempotency_key": task.idempotency_key or f"task:{task.id}",
                              "provider_try": try_no + 1},
                )
                logger.info("Executing task [%s] %s via %r (mode=%s, try %d)",
                            task.id, capability, cand.name, pc.mode, try_no + 1)
                started = time.perf_counter()
                try:
                    result = await asyncio.wait_for(binding.execute(context, session=session),
                                                    timeout=pc.resilience.timeout_s)
                except asyncio.TimeoutError:
                    result = ExecutionResult(success=False, status="failed", provider=cand.name,
                                             error_code="TIMEOUT",
                                             error_message=f"Provider timed out after {pc.resilience.timeout_s}s")
                except Exception as exc:  # noqa: BLE001 - provider failures become results
                    logger.exception("Provider %r raised on task [%s]", cand.name, task.id)
                    result = ExecutionResult(success=False, status="failed", provider=cand.name,
                                             error_code="PROVIDER_EXCEPTION", error_message=str(exc))
                used = cand
                attempts.append({"provider": cand.name, "success": result.success,
                                 "error_code": result.error_code,
                                 "duration_ms": round((time.perf_counter() - started) * 1000, 2)})

                if result.success:
                    await self.selector.health.record_success(cand.name)
                    return result, used, attempts, False

                category = self._category(result)
                if category is not None:  # infrastructure failure -> provider health
                    await self.selector.health.record_failure(
                        cand.name, result.error_code, sel_cfg.consecutive_failure_threshold,
                        sel_cfg.failure_cooldown_s)
                if result.error_code == "RATE_LIMITED":
                    retry_after = float(result.metadata.get("retry_after_s") or sel_cfg.failure_cooldown_s)
                    await self.selector.rate_limiter.block(cand.name, retry_after)

                # Ambiguous outcome of an external action: stop everything.
                if external and result.error_code in ambiguous_codes and not pc.supports_idempotency:
                    return result, used, attempts, True

                if category is None or category not in pc.resilience.retry_on:
                    return result, used, attempts, False  # business failure: no retry, no failover
                if category == "rate_limited":
                    break  # don't hammer this provider; fail over
                if try_no < pc.resilience.max_retries:
                    await asyncio.sleep(min(pc.resilience.backoff_base_s * (2 ** try_no), pc.resilience.backoff_max_s))

            if not sel_cfg.failover:
                break

        if result is None:
            result = ExecutionResult(success=False, status="failed", provider="none", error_code="UNAVAILABLE",
                                     error_message="All providers were rate-limited or unbound")
        return result, used, attempts, False

    # ── outcomes ─────────────────────────────────────────────────────────────

    async def _finish_success(self, task, session, capability, result, used, attempts) -> ExecutionOutcome:
        now = _now()
        task.status = TaskStatus.COMPLETED.value
        task.result = json.dumps(result.result, default=str)
        task.error_message = None
        task.last_error_code = None
        task.next_attempt_at = None
        task.lease_expires_at = None
        task.completed_at = now
        task.updated_at = now
        session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="task_completed",
                          source=used.name if used else "task_executor",
                          payload=json.dumps({**self._common(task, capability, used, attempts),
                                              "success": True, "result": result.result}, default=str)))
        await self._sync_decision(session, task, "executed")
        await session.commit()
        logger.info("Task [%s] completed via %r after %d attempt(s)", task.id, used.name if used else None, len(attempts))
        return ExecutionOutcome(task_id=task.id, success=True, task_status=task.status,
                                result=result.result, event_type="task_completed")

    async def _finish_failure(
        self, task, session, capability, tenant_cfg: GTMConfig, policy: RetryPolicy, result: ExecutionResult, *,
        used: Optional[Candidate], attempts: list, ambiguous: bool, selection: Optional[dict], counted: bool,
    ) -> ExecutionOutcome:
        now = _now()
        if not counted:
            task.attempt_count = (task.attempt_count or 0) + 1
        max_attempts = task.max_attempts or policy.max_attempts
        retryable = self._category(result) is not None and not ambiguous
        common = {**self._common(task, capability, used, attempts),
                  "error": {"message": result.error_message, "code": result.error_code}}
        if selection:
            common["selection"] = selection

        task.last_error_code = result.error_code
        task.error_message = result.error_message or "Execution failed"
        task.lease_expires_at = None
        task.updated_at = now

        if retryable and task.attempt_count < max_attempts:
            delay = backoff_delay(policy, task.attempt_count)
            retry_after = result.metadata.get("retry_after_s")
            if retry_after:
                delay = max(delay, float(retry_after))
            task.status = TaskStatus.PENDING.value
            task.next_attempt_at = now + timedelta(seconds=delay)
            event_type = "task_retry_scheduled"
            body = {**common, "success": False, "next_attempt_at": task.next_attempt_at.isoformat(),
                    "attempt": task.attempt_count, "max_attempts": max_attempts}
        else:
            task.status = TaskStatus.FAILED.value
            task.completed_at = now
            task.next_attempt_at = None
            task.needs_review = ambiguous
            task.result = json.dumps({"error": result.error_message, "code": result.error_code,
                                      "needs_review": ambiguous}, default=str)
            event_type = "task_failed"
            body = {**common, "success": False, "needs_review": ambiguous,
                    "attempt": task.attempt_count, "max_attempts": max_attempts}

        session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type=event_type,
                          source=used.name if used else "task_executor", payload=json.dumps(body, default=str)))
        await self._sync_decision(session, task, "retry_scheduled" if event_type == "task_retry_scheduled" else "failed")
        if ambiguous:
            session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="task_needs_review",
                              source="task_executor", payload=json.dumps({
                                  **common, "reason": "External action outcome unknown; not retried to avoid "
                                                      "a duplicate. Reconcile with the provider, then retry or close."
                              }, default=str)))
        await session.commit()
        logger.info("Task [%s] %s (%s)", task.id, event_type, result.error_code)
        return ExecutionOutcome(task_id=task.id, success=False, task_status=task.status, result={},
                                event_type=event_type, error_message=task.error_message)

    # ── external-action gates ────────────────────────────────────────────────

    async def _external_gates(self, task, session, capability, spec, tenant_cfg) -> Optional[ExecutionOutcome]:
        from app.capabilities import get_capability_catalogue
        from app.compliance.approvals import verify_approved
        from app.compliance.rules import check_contact_allowed
        from app.models.base import Lead

        if get_capability_catalogue().requires_approval(capability, task.tenant_id):
            ok, reason = await verify_approved(session, task)
            if not ok:
                return await self._cancel(task, session, capability, "APPROVAL_REQUIRED", reason)
        lead = await session.get(Lead, task.lead_id) if task.lead_id else None
        if lead is None:
            return await self._cancel(task, session, capability, "COMPLIANCE_BLOCKED", "external action without a lead")
        status = await check_contact_allowed(session, lead)
        if not status.allowed:
            return await self._cancel(task, session, capability, "COMPLIANCE_BLOCKED", "; ".join(status.reasons()),
                                      compliance=status.as_dict())
        return None

    async def _cancel(self, task, session, capability, code, reason, compliance=None) -> ExecutionOutcome:
        """Refuse to execute: no provider is called and nothing is retried."""
        now = _now()
        task.status = TaskStatus.CANCELLED.value
        task.last_error_code = code
        task.error_message = reason
        task.completed_at = now
        task.updated_at = now
        session.add(Event(lead_id=task.lead_id, tenant_id=task.tenant_id, event_type="compliance_blocked",
                          source="task_executor", payload=json.dumps({
                              "task_id": str(task.id), "capability": capability, "code": code, "reason": reason,
                              "compliance": compliance}, default=str)))
        await self._sync_decision(session, task, "blocked")
        await session.commit()
        logger.warning("Task [%s] %s refused: %s", task.id, capability, reason)
        return ExecutionOutcome(task_id=task.id, success=False, task_status=task.status, event_type="compliance_blocked",
                                error_message=f"{code}: {reason}")

    async def _record_touch(self, task, session, spec, result: ExecutionResult, used) -> None:
        """Successful external actions become outbound interactions (memory + frequency caps)."""
        from app.models.base import Interaction, Lead
        if spec.name == "send_email" and task.lead_id:
            lead = await session.get(Lead, task.lead_id)
            if lead is not None and lead.status in ("new", "enriching", "qualified"):
                lead.status = "contacted"   # pipeline stage follows the real send, never the proposal
        if result.result.get("interaction_recorded"):
            return
        touch_type = {"send_email": "email_sent", "book_meeting": "meeting"}.get(spec.name, "note")
        payload = _loads(task.payload)
        session.add(Interaction(
            tenant_id=task.tenant_id, lead_id=task.lead_id, interaction_type=touch_type, direction="outbound",
            subject=payload.get("subject") or result.result.get("subject"), body=payload.get("body"),
            channel_message_id=str(result.result.get("message_id") or result.result.get("meeting_id") or "") or None,
            occurred_at=_now(), task_id=task.id, channel=payload.get("channel") or spec.category,
            provider=used.name if used else None, details={"mode": used.config.mode if used else None,
                                                             "result": result.result},
        ))
        await session.flush()

    @staticmethod
    async def _spent_today(session, tenant_id) -> float:
        from sqlalchemy import text as sql
        row = await session.execute(sql(
            "SELECT coalesce(sum((payload::jsonb ->> 'cost')::float), 0) FROM events "
            "WHERE tenant_id = :t AND event_type = 'task_completed' AND occurred_at >= date_trunc('day', now())"),
            {"t": tenant_id})
        return float(row.scalar() or 0.0)

    @staticmethod
    async def _sync_decision(session, task, status: str) -> None:
        """Keep the decision history in step with what happened to the task it created."""
        from sqlalchemy import update
        from app.models.memory import AgentDecision
        await session.execute(update(AgentDecision).where(AgentDecision.task_id == task.id)
                              .values(status=status, updated_at=_now()))

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _category(result: ExecutionResult) -> Optional[str]:
        if result.metadata.get("retryable") is True:
            return "unavailable"
        return _CATEGORY.get(result.error_code or "")

    @staticmethod
    def _retry_policy(cfg: GTMConfig, capability: str) -> RetryPolicy:
        policy = cfg.capabilities.get(capability)
        return policy.retry if policy is not None and policy.retry is not None else cfg.execution.retry

    @staticmethod
    def _common(task: Task, capability: str, used: Optional[Candidate], attempts: list) -> dict[str, Any]:
        return {
            "task_id": str(task.id), "lead_id": _s(task.lead_id), "task_type": task.task_type,
            "capability": capability, "provider": used.name if used else None,
            "mode": used.config.mode if used else None,
            "duration_ms": attempts[-1]["duration_ms"] if attempts else None,
            "attempts": attempts, "idempotency_key": task.idempotency_key,
            "cost": used.config.cost_per_call if used else 0.0,
        }


def _loads(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except Exception:
        return {"raw_payload": raw}


def _s(value: Any) -> Optional[str]:
    return str(value) if value is not None else None
