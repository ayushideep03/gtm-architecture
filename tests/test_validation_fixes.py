"""
Regression tests for defects found by the live end-to-end validation run:

- email providers could never be enabled from config (host/from_address were read from settings)
- a crash during an external call rolled the task back to 'pending' -> the email would be re-sent
- a wake-up merged into a work item that was being processed was lost
- two workers attributing outcomes at once crashed the tick (unique violation)
- one prospect's failing cycle aborted the whole tick
- API had no authentication; reviewers were free text
- website research could be pointed at internal addresses (SSRF)
- meeting topics could inject iCalendar properties
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.gtm_config import load_gtm_config
from app.execution.models import ConcurrentExecutionError
from tests.test_phase1_platform import FakeProvider
import tests.test_phase2_reliability as _rel
from tests.test_phase2_reliability import fresh_pool  # noqa: F401 (fixture)


# ── Provider construction from the shipped config ─────────────────────────────

class TestEmailProvidersFromConfig:
    """Built exactly as the app builds them: from config/gtm.yaml + environment variables."""

    @staticmethod
    def _build(name):
        from app.providers.loader import build_provider
        return build_provider(load_gtm_config(profile="production").provider(name))

    def test_smtp_available_with_host_and_sender_only(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.internal.test")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "alex@oxygen.test")
        monkeypatch.delenv("SMTP_USERNAME", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        for name in ("smtp_email", "smtp_meetings"):
            inst, reason = self._build(name)
            assert reason is None and inst.host == "smtp.internal.test" and inst.username is None

    def test_smtp_login_needs_both_halves(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.internal.test")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "alex@oxygen.test")
        monkeypatch.setenv("SMTP_USERNAME", "alex")
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        inst, reason = self._build("smtp_email")
        assert inst is None and "SMTP_PASSWORD" in reason
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        inst, reason = self._build("smtp_email")
        assert reason is None and (inst.username, inst.password) == ("alex", "secret")

    def test_smtp_unavailable_without_host(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "alex@oxygen.test")
        inst, reason = self._build("smtp_email")
        assert inst is None and "SMTP_HOST" in reason

    def test_imap_available_from_env(self, monkeypatch):
        for k, v in {"IMAP_HOST": "imap.internal.test", "IMAP_USERNAME": "u", "IMAP_PASSWORD": "p"}.items():
            monkeypatch.setenv(k, v)
        inst, reason = self._build("imap_inbox")
        assert reason is None and inst.host == "imap.internal.test"

    def test_billionmail_available_from_env(self, monkeypatch):
        for k, v in {"BILLIONMAIL_URL": "https://bm.internal.test", "BILLIONMAIL_API_KEY": "k",
                     "BILLIONMAIL_ADDRESSER": "alex@oxygen.test"}.items():
            monkeypatch.setenv(k, v)
        inst, reason = self._build("billionmail")
        assert reason is None and inst is not None


# ── Crash during an external call ─────────────────────────────────────────────

class _CrashingMailer(FakeProvider):
    """Simulates the worker process dying while the SMTP call is in flight."""

    async def execute(self, context, session=None):
        self.calls += 1
        raise asyncio.CancelledError()


@pytest.mark.usefixtures("fresh_pool")
class TestCrashMidSend:
    h = _rel.TestExecutionReliability()     # reuse its task/execute helpers (composition, not inheritance)

    @pytest.mark.anyio
    async def test_crash_leaves_task_leased_and_it_is_never_resent(self):
        self._task, self._execute, self._get, self.quick = self.h._task, self.h._execute, self.h._get, self.h.quick
        from app.core.database import AsyncSessionLocal
        from app.execution.recovery import recover_stale_tasks
        tid = await self._task("send_email")
        crash = _CrashingMailer("mailer", caps=("send_email",))
        with pytest.raises(asyncio.CancelledError):
            await self._execute(tid, [(crash, self.quick("mailer", ("send_email",)))])
        task = await self._get(tid)
        # the claim was committed before the call: the task did NOT roll back to 'pending'
        assert task.status == "in_progress" and task.lease_expires_at is not None and crash.calls == 1

        # another worker (or the next tick) refuses to run it again
        second = FakeProvider("mailer2", caps=("send_email",))
        with pytest.raises(ConcurrentExecutionError):
            await self._execute(tid, [(second, self.quick("mailer2", ("send_email",)))])
        assert second.calls == 0

        # once the lease expires, recovery flags it for a human instead of re-sending
        async with AsyncSessionLocal() as s:
            report = await recover_stale_tasks(s, now=task.lease_expires_at + timedelta(seconds=1))
            await s.commit()
        task = await self._get(tid)
        assert tid in report.flagged_for_review and task.status == "failed" and task.needs_review is True


# ── Work queue: no lost wake-ups ───────────────────────────────────────────────

class TestWorkQueueRerun:
    @pytest.mark.anyio
    async def test_wakeup_during_processing_reruns_the_item(self):
        from app.core.database import AsyncSessionLocal
        from app.memory.records import claim_due, complete_work, schedule_work
        from app.models.base import Lead
        from app.models.memory import WorkItem
        async with AsyncSessionLocal() as s:
            lead = Lead(source="rerun-test", status="new")
            s.add(lead)
            await s.flush()
            await schedule_work(s, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="first")
            await s.commit()
            lead_id, tenant = lead.id, lead.tenant_id

        async with AsyncSessionLocal() as s:
            [item] = [i for i in await claim_due(s, worker="w1", kinds=["evaluate"], limit=100) if i.lead_id == lead_id]
            await s.commit()
        async with AsyncSessionLocal() as s:   # a reply arrives while w1 is still evaluating
            again = await schedule_work(s, tenant_id=tenant, lead_id=lead_id, kind="evaluate",
                                        reason="New event: reply_received")
            await s.commit()
        assert again.id == item.id            # merged (one open item per lead and kind) ...
        async with AsyncSessionLocal() as s:
            row = await s.get(WorkItem, item.id, with_for_update=True)
            await complete_work(s, row, "success")
            await s.commit()
        async with AsyncSessionLocal() as s:
            row = await s.get(WorkItem, item.id)
            assert row.finished_at is None and row.leased_until is None   # ... and NOT lost: runs again
            reclaimed = [i for i in await claim_due(s, worker="w2", kinds=["evaluate"], limit=100) if i.lead_id == lead_id]
            assert [i.id for i in reclaimed] == [item.id]
            await complete_work(s, reclaimed[0], "success")
            await s.commit()
            assert (await s.get(WorkItem, item.id)).finished_at is not None

        try:
            async with AsyncSessionLocal() as s:
                await s.execute(WorkItem.__table__.delete().where(WorkItem.lead_id == lead_id))
                await s.execute(Lead.__table__.delete().where(Lead.id == lead_id))
                await s.commit()
        except Exception:
            pass


# ── Concurrency: attribution and tick isolation ───────────────────────────────

class TestConcurrentWorkers:
    @pytest.mark.anyio
    async def test_only_one_worker_attributes_at_a_time(self):
        from app.core.database import AsyncSessionLocal
        from app.revops.outcomes import attribute_outcomes
        from sqlalchemy import text
        from app.revops.outcomes import ATTRIBUTION_LOCK_KEY
        async with AsyncSessionLocal() as holder, AsyncSessionLocal() as other:
            assert (await holder.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                                         {"k": ATTRIBUTION_LOCK_KEY})).scalar() is True
            assert await attribute_outcomes(other) == 0      # skipped, no duplicate inserts
            await holder.rollback()
            await other.rollback()

    @pytest.mark.anyio
    async def test_one_failing_cycle_does_not_stop_the_tick(self, monkeypatch):
        from app.core.database import AsyncSessionLocal
        from app.memory.records import schedule_work
        from app.models.base import Lead
        from app.models.memory import WorkItem
        from app.services import autonomy

        async with AsyncSessionLocal() as s:
            leads = [Lead(source="tick-test", status="new") for _ in range(2)]
            s.add_all(leads)
            await s.flush()
            for lead in leads:
                await schedule_work(s, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="t",
                                    priority=99)
            await s.commit()
            bad, good = leads[0].id, leads[1].id

        seen = []

        async def fake_cycle(lead_id, **_):
            seen.append(lead_id)
            if lead_id == bad:
                raise RuntimeError("boom")
            return {"status": "no_action", "decision": None}

        monkeypatch.setattr(autonomy, "run_lead_cycle", fake_cycle)
        monkeypatch.setattr(autonomy, "is_paused", lambda: _false())
        report = await autonomy.tick(worker="t")
        statuses = {c["lead_id"]: c["status"] for c in report["cycles"]}
        assert statuses.get(str(bad)) == "error" and statuses.get(str(good)) == "no_action"
        async with AsyncSessionLocal() as s:
            done = {w.lead_id: w.finished_at for w in (await s.execute(
                select(WorkItem).where(WorkItem.lead_id.in_([bad, good])))).scalars()}
            assert done[good] is not None and done[bad] is None     # the failed one is retried later
            await s.execute(WorkItem.__table__.delete().where(WorkItem.lead_id.in_([bad, good])))
            await s.execute(Lead.__table__.delete().where(Lead.id.in_([bad, good])))
            await s.commit()


async def _false():
    return False


# ── Authentication ────────────────────────────────────────────────────────────

KEY = "k" * 24


class TestApiAuthentication:
    @pytest.mark.anyio
    async def test_keys_required_when_configured(self, app, monkeypatch):
        monkeypatch.setenv("OXYGEN_API_KEYS", f"alex={KEY}")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/v1/approvals")).status_code == 401
            assert (await c.get("/api/v1/approvals", headers={"X-API-Key": "wrong" * 5})).status_code == 401
            assert (await c.get("/api/v1/approvals", headers={"X-API-Key": KEY})).status_code == 200
            assert (await c.get("/api/v1/approvals", headers={"Authorization": f"Bearer {KEY}"})).status_code == 200
            # public: liveness and sign-in
            assert (await c.get("/api/v1/health")).status_code == 200
            assert (await c.get("/api/v1/auth/me")).json() == {"auth_required": True, "configured": True,
                                                                "principal": None}

    @pytest.mark.anyio
    async def test_dashboard_login_sets_httponly_cookie(self, app, monkeypatch):
        monkeypatch.setenv("OXYGEN_API_KEYS", f"alex={KEY}")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/v1/auth/login", json={"api_key": "nope"})).status_code == 401
            r = await c.post("/api/v1/auth/login", json={"api_key": KEY})
            assert r.status_code == 200 and r.json() == {"principal": "alex"}
            cookie = r.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie
            assert (await c.get("/api/v1/approvals")).status_code == 200       # cookie sent automatically
            assert (await c.get("/api/v1/auth/me")).json()["principal"] == "alex"
            await c.post("/api/v1/auth/logout")
            c.cookies.clear()
            assert (await c.get("/api/v1/approvals")).status_code == 401

    @pytest.mark.anyio
    async def test_fail_closed_outside_local_profiles(self, app, monkeypatch):
        from app.core import auth
        monkeypatch.delenv("OXYGEN_API_KEYS", raising=False)
        monkeypatch.setattr(auth, "_profile", lambda: "production")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/v1/approvals")).status_code == 503
            assert (await c.get("/api/v1/health")).status_code == 200

    @pytest.mark.anyio
    async def test_local_profile_open_but_reviewer_marked_unverified(self, monkeypatch):
        from types import SimpleNamespace
        from app.core.auth import LOCAL_PRINCIPAL, Principal, reviewer_for
        monkeypatch.delenv("OXYGEN_API_KEYS", raising=False)
        local = SimpleNamespace(state=SimpleNamespace(principal=Principal(LOCAL_PRINCIPAL, authenticated=False)))
        signed = SimpleNamespace(state=SimpleNamespace(principal=Principal("alex", authenticated=True)))
        assert reviewer_for(local, "ceo@corp") == "ceo@corp (unverified, local-dev)"
        assert reviewer_for(signed, "ceo@corp") == "alex"       # the body cannot impersonate

    @pytest.mark.anyio
    async def test_webhooks_are_not_behind_api_keys_but_signed(self, app, monkeypatch):
        monkeypatch.setenv("OXYGEN_API_KEYS", f"alex={KEY}")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/webhooks/email", json={"type": "opened"})
            assert r.status_code != 401 or "signature" in r.text


# ── SSRF guard ────────────────────────────────────────────────────────────────

class TestResearchSsrfGuard:
    @pytest.mark.anyio
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "169.254.169.254", "10.0.0.5", "localhost:8000",
                                      "metadata.google.internal", "printer.local", "[::1]"])
    async def test_internal_targets_refused(self, host):
        from app.capabilities.web import UnsafeTarget, resolve_public
        with pytest.raises(UnsafeTarget):
            await resolve_public(host)

    def test_public_ip_classification(self):
        from app.capabilities.web import is_public_ip
        assert is_public_ip("93.184.216.34") and not is_public_ip("192.168.1.1") and not is_public_ip("::1")

    @pytest.mark.anyio
    async def test_fetcher_makes_no_request_for_internal_domain(self):
        from app.capabilities.web import WebsiteFetcher
        from app.core.gtm_config import ResearchPolicy
        calls = []
        fetcher = WebsiteFetcher(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)),
                                 check_dns=True)
        assert await fetcher.fetch_site("169.254.169.254", ResearchPolicy()) == [] and calls == []

    @pytest.mark.anyio
    async def test_offsite_redirect_not_followed_and_body_capped(self):
        from app.capabilities.web import WebsiteFetcher
        from app.core.gtm_config import ResearchPolicy
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path == "/":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            if request.url.path == "/about":
                return httpx.Response(200, headers={"content-type": "text/html"},
                                      content=b"<html><body>" + b"A" * 2_000_000 + b"</body></html>")
            return httpx.Response(404)

        policy = ResearchPolicy(paths=["/", "/about"], respect_robots_txt=False, max_chars_per_page=1000)
        pages = await WebsiteFetcher(transport=httpx.MockTransport(handler)).fetch_site("acme.test", policy)
        assert not any("169.254" in u for u in seen)              # the redirect target was never requested
        assert len(pages) == 1 and len(pages[0].text) <= 1000


# ── iCalendar injection ───────────────────────────────────────────────────────

class TestIcsEscaping:
    def test_topic_cannot_add_properties(self):
        from app.integrations.email_smtp import ics_param, ics_text
        out = ics_text("Intro\r\nATTENDEE:mailto:x@evil.test; a, b")
        assert "\r" not in out and "\n" not in out and out.startswith("Intro\\nATTENDEE")
        assert "\\;" in out and "\\," in out
        assert ics_param('Alex "M";\r\nX') == '"Alex MX"' or ics_param('Alex "M";\r\nX') == '"Alex M X"'


class TestWorkQueueScheduledRecheck:
    @pytest.mark.anyio
    async def test_wait_decided_during_processing_is_not_lost(self):
        """Oxygen decides 'wait 72h' inside the cycle that is processing the lead's evaluate item.
        Before the fix the re-check was merged into that item and then completed: the follow-up
        never happened."""
        from app.core.database import AsyncSessionLocal
        from app.memory.records import claim_due, complete_work, schedule_work
        from app.models.base import Lead
        from app.models.memory import WorkItem
        async with AsyncSessionLocal() as s:
            lead = Lead(source="wait-test", status="contacted")
            s.add(lead)
            await s.flush()
            await schedule_work(s, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="sent")
            await s.commit()
            lead_id, tenant = lead.id, lead.tenant_id
        async with AsyncSessionLocal() as s:
            [item] = [i for i in await claim_due(s, worker="w", kinds=["evaluate"], limit=100) if i.lead_id == lead_id]
            await s.commit()
        later = datetime.now(timezone.utc) + timedelta(hours=72)
        async with AsyncSessionLocal() as s:    # the cycle's own decision
            await schedule_work(s, tenant_id=tenant, lead_id=lead_id, kind="evaluate", reason="wait 72h", due_at=later)
            await s.commit()
        async with AsyncSessionLocal() as s:    # the tick completes the item it claimed
            await complete_work(s, await s.get(WorkItem, item.id, with_for_update=True), "waiting")
            await s.commit()
        async with AsyncSessionLocal() as s:
            row = await s.get(WorkItem, item.id)
            assert row.finished_at is None and abs((row.due_at - later).total_seconds()) < 1
            assert "_requeue_at" not in (row.payload or {})
            # and it is not claimable before then (no busy loop)
            assert not [i for i in await claim_due(s, worker="w", kinds=["evaluate"], limit=100) if i.lead_id == lead_id]
            await s.rollback()
            await s.execute(WorkItem.__table__.delete().where(WorkItem.lead_id == lead_id))
            await s.execute(Lead.__table__.delete().where(Lead.id == lead_id))
            await s.commit()

    @pytest.mark.anyio
    async def test_stop_closes_item_even_with_pending_requeue(self):
        from app.core.database import AsyncSessionLocal
        from app.memory.records import claim_due, complete_work, schedule_work
        from app.models.base import Lead
        from app.models.memory import WorkItem
        async with AsyncSessionLocal() as s:
            lead = Lead(source="stop-test", status="new")
            s.add(lead)
            await s.flush()
            await schedule_work(s, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="x")
            await s.commit()
            lead_id = lead.id
        async with AsyncSessionLocal() as s:
            [item] = [i for i in await claim_due(s, worker="w", kinds=["evaluate"], limit=100) if i.lead_id == lead_id]
            await schedule_work(s, tenant_id=item.tenant_id, lead_id=lead_id, kind="evaluate", reason="event")
            await complete_work(s, item, "stopped_by_oxygen", force=True)
            await s.commit()
            assert (await s.get(WorkItem, item.id)).finished_at is not None
            await s.execute(WorkItem.__table__.delete().where(WorkItem.lead_id == lead_id))
            await s.execute(Lead.__table__.delete().where(Lead.id == lead_id))
            await s.commit()


class TestReactionCursorRace:
    @pytest.mark.anyio
    async def test_two_workers_start_on_empty_cursor(self):
        from app.core.database import AsyncSessionLocal
        from app.models.base import EventProcessingState
        from app.services.autonomy import REACTIONS_CONSUMER, react_to_events
        async with AsyncSessionLocal() as s:
            saved = await s.get(EventProcessingState, REACTIONS_CONSUMER)
            snapshot = None if saved is None else (saved.last_processed_timestamp, saved.last_processed_event_id,
                                                   saved.processed_count)
            await s.execute(EventProcessingState.__table__.delete().where(
                EventProcessingState.consumer_name == REACTIONS_CONSUMER))
            await s.commit()

        async def one():
            async with AsyncSessionLocal() as s:
                await react_to_events(s, limit=1)
                await s.commit()

        await asyncio.gather(one(), one())      # before the fix: UniqueViolation on the cursor row
        async with AsyncSessionLocal() as s:
            row = await s.get(EventProcessingState, REACTIONS_CONSUMER)
            assert row is not None
            if snapshot:
                row.last_processed_timestamp, row.last_processed_event_id, row.processed_count = snapshot
            await s.commit()
