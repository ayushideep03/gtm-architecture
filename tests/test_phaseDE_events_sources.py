"""
Phases D & E — inbound events (replies, bounces, engagement, meetings), the
event-driven autonomy loop, and lead sources (CSV, inbound forms, Apollo, Scout).
"""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.gtm_config import GTMConfig, load_gtm_config
from app.events.inbound import (
    CalendarEvent,
    EmailEvent,
    SignatureError,
    detect_opt_out,
    process_calendar_event,
    process_email_event,
    sign,
    verify_signature,
)
from app.llm import LLMRouter
from tests.test_phaseA_memory import _prospect, db  # noqa: F401
from tests.test_phaseB_controller import Scripted


async def _sent_email(db, lead, message_id="msg-123", subject="Your SDR expansion"):
    from app.models.base import Interaction
    db.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound", subject=subject,
                       channel_message_id=message_id, occurred_at=datetime.now(timezone.utc) - timedelta(days=3)))
    await db.flush()


def classifier(category, quotes, **kw):
    data = {"category": category, "summary": f"{category} reply", "objection": kw.get("objection"),
            "follow_up_after_days": kw.get("days"), "referral": None, "quotes": quotes}
    return LLMRouter(clients={"openai": Scripted("openai", data)})


# ── Signatures ───────────────────────────────────────────────────────────────

class TestSignatures:
    def test_hmac_verification(self, monkeypatch):
        cfg = GTMConfig()
        body = b'{"type":"reply"}'
        monkeypatch.setenv(cfg.webhooks.secret_env, "s3cret")
        ts = str(int(time.time()))
        verify_signature(body, sign("s3cret", ts, body), ts, cfg)
        for sig, stamp in ((sign("wrong", ts, body), ts), (None, ts), (sign("s3cret", "1", body), "1")):
            with pytest.raises(SignatureError):
                verify_signature(body, sig, stamp, cfg)
        monkeypatch.setenv(cfg.webhooks.secret_env, "")
        with pytest.raises(SignatureError):
            verify_signature(body, None, None, cfg)          # production: no secret => reject
        verify_signature(body, None, None, GTMConfig(webhooks={"allow_unsigned": True}))

    def test_opt_out_ignores_quoted_text(self):
        cfg = load_gtm_config("test")
        assert detect_opt_out("Please remove me from your list.", cfg)
        assert detect_opt_out("No thanks.", cfg)
        quoted = "Sounds interesting, send details.\n\nOn Tue, Alex wrote:\n> reply \"no thanks\" and I won't follow up\n> unsubscribe"
        assert detect_opt_out(quoted, cfg) is None


# ── Inbound processing ───────────────────────────────────────────────────────

