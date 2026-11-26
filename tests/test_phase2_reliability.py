"""
Phase 2 — identity resolution & duplicate prevention, Redis-shared provider
health / rate limits, retries, recovery and idempotency.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.gtm_config import ProviderConfig, RateLimitConfig
from app.core.tenancy import DEFAULT_TENANT_ID
from app.execution.models import ExecutionResult, InvalidStateTransitionError
from app.integrations.lead_source_record import LeadSourceRecord
from app.providers.runtime import RedisProviderHealth, RedisRateLimiter
from tests.test_phase1_platform import FakeProvider, pc


def rec(**kw) -> LeadSourceRecord:
    base = dict(source="phase2", external_id=None, first_name="Dana", last_name="Lee",
                email=None, company_name="Acme Robotics", company_domain=None)
    base.update(kw)
    return LeadSourceRecord(**base)


def uniq(prefix="x") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _db_ok() -> bool:
    from app.core.database import check_db_health
    return (await check_db_health())["status"] == "ok"


@pytest.fixture
async def fresh_pool():
    from app.core.database import engine
    await engine.dispose()
    yield
    await engine.dispose()


# ── Identity resolution & ingestion ───────────────────────────────────────────

@pytest.mark.usefixtures("fresh_pool")
class TestIdentityPhase2:
    async def _ingest(self, records, tenant=None):
        from app.core.database import AsyncSessionLocal
        from app.services.lead_ingestion import LeadIngestionService
        async with AsyncSessionLocal() as s:
            summary = await LeadIngestionService().ingest_batch(records, s, "phase2", tenant_id=tenant)
            await s.commit()
        return summary

    @pytest.mark.anyio
    async def test_reimport_creates_no_duplicates(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        domain = uniq("acme") + ".io"
        r = rec(email=f"dana@{domain}", company_domain=domain, external_id=uniq("ext"))
        first = await self._ingest([r])
        second = await self._ingest([r])
        assert (first.companies_created, first.people_created, first.leads_created) == (1, 1, 1)
        assert (second.companies_created, second.people_created, second.leads_created, second.leads_existing) == (0, 0, 0, 1)

    @pytest.mark.anyio
    async def test_source_record_matches_even_if_email_changed(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Person, SourceRecord
        domain, ext = uniq("src") + ".io", uniq("ext")
        await self._ingest([rec(email=f"dana@{domain}", company_domain=domain, external_id=ext)])
        second = await self._ingest([rec(email=f"dana.lee@{domain}", company_domain=domain, external_id=ext)])
        assert second.people_created == 0 and second.leads_created == 0
        async with AsyncSessionLocal() as s:
            sr = (await s.execute(select(SourceRecord).where(SourceRecord.external_id == ext))).scalar_one()
            person = await s.get(Person, sr.person_id)
        assert sr.times_seen == 2
        assert person.email == f"dana@{domain}"  # identity key not overwritten

    @pytest.mark.anyio
    async def test_linkedin_and_name_company_matching_and_fill_missing(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Person
        domain = uniq("li") + ".io"
        li = f"https://linkedin.com/in/{uniq('dana')}"
        await self._ingest([rec(company_domain=domain, linkedin_url=li)])          # no email at first
        s2 = await self._ingest([rec(company_domain=domain, linkedin_url=li,
                                     email=f"dana@{domain}", job_title="VP Sales")])
        s3 = await self._ingest([rec(company_domain=domain, phone="+1 555 0100")])  # name + company only
        assert s2.people_created == 0 and s3.people_created == 0
        async with AsyncSessionLocal() as s:
            people = (await s.execute(select(Person).where(Person.linkedin_url.isnot(None),
                                                           Person.email == f"dana@{domain}"))).scalars().all()
        assert len(people) == 1
        assert people[0].title is not None and people[0].phone is not None  # filled, not duplicated

    @pytest.mark.anyio
    async def test_tenants_are_isolated(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Organization
        other = uuid.uuid4()
        async with AsyncSessionLocal() as s:
            s.add(Organization(id=other, name="Other org", slug=uniq("org")))
            await s.commit()
        domain = uniq("shared") + ".io"
        r = rec(email=f"dana@{domain}", company_domain=domain)
        a = await self._ingest([r])
        b = await self._ingest([r], tenant=other)
        assert a.companies_created == 1 and b.companies_created == 1  # same prospect, two organisations

    @pytest.mark.anyio
    async def test_closed_lead_attached_not_reopened(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Lead
        domain = uniq("closed") + ".io"
        r = rec(email=f"dana@{domain}", company_domain=domain)
        await self._ingest([r])
        async with AsyncSessionLocal() as s:
            lead = (await s.execute(select(Lead).join(Lead.person).where(
                Lead.source == "phase2").order_by(Lead.created_at.desc()))).scalars().first()
            lead.status = "disqualified"
            await s.commit()
            assert lead.is_open is False
        again = await self._ingest([r])
        assert again.leads_created == 0 and again.leads_existing == 1

    @pytest.mark.anyio
    async def test_database_enforces_one_open_lead_per_person(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Lead, Person
        async with AsyncSessionLocal() as s:
            p = Person(first_name="Solo", email=f"{uniq('solo')}@x.io")
            s.add(p)
            await s.flush()
            s.add_all([Lead(person_id=p.id, status="new"), Lead(person_id=p.id, status="qualified")])
            with pytest.raises(IntegrityError):
                await s.flush()

    @pytest.mark.anyio
    async def test_concurrent_imports_do_not_duplicate(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Company
        domain = uniq("race") + ".io"
        records = [rec(email=f"dana@{domain}", company_domain=domain)]
        results = await asyncio.gather(self._ingest(records), self._ingest(records), return_exceptions=True)
        assert not any(isinstance(r, Exception) for r in results), results
        async with AsyncSessionLocal() as s:
            n = (await s.execute(select(func.count()).select_from(Company).where(Company.domain == domain))).scalar()
        assert n == 1


# ── Shared runtime state (Redis) ──────────────────────────────────────────────

def _redis_ok() -> bool:
    try:
        import redis
        from app.core.config import get_settings
        return bool(redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1).ping())
    except Exception:
        return False


class TestRedisRuntimeState:
    @pytest.mark.anyio
    async def test_cooldown_shared_between_workers(self):
        if not _redis_ok():
            pytest.skip("Redis not available")
        prefix = f"gtm-test:{uniq('w')}"
        worker_a, worker_b = RedisProviderHealth(prefix), RedisProviderHealth(prefix)
        for _ in range(3):
            await worker_a.record_failure("vendor", "TIMEOUT", threshold=3, cooldown_s=30)
        assert await worker_b.cooling_down("vendor") is True
        assert (await worker_b.state("vendor"))["cooldown_remaining_s"] > 0
        await worker_b.record_success("vendor")
        assert (await worker_a.state("vendor"))["consecutive_failures"] == 0

    @pytest.mark.anyio
    async def test_rate_limit_shared_between_workers_and_block(self):
        if not _redis_ok():
            pytest.skip("Redis not available")
        prefix = f"gtm-test:{uniq('rl')}"
        a, b = RedisRateLimiter(prefix), RedisRateLimiter(prefix)
        cfg = RateLimitConfig(requests=2, per_seconds=60)
        assert await a.try_acquire("vendor", cfg) and await b.try_acquire("vendor", cfg)
        assert await a.try_acquire("vendor", cfg) is False
        assert await b.has_capacity("vendor", cfg) is False
        await a.block("other", 30)
        assert await b.has_capacity("other", RateLimitConfig()) is False

    @pytest.mark.anyio
    async def test_falls_back_to_memory_when_redis_down(self):
        import redis.asyncio as aioredis
        dead = aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
        health = RedisProviderHealth("x", allow_fallback=True, client_factory=lambda: dead)
        for _ in range(2):
            await health.record_failure("v", "TIMEOUT", threshold=2, cooldown_s=30)
        assert await health.cooling_down("v") is True  # served by the in-memory fallback

    def test_global_selector_uses_redis_backend(self):
        from app.providers import get_provider_selector
        sel = get_provider_selector()
        assert isinstance(sel.health, RedisProviderHealth) and isinstance(sel.rate_limiter, RedisRateLimiter)


# ── Retries, ambiguity safety, recovery, idempotency ─────────────────────────

@pytest.mark.usefixtures("fresh_pool")
class TestExecutionReliability:
    async def _task(self, task_type="research_prospect", **kw):
        from app.core.database import AsyncSessionLocal
        from app.models.base import Lead, Task
        from tests.compliance_helpers import approve, make_contactable
        async with AsyncSessionLocal() as s:
            lead = Lead(source="phase2", status="new")
            s.add(lead)
            await s.flush()
            t = Task(lead_id=lead.id, task_type=task_type, status="pending", payload="{}", **kw)
            s.add(t)
            await s.flush()
            if task_type in ("send_email", "book_meeting"):
                await make_contactable(s, lead)
                await approve(s, t)
            await s.commit()
            return t.id

    async def _execute(self, task_id, providers, force=False):
        from app.core.database import AsyncSessionLocal
        from app.execution.executor import TaskExecutor
        from app.execution.registry import ExecutionRegistry
        reg = ExecutionRegistry()
        for inst, cfg in providers:
            reg.register(inst, config=cfg)
        async with AsyncSessionLocal() as s:
            return await TaskExecutor(reg).execute(task_id, s, force=force)

    async def _get(self, task_id):
        from app.core.database import AsyncSessionLocal
        from app.models.base import Task
        async with AsyncSessionLocal() as s:
            return await s.get(Task, task_id)

    @staticmethod
    def quick(name, caps=("research_prospect",), **kw):
        kw.setdefault("resilience", {"max_retries": 0, "timeout_s": 2})
        return pc(name, caps, **kw)

    @pytest.mark.anyio
    async def test_transient_failure_schedules_retry_then_succeeds(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        tid = await self._task()
        out = await self._execute(tid, [(FakeProvider("flaky", outcome="raise"), self.quick("flaky"))])
        task = await self._get(tid)
        assert out.event_type == "task_retry_scheduled" and task.status == "pending"
        assert task.attempt_count == 1 and task.next_attempt_at is not None and task.last_error_code == "PROVIDER_EXCEPTION"
        out2 = await self._execute(tid, [(FakeProvider("ok"), self.quick("ok"))])
        task = await self._get(tid)
        assert out2.success and task.status == "completed" and task.attempt_count == 2

    @pytest.mark.anyio
    async def test_future_retry_is_deferred_unless_forced(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        tid = await self._task(next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1))
        out = await self._execute(tid, [(FakeProvider("ok"), self.quick("ok"))])
        assert out.event_type == "task_deferred" and (await self._get(tid)).status == "pending"
        forced = await self._execute(tid, [(FakeProvider("ok"), self.quick("ok"))], force=True)
        assert forced.success

    @pytest.mark.anyio
    async def test_attempts_exhausted_fail_terminally(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        tid = await self._task(max_attempts=1)
        out = await self._execute(tid, [(FakeProvider("flaky", outcome="raise"), self.quick("flaky"))])
        assert out.event_type == "task_failed" and (await self._get(tid)).status == "failed"

    @pytest.mark.anyio
    async def test_ambiguous_external_action_not_retried_or_failed_over(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        tid = await self._task("send_email")
        backup = FakeProvider("backup_mailer", caps=("send_email",))
        out = await self._execute(tid, [
            (FakeProvider("mailer", caps=("send_email",), outcome="raise"),
             self.quick("mailer", ("send_email",), priority=90, resilience={"max_retries": 2, "timeout_s": 2})),
            (backup, self.quick("backup_mailer", ("send_email",), priority=10)),
        ])
        task = await self._get(tid)
        assert out.event_type == "task_failed" and task.needs_review is True
        assert backup.calls == 0                         # never sent through a second provider
        assert json.loads(task.result)["needs_review"] is True

    @pytest.mark.anyio
    async def test_idempotent_vendor_may_fail_over_external_action(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        tid = await self._task("send_email")
        out = await self._execute(tid, [
            (FakeProvider("mailer", caps=("send_email",), outcome="raise"),
             self.quick("mailer", ("send_email",), priority=90, supports_idempotency=True)),
            (FakeProvider("backup_mailer", caps=("send_email",)),
             self.quick("backup_mailer", ("send_email",), priority=10, supports_idempotency=True)),
        ])
        assert out.success

    @pytest.mark.anyio
    async def test_vendor_rate_limit_blocks_provider_and_reschedules(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")

        class Limited(FakeProvider):
            async def execute(self, context, session=None):
                return ExecutionResult(success=False, provider="limited", error_code="RATE_LIMITED",
                                       metadata={"retry_after_s": 120})
        tid = await self._task()
        out = await self._execute(tid, [(Limited("limited"), self.quick("limited"))])
        task = await self._get(tid)
        assert out.event_type == "task_retry_scheduled"
        assert task.next_attempt_at > datetime.now(timezone.utc) + timedelta(seconds=100)

    @pytest.mark.anyio
    async def test_business_failure_does_not_hurt_provider_health(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.execution.executor import TaskExecutor
        from app.execution.registry import ExecutionRegistry
        from app.core.database import AsyncSessionLocal
        reg = ExecutionRegistry()
        reg.register(FakeProvider("picky", outcome="fail"), config=self.quick("picky"))
        ex = TaskExecutor(reg)
        for _ in range(4):
            tid = await self._task()
            async with AsyncSessionLocal() as s:
                await ex.execute(tid, s)
        assert await ex.selector.health.cooling_down("picky") is False


@pytest.mark.usefixtures("fresh_pool")
class TestRecovery:
    async def _failed_task(self, **kw):
        from app.core.database import AsyncSessionLocal
        from app.models.base import Lead, Task
        async with AsyncSessionLocal() as s:
            lead = Lead(source="phase2", status="new")
            s.add(lead)
            await s.flush()
            t = Task(lead_id=lead.id, status="failed", payload="{}", attempt_count=4, **kw)
            s.add(t)
            await s.commit()
            return t.id

    @pytest.mark.anyio
    async def test_retry_reuses_same_task_row(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.execution.recovery import retry_failed_task
        from app.models.base import Task
        tid = await self._failed_task(task_type="research_prospect")
        async with AsyncSessionLocal() as s:
            before = (await s.execute(select(func.count()).select_from(Task))).scalar()
            task = await retry_failed_task(s, tid, requested_by="ops@test", reason="vendor fixed")
            await s.commit()
            after = (await s.execute(select(func.count()).select_from(Task))).scalar()
        assert task.id == tid and task.status == "pending" and task.max_attempts > 4
        assert before == after

    @pytest.mark.anyio
    async def test_needs_review_requires_acknowledgement(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.execution.recovery import RecoveryRefused, retry_failed_task
        tid = await self._failed_task(task_type="send_email", needs_review=True)
        async with AsyncSessionLocal() as s:
            with pytest.raises(RecoveryRefused):
                await retry_failed_task(s, tid, requested_by="ops@test")
        async with AsyncSessionLocal() as s:
            task = await retry_failed_task(s, tid, requested_by="ops@test", acknowledge_review=True,
                                           reason="confirmed not delivered")
            await s.commit()
            assert task.status == "pending" and task.needs_review is False

    @pytest.mark.anyio
    async def test_completed_task_cannot_be_retried(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.execution.recovery import retry_failed_task
        from app.models.base import Lead, Task
        async with AsyncSessionLocal() as s:
            lead = Lead(source="phase2", status="new")
            s.add(lead)
            await s.flush()
            t = Task(lead_id=lead.id, task_type="research_prospect", status="completed")
            s.add(t)
            await s.commit()
            with pytest.raises(InvalidStateTransitionError):
                await retry_failed_task(s, t.id, requested_by="ops@test")

    @pytest.mark.anyio
    async def test_stale_recovery_resumes_safe_and_flags_external(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.execution.recovery import recover_stale_tasks
        from app.models.base import Lead, Task
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        async with AsyncSessionLocal() as s:
            lead = Lead(source="phase2", status="new")
            s.add(lead)
            await s.flush()
            safe = Task(lead_id=lead.id, task_type="research_prospect", status="in_progress", lease_expires_at=past)
            risky = Task(lead_id=lead.id, task_type="send_email", status="in_progress", lease_expires_at=past)
            s.add_all([safe, risky])
            await s.commit()
            report = await recover_stale_tasks(s)
            await s.commit()
            await s.refresh(safe)
            await s.refresh(risky)
        assert safe.id in report.resumed and safe.status == "pending"
        assert risky.id in report.flagged_for_review and risky.status == "failed" and risky.needs_review


@pytest.mark.usefixtures("fresh_pool")
class TestIdempotency:
    @pytest.mark.anyio
    async def test_unique_idempotency_key_per_tenant(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        from app.core.database import AsyncSessionLocal
        from app.models.base import Task
        key = f"send_email:{uniq('lead')}:draft_task_id=1"
        async with AsyncSessionLocal() as s:
            s.add(Task(task_type="send_email", status="completed", idempotency_key=key))
            await s.commit()
            s.add(Task(task_type="send_email", status="pending", idempotency_key=key))
            with pytest.raises(IntegrityError):
                await s.commit()

    @pytest.mark.anyio
    async def test_guardrail_blocks_repeat_of_recorded_action(self):
        from app.guardrails.evaluator import GuardrailEvaluator
        from app.guardrails.models import GuardrailContext
        from app.guardrails.registry import create_default_guardrail_registry
        lead, draft = uuid.uuid4(), uuid.uuid4()
        key = f"send_email:{lead}:draft_task_id={draft}"
        ctx = GuardrailContext(lead_id=lead, target_id=lead, action="send_email", task_type="send_email",
                               parameters={"draft_task_id": str(draft)},
                               entity_facts={"person.email": True},
                               task_history=[{"id": "t1", "task_type": "send_email", "status": "failed",
                                              "idempotency_key": key,
                                              "completed_at": (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()}])
        res = await GuardrailEvaluator(create_default_guardrail_registry()).evaluate(ctx)
        assert not res.allowed and "GR-010" in res.rule_ids

    def test_only_declared_capabilities_get_keys(self):
        from app.capabilities import get_capability_catalogue
        cat = get_capability_catalogue()
        assert cat.get("enrich_company").idempotency_key("L", {"domain": "x"}) is None
        assert cat.get("send_email").idempotency_key("L", {"draft_task_id": "D"}) == "send_email:L:draft_task_id=D"