class TestInboundEvents:
    @pytest.mark.anyio
    async def test_opt_out_reply_suppresses_and_withdraws_consent(self, db):
        from app.compliance.rules import check_contact_allowed
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        res = await process_email_event(db, EmailEvent(type="reply", in_reply_to="msg-123", from_email=person.email,
                                                        body="Please stop emailing me."),
                                        router=classifier("unsubscribe", ["stop emailing me"]))
        status = await check_contact_allowed(db, lead)
        assert res.matched and "opt-out detected deterministically" in " ".join(res.actions)
        assert status.suppressed and not status.lawful_basis_ok and lead.status == "replied"

    @pytest.mark.anyio
    async def test_reply_classified_with_grounded_quotes_and_objection(self, db):
        from app.models.memory import ResearchFinding
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        body = "Interesting, but we just signed a 2-year contract with another vendor."
        res = await process_email_event(db, EmailEvent(type="reply", in_reply_to="msg-123", from_email=person.email,
                                                        body=body),
                                        router=classifier("objection", ["signed a 2-year contract"],
                                                          objection="Locked into a competitor contract"))
        kinds = (await db.execute(select(ResearchFinding.kind).where(ResearchFinding.lead_id == lead.id))).scalars().all()
        assert res.classification["category"] == "objection" and sorted(kinds) == ["objection", "reply_analysis"]

    @pytest.mark.anyio
    async def test_ungrounded_classification_escalates_to_human(self, db):
        from app.models.memory import WorkItem
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        res = await process_email_event(db, EmailEvent(type="reply", in_reply_to="msg-123", from_email=person.email,
                                                        body="Let's talk next week."),
                                        router=classifier("meeting_request", ["Book me for Friday at 3pm"]))
        item = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id))).scalar_one()
        assert res.classification is None and item.kind == "human_review"

    @pytest.mark.anyio
    async def test_out_of_office_schedules_reevaluation(self, db):
        from app.models.memory import WorkItem
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        await process_email_event(db, EmailEvent(type="reply", in_reply_to="msg-123", from_email=person.email,
                                                 body="I'm out of office until the 14th."),
                                  router=classifier("out_of_office", ["out of office until the 14th"], days=9))
        item = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id, WorkItem.kind == "evaluate"))).scalar_one()
        assert item.due_at > datetime.now(timezone.utc) + timedelta(days=8)

    @pytest.mark.anyio
    async def test_bounces_unsubscribes_engagement_duplicates_unmatched(self, db):
        from app.compliance.rules import check_contact_allowed
        from app.models.base import Event
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        soft = await process_email_event(db, EmailEvent(type="bounce", message_id="msg-123", bounce_type="soft"))
        assert not (await check_contact_allowed(db, lead)).suppressed
        await process_email_event(db, EmailEvent(type="opened", message_id="msg-123", provider="esp", event_id="e1"))
        hard = await process_email_event(db, EmailEvent(type="bounce", message_id="msg-123", bounce_type="hard"))
        assert soft.matched and "hard bounce" in hard.actions[0] and (await check_contact_allowed(db, lead)).suppressed
        unmatched = await process_email_event(db, EmailEvent(type="reply", from_email="stranger@nowhere.test", body="hi"))
        assert not unmatched.matched
        types = (await db.execute(select(Event.event_type).where(Event.lead_id == lead.id))).scalars().all()
        assert {"email_bounced", "email_opened"} <= set(types)

    @pytest.mark.anyio
    async def test_duplicate_reply_event_ignored(self, db):
        from app.models.base import Interaction
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead)
        ev = EmailEvent(type="reply", in_reply_to="msg-123", from_email=person.email, body="Tell me more",
                        provider="esp", event_id="evt-9")
        await process_email_event(db, ev, router=classifier("question", ["Tell me more"]))
        again = await process_email_event(db, ev, router=classifier("question", ["Tell me more"]))
        n = (await db.execute(select(func.count()).select_from(Interaction).where(
            Interaction.lead_id == lead.id, Interaction.direction == "inbound"))).scalar()
        assert again.duplicate and n == 1

    @pytest.mark.anyio
    async def test_calendar_booked_and_cancelled(self, db):
        _, person, lead = await _prospect(db)
        res = await process_calendar_event(db, CalendarEvent(type="booked", meeting_id="m1", attendee_email=person.email,
                                                              start=datetime.now(timezone.utc) + timedelta(days=2)))
        assert res.matched and lead.status == "meeting_booked"
        await process_calendar_event(db, CalendarEvent(type="cancelled", meeting_id="m1", attendee_email=person.email))
        assert lead.status == "replied"

    @pytest.mark.anyio
    async def test_webhook_endpoints(self, db, app):
        from httpx import ASGITransport, AsyncClient
        _, person, lead = await _prospect(db)
        await _sent_email(db, lead, message_id="msg-api")
        await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/webhooks/email", json={"type": "bounce", "message_id": "msg-api",
                                                             "bounce_type": "hard"})
            assert r.status_code == 200 and r.json()["matched"]
            r = await c.post("/api/v1/webhooks/calendar", json={"type": "booked", "meeting_id": "x",
                                                                "attendee_email": person.email})
            assert r.status_code == 200
            assert (await c.post("/api/v1/webhooks/email", json={"type": "nope"})).status_code == 422


# ── Autonomy ─────────────────────────────────────────────────────────────────

class TestAutonomy:
    @pytest.mark.anyio
    async def test_events_wake_oxygen_and_cursor_advances(self, db):
        from app.models.memory import WorkItem
        from app.services.autonomy import react_to_events
        from app.models.base import Event
        _, _, lead = await _prospect(db)
        db.add(Event(lead_id=lead.id, event_type="lead_ingested", source="test", payload="{}"))
        db.add(Event(lead_id=lead.id, event_type="email_opened", source="test", payload="{}"))  # not a trigger
        await db.flush()
        assert await react_to_events(db) == 1
        assert await react_to_events(db) == 0
        item = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id))).scalar_one()
        assert item.kind == "evaluate" and item.reason == "New event: lead_ingested"

    @pytest.mark.anyio
    async def test_ticks_drive_the_loop_until_nothing_is_left(self, db):
        from app.models.base import Event
        from app.services.autonomy import tick
        _, _, lead = await _prospect(db)
        lead.status = "new"
        db.add(Event(lead_id=lead.id, event_type="lead_ingested", source="test", payload="{}"))
        await db.commit()
        actions = []
        for _ in range(6):
            report = await tick(worker="test")
            actions += [c["action"] for c in report["cycles"]]
        assert actions[:3] == ["enrich_company", "enrich_person", "qualify_lead"]
        await db.refresh(lead)
        assert lead.status == "qualified"

    @pytest.mark.anyio
    async def test_pause_stops_everything(self, db):
        from app.models.base import Event
        from app.services.autonomy import set_paused, tick
        _, _, lead = await _prospect(db)
        db.add(Event(lead_id=lead.id, event_type="lead_ingested", source="test", payload="{}"))
        await db.commit()
        await set_paused(True, "ops@test")
        try:
            report = await tick(worker="test")
            assert report["paused"] and report["cycles"] == []
        finally:
            await set_paused(False, "ops@test")

    @pytest.mark.anyio
    async def test_daily_cycle_cap(self, db, monkeypatch):
        from app.memory.records import schedule_work
        from app.models.memory import AgentDecision, WorkItem
        from app.services import autonomy
        _, _, lead = await _prospect(db)
        db.add(AgentDecision(lead_id=lead.id, engine="rules", action="wait", status="waiting"))
        await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="test")
        await db.commit()
        cfg = load_gtm_config("test")
        cfg.autonomy.max_cycles_per_lead_per_day = 1
        monkeypatch.setattr(autonomy, "get_gtm_config", lambda: cfg)
        report = await autonomy.tick(worker="test")
        assert report["capped"] == 1 and report["cycles"] == []
        recheck = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id,
                                                           WorkItem.kind == "recheck"))).scalar_one()
        assert recheck.due_at > datetime.now(timezone.utc) + timedelta(hours=23)

    @pytest.mark.anyio
    async def test_tick_executes_approved_external_action(self, db):
        from app.compliance import approvals
        from app.models.base import Interaction, Task
        from app.services.autonomy import tick
        _, _, lead = await _prospect(db)
        task = Task(lead_id=lead.id, task_type="send_email", status="pending",
                    payload=json.dumps({"subject": "Hi Dana", "body": "Body"}))
        db.add(task)
        await db.flush()
        approval = await approvals.request_approval(db, task, reasoning="r")
        await approvals.approve(db, approval.id, reviewer="manager@test")
        await db.commit()
        report = await tick(worker="test")
        await db.refresh(task)
        sent = (await db.execute(select(Interaction).where(Interaction.lead_id == lead.id))).scalars().all()
        assert report["tasks_executed"] >= 1 and task.status == "completed" and sent[0].subject == "Hi Dana"

    @pytest.mark.anyio
    async def test_worker_once(self, db):
        from app.worker import run
        await run(once=True)


# ── Lead sources ─────────────────────────────────────────────────────────────

CSV = """First Name,Last Name,Work Email,Title,Company,Website,Employees
Maya,Patel,maya@northwind.test,VP Sales,Northwind,northwind.test,"1,200"
Leo,,leo@northwind.test,SDR Manager,Northwind,northwind.test,
"""


class TestLeadSources:
    @pytest.mark.anyio
    async def test_csv_import_with_header_aliases(self, db, app):
        from httpx import ASGITransport, AsyncClient
        from app.models.base import Company, Person
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/leads/import", json={"source": "event-list", "csv": CSV})
            assert r.status_code == 200, r.text
            assert (r.json()["people_created"], r.json()["companies_created"]) == (2, 1)
            bad = await c.post("/api/v1/leads/import", json={"source": "x1", "csv": "foo,bar\n1,2\n"})
            assert bad.status_code == 422
        company = (await db.execute(select(Company).where(Company.domain == "northwind.test"))).scalar_one()
        maya = (await db.execute(select(Person).where(Person.email == "maya@northwind.test"))).scalar_one()
        assert company.employee_count == 1200 and maya.title == "Vp Sales" or maya.title.lower() == "vp sales"

    @pytest.mark.anyio
    async def test_inbound_form_records_consent(self, db, app):
        from httpx import ASGITransport, AsyncClient
        from app.models.memory import ContactConsent
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/webhooks/inbound-lead", json={
                "email": "sam@contoso.test", "first_name": "Sam", "company": "Contoso", "company_domain": "contoso.test",
                "consent_text": "I agree to be contacted about the product", "form_id": "demo-request-1"})
            assert r.status_code == 200 and r.json()["leads_created"] == 1
        bases = (await db.execute(select(ContactConsent.basis, ContactConsent.source))).all()
        assert ("consent", "form:demo-request-1") in bases

    @pytest.mark.anyio
    async def test_apollo_search_then_capped_enrichment(self):
        from app.integrations.apollo import ApolloProvider
        from app.integrations.lead_source import ProviderError
        from app.integrations.lead_source_record import LeadSearchCriteria
        calls = []

        def handler(request):
            calls.append(request)
            assert request.headers["x-api-key"] == "k"
            if request.url.path.endswith("/mixed_people/api_search"):
                return httpx.Response(200, json={"total_entries": 2, "people": [
                    {"id": "p1", "first_name": "Ana", "last_name_obfuscated": "Ru***z", "title": "CRO",
                     "organization": {"name": "Globex"}},
                    {"id": "p2", "first_name": "Bo", "last_name_obfuscated": "Ch***n", "title": "VP Sales",
                     "organization": {"name": "Initech"}}]})
            return httpx.Response(200, json={"person": {
                "id": "p1", "first_name": "Ana", "last_name": "Ruiz", "name": "Ana Ruiz", "title": "CRO",
                "email": "ana@globex.test", "email_status": "verified", "linkedin_url": "https://linkedin.com/in/ana",
                "city": "Austin", "country": "United States",
                "organization": {"name": "Globex", "website_url": "https://www.globex.test"}}})

        provider = ApolloProvider(api_key="k", max_enrich_per_search=1, transport=httpx.MockTransport(handler))
        records = await provider.search_leads(LeadSearchCriteria(job_titles=["CRO", "VP Sales"], limit=2,
                                                                 extra={"seniorities": ["vp", "c_suite"]}))
        search = calls[0]
        assert search.url.params.get_list("person_titles[]") == ["CRO", "VP Sales"]
        assert search.url.params.get_list("person_seniorities[]") == ["vp", "c_suite"]
        assert len([c for c in calls if c.url.path.endswith("/people/match")]) == 1     # capped
        ana, bo = records
        assert (ana.email, ana.last_name, ana.company_domain) == ("ana@globex.test", "Ruiz", "globex.test")
        assert bo.last_name is None and bo.email is None and bo.company_name == "Initech"  # never obfuscated value
        limited = ApolloProvider(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(429)))
        with pytest.raises(ProviderError):
            await limited.search_leads(LeadSearchCriteria())

    @pytest.mark.anyio
    async def test_scout_profiles_are_weak_evidence(self, db, tmp_path):
        from app.integrations.lead_source_record import LeadSearchCriteria
        from app.integrations.scout import ScoutProfileProvider
        from app.models.memory import Fact
        from app.services.lead_ingestion import LeadIngestionService
        pkg = tmp_path / "app" / "scrapers"
        pkg.mkdir(parents=True)
        (tmp_path / "app" / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text(
            "def scrape_github(u):\n"
            "    return {'platform':'github','username':u,'full_name':'Dev Person','email':'dev@guess.test',"
            "'company':'Hooli','website':'https://hooli.test','location':'Berlin','profile_url':'https://github.com/'+u}\n"
            "def scrape_linkedin(u):\n    raise RuntimeError('must never be called')\n")
        provider = ScoutProfileProvider(scout_path=str(tmp_path))
        records = await provider.search_leads(LeadSearchCriteria(extra={"profiles": [
            {"platform": "github", "username": "devp"}, {"platform": "linkedin", "username": "x"}]}))
        assert [r.source for r in records] == ["scout:github"] and records[0].email == "dev@guess.test"
        summary = await LeadIngestionService().ingest_batch(records, db, "scout")
        assert summary.people_created == 1
        fact = (await db.execute(select(Fact).where(Fact.field == "email",
                                                    Fact.value.op("->>")(0).is_(None)))).scalars().all()
        from app.models.base import Person
        person = (await db.execute(select(Person).where(Person.first_name == "Dev"))).scalar_one()
        email_fact = (await db.execute(select(Fact).where(Fact.entity_id == person.id, Fact.field == "email"))).scalar_one()
        assert person.email is None and email_fact.status == "proposed" and email_fact.band == "possible"
