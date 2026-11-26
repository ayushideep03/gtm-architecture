"""
Demo framework: the 12-stage pipeline run through the real API and workflow engine under the
demo profile (demo providers, scripted AI, no external calls), plus tenant isolation, role
permissions, campaign controls, ICP engine, follow-ups and the demo-mode guard.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.tenancy import DEFAULT_TENANT_ID


@pytest.fixture
def demo_profile(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("runtime: {key_prefix: gtm-test-demo}\n")
    monkeypatch.setenv("GTM_PROFILE", "demo")
    monkeypatch.setenv("GTM_CONFIG_FILE", str(overlay))
    monkeypatch.delenv("OXYGEN_API_KEYS", raising=False)
    from app.providers import reload_providers
    reload_providers()
    yield
    monkeypatch.undo()
    reload_providers()


async def _client_as(app, role: str, org: str = "northstar") -> AsyncClient:
    from app.pipeline.demo_data import demo_uuid
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    r = await c.post("/api/v1/auth/demo-login", json={"user_id": str(demo_uuid("user", org, role))})
    assert r.status_code == 200, r.text
    return c


async def _loaded(app) -> AsyncClient:
    from app.pipeline import demo_control
    await demo_control.load()
    return await _client_as(app, "admin")


@pytest.mark.usefixtures("demo_profile")
class TestDemoPipeline:
    @pytest.mark.anyio
    async def test_all_scenarios_end_to_end_and_metrics(self, app):
        admin = await _loaded(app)
        st = (await admin.get("/api/v1/demo/control/status")).json()
        assert st["dataset_loaded"] and st["role"] == "admin"
        start = (await admin.post("/api/v1/demo/start")).json()
        assert start["accounts"] == 12 and start["contacts"] > 20 and start["provider"] == "demo_apollo"

        results = {}
        for key in ("successful_conversion", "objection", "question", "unsupported_question", "meeting_request",
                    "no_reply", "unsubscribe"):
            r = await admin.post(f"/api/v1/demo/scenarios/{key}/run", json={"auto_approve": True})
            assert r.status_code == 200, r.text
            results[key] = r.json()
        failed = {k: [s for s in v["log"] if not s["ok"]] for k, v in results.items() if not v["completed"]}
        assert not failed, failed

        # stage data is in the database and the dashboard numbers match it
        from app.core.database import AsyncSessionLocal
        from app.models.base import Interaction
        from app.models.pipeline import Draft, FollowUp, Meeting
        m = (await admin.get("/api/v1/metrics/summary")).json()
        k = m["kpis"]
        async with AsyncSessionLocal() as s:
            sent = (await s.execute(select(func.count(Interaction.id)).where(
                Interaction.interaction_type == "email_sent", Interaction.tenant_id == DEFAULT_TENANT_ID))).scalar()
            booked = (await s.execute(select(func.count(Meeting.id)).where(Meeting.status == "booked"))).scalar()
            escalated = (await s.execute(select(func.count(Draft.id)).where(Draft.status == "escalated"))).scalar()
            fu_sent = (await s.execute(select(func.count(FollowUp.id)).where(FollowUp.status == "sent"))).scalar()
            cancelled = (await s.execute(select(func.count(FollowUp.id)).where(FollowUp.status == "cancelled"))).scalar()
        assert k["emails_sent"] == sent >= 9
        assert k["meetings_booked"] == booked == 1 and k["opt_outs"] == 1
        assert k["replies_received"] == 6 and k["positive_replies"] >= 2 and k["negative_replies"] == 1
        assert k["research_signals"] > 0 and k["drafts_generated"] > 0 and k["target_accounts"] == 12
        assert escalated == 1 and fu_sent >= 1 and cancelled >= 1
        assert m["rates"]["reply_rate"] is not None and sum(p["sent"] for p in m["timeseries"]) == sent

        # every external send was approved, and nothing used a live provider
        async with AsyncSessionLocal() as s:
            from app.models.base import Task
            from app.models.memory import Approval
            ext = (await s.execute(select(Task).where(Task.task_type.in_(("send_email", "book_meeting")),
                                                      Task.status == "completed"))).scalars().all()
            for t in ext:
                a = (await s.execute(select(Approval).where(Approval.task_id == t.id))).scalar_one()
                assert a.status == "approved"
            providers = {i.provider for i in (await s.execute(select(Interaction).where(
                Interaction.direction == "outbound"))).scalars().all()}
        assert providers <= {"mock_outreach_send", "demo_google_calendar", "demo_calendly"}

        # workflow view shows the converted prospect through every stage
        lead_id = results["successful_conversion"]["lead_id"]
        wf = (await admin.get(f"/api/v1/workflow/leads/{lead_id}")).json()
        states = {s["name"]: s["state"] for s in wf["stages"]}
        assert states["Meeting"] == "done" and states["Reply"] == "done" and states["Personalised draft"] == "done"
        await admin.aclose()

    @pytest.mark.anyio
    async def test_draft_regenerate_edit_submit_and_reject_returns_for_redraft(self, app):
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        contacts = (await admin.get("/api/v1/contacts", params={"search": "northwind"})).json()["items"]
        lead_id = next(c["lead_id"] for c in contacts if c["lead_status"] != "disqualified")
        research = (await admin.post(f"/api/v1/research/leads/{lead_id}/run")).json()
        assert research["success"] and research["result"]["simulated"] is True
        d1 = (await admin.post("/api/v1/drafts/generate", json={"lead_id": lead_id})).json()
        assert d1["subject"] and d1["explanation"] and d1["evidence"] and d1["strategy"]
        assert any(e.get("simulated") for e in d1["evidence"])            # research evidence is labelled
        d2 = (await admin.post(f"/api/v1/drafts/{d1['id']}/regenerate")).json()
        assert d2["version"] == d1["version"] + 1 and (d2["body"] != d1["body"] or d2["subject"] != d1["subject"])
        edited = (await admin.put(f"/api/v1/drafts/{d2['id']}", json={"subject": "Edited subject",
                                                                     "body": "Hi Priya,\n\nEdited body."})).json()
        assert edited["edited_by"] == "Avery Collins"
        sub = await admin.post(f"/api/v1/drafts/{d2['id']}/submit")
        assert sub.status_code == 200, sub.text
        again = await admin.post(f"/api/v1/drafts/{d2['id']}/submit")
        assert again.status_code == 400                                  # no duplicate submission
        appr = (await admin.get(f"/api/v1/approvals/{sub.json()['approval_id']}")).json()
        assert appr["original_payload"]["subject"] == "Edited subject"
        r = await admin.post(f"/api/v1/approvals/{appr['id']}/reject", json={"reviewer": "x", "note": "tone"})
        assert r.status_code == 200
        drafts = (await admin.get("/api/v1/drafts", params={"lead_id": lead_id, "status": "rejected"})).json()
        assert drafts["total"] == 1
        await admin.aclose()


@pytest.mark.usefixtures("demo_profile")
class TestTenantIsolation:
    @pytest.mark.anyio
    async def test_organisations_cannot_see_each_other(self, app):
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        helix = await _client_as(app, "admin", "helix")
        await helix.post("/api/v1/demo/start")
        ns = (await admin.get("/api/v1/contacts", params={"page_size": 200})).json()["items"]
        hx = (await helix.get("/api/v1/contacts", params={"page_size": 200})).json()["items"]
        assert ns and hx and not {c["lead_id"] for c in ns} & {c["lead_id"] for c in hx}
        assert all(c["company_domain"] not in {x["company_domain"] for x in hx} for c in ns)
        # direct access by id across organisations is not found
        assert (await helix.get(f"/api/v1/memory/leads/{ns[0]['lead_id']}")).status_code == 404
        assert (await helix.get(f"/api/v1/workflow/leads/{ns[0]['lead_id']}")).status_code == 404
        # demo triggers cannot reach another organisation's prospect either
        assert (await helix.post(f"/api/v1/demo/leads/{ns[0]['lead_id']}/reply", json={"kind": "interested"})).status_code == 400
        hc = (await helix.get("/api/v1/campaigns")).json()
        assert [c["name"] for c in hc] == ["Hospital Groups — Patient Flow"]
        # metrics are per organisation
        assert (await helix.get("/api/v1/metrics/summary")).json()["kpis"]["target_accounts"] == 4
        # and the database itself enforces it (row-level security), not just the API
        from app.core.database import AsyncSessionLocal
        from app.core.tenancy import tenant_scope
        from app.models.base import Lead
        from app.pipeline.demo_control import org_id
        with tenant_scope(org_id("helix")):
            async with AsyncSessionLocal() as s:
                tenants = set((await s.execute(select(Lead.tenant_id))).scalars().all())
                assert tenants == {org_id("helix")}
                with pytest.raises(Exception):     # writing another tenant's row is rejected by the policy
                    s.add(Lead(tenant_id=DEFAULT_TENANT_ID, source="x", status="new"))
                    await s.flush()
        await admin.aclose()
        await helix.aclose()


@pytest.mark.usefixtures("demo_profile")
class TestRolePermissions:
    @pytest.mark.anyio
    async def test_roles_enforced_on_the_backend(self, app):
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        from app.pipeline import demo_control
        await demo_control.tick_now(6)
        appr = (await admin.get("/api/v1/approvals")).json()
        assert appr, "expected a pending approval"
        operator = await _client_as(app, "operator")
        approver = await _client_as(app, "approver")
        r = await operator.post(f"/api/v1/approvals/{appr[0]['id']}/approve", json={"reviewer": "boss"})
        assert r.status_code == 403 and "approver" in r.text
        camp = (await admin.get("/api/v1/campaigns")).json()[0]
        assert (await approver.post(f"/api/v1/campaigns/{camp['id']}/status", json={"status": "paused"})).status_code == 403
        assert (await operator.post("/api/v1/demo/reset")).status_code == 403
        assert (await operator.post("/api/v1/integrations/apollo/test")).status_code == 403
        assert (await operator.post("/api/v1/admin/users", json={"name": "x", "email": "x@y.z", "role": "admin"})).status_code == 403
        ok = await approver.post(f"/api/v1/approvals/{appr[0]['id']}/approve", json={"reviewer": "someone else"})
        assert ok.status_code == 200 and ok.json()["decided_by"] == "Morgan Reyes"    # identity, not the body
        assert (await approver.get("/api/v1/metrics/summary")).status_code == 200   # everyone can read
        anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
        assert (await anon.get("/api/v1/campaigns")).status_code == 401
        for c in (admin, operator, approver, anon):
            await c.aclose()

    @pytest.mark.anyio
    async def test_admin_creates_user_with_one_time_key(self, app):
        admin = await _loaded(app)
        r = (await admin.post("/api/v1/admin/users", json={"name": "New Op", "email": "op@x.example",
                                                           "role": "operator"})).json()
        key = r["api_key"]
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"X-API-Key": key})
        me = (await c.get("/api/v1/auth/me")).json()
        assert me["principal"] == "New Op" and me["role"] == "operator"
        users = (await admin.get("/api/v1/admin/organisation")).json()["users"]
        assert "api_key" not in str(users) or all("api_key" not in u for u in users)
        await c.aclose()
        await admin.aclose()


@pytest.mark.usefixtures("demo_profile")
class TestCampaignControls:
    @pytest.mark.anyio
    async def test_create_accounts_csv_icp_recalculate_pause_stop(self, app):
        admin = await _loaded(app)
        c = (await admin.post("/api/v1/campaigns", json={
            "name": "Test campaign", "offer": "Test offer.", "value_points": ["Saves time"],
            "icp": {"industries": ["Software"], "min_employees": 50, "max_employees": 1000, "min_score": 60},
            "sequence": [{"delay_days": 0}, {"delay_days": 2, "angle": "nudge"}]})).json()
        cid = c["id"]
        r = await admin.post(f"/api/v1/campaigns/{cid}/accounts", json={"accounts": [
            {"name": "Quarry Labs", "domain": "quarry-labs.example", "priority": "high"}]})
        assert r.json()["added"] == 1
        csv = "name,domain,industry,employees,country,technologies,priority\n" \
              "Acme Widgets,acme-widgets.example,Manufacturing,300,Germany,SAP,low\n" \
              "Zen Soft,zen-soft.example,Software,120,United Kingdom,HubSpot;Salesforce,medium\n"
        assert (await admin.post(f"/api/v1/campaigns/{cid}/accounts/import", json={"csv": csv})).json()["imported"] == 2
        bad = await admin.post(f"/api/v1/campaigns/{cid}/accounts/import", json={"csv": "name\nx"})
        assert bad.status_code == 400
        accts = (await admin.get(f"/api/v1/campaigns/{cid}/accounts", params={"search": "zen"})).json()
        assert accts["total"] == 1 and accts["items"][0]["technologies"] == ["HubSpot", "Salesforce"]
        started = (await admin.post(f"/api/v1/campaigns/{cid}/start")).json()
        assert started["status"] == "active" and started["accounts"] == 3
        from app.pipeline import demo_control
        await demo_control.tick_now(4)
        q = (await admin.get("/api/v1/qualification", params={"campaign_id": cid})).json()["items"]
        acme = [x for x in q if x["company"] == "Acme Widgets"]
        assert acme and not acme[0]["qualified"] and any("Manufacturing" in r for r in acme[0]["reasons_against"])
        # widen the ICP: preview, save, recalculate
        icp = {"industries": ["Software", "Manufacturing"], "min_employees": 50, "max_employees": 1000, "min_score": 50}
        prev = (await admin.post(f"/api/v1/campaigns/{cid}/qualification/preview", json={"icp": icp})).json()
        assert prev["qualified"] > sum(1 for x in q if x["qualified"])
        await admin.patch(f"/api/v1/campaigns/{cid}", json={"icp": icp})
        rec = (await admin.post(f"/api/v1/campaigns/{cid}/qualification/recalculate")).json()
        assert rec["rescored"] >= 1
        # pause holds the campaign's leads; resume wakes them; stop cancels follow-ups and approvals
        p = await admin.post(f"/api/v1/campaigns/{cid}/status", json={"status": "paused"})
        assert p.json()["status"] == "paused"
        ticks = await demo_control.tick_now(1)
        assert all(cy["lead_id"] not in {x["lead_id"] for x in q} for cy in ticks[0]["cycles"])
        res = (await admin.post(f"/api/v1/campaigns/{cid}/status", json={"status": "active"})).json()
        assert res["effects"]["leads_woken"] >= 1
        stop = (await admin.post(f"/api/v1/campaigns/{cid}/status", json={"status": "stopped"})).json()
        assert stop["status"] == "stopped" and "approvals_rejected" in stop["effects"]
        assert (await admin.post(f"/api/v1/campaigns/{cid}/status", json={"status": "active"})).status_code == 400
        await admin.aclose()

    @pytest.mark.anyio
    async def test_integrations_page_is_honest_in_demo_mode(self, app):
        admin = await _loaded(app)
        data = (await admin.get("/api/v1/integrations")).json()
        by = {i["key"]: i for i in data["integrations"]}
        assert data["demo_mode"] is True
        assert {by[k]["status"] for k in ("apollo", "openai", "anthropic", "smtp", "imap", "google_calendar",
                                          "calendly", "research")} == {"Demo Active"}
        assert by["billionmail"]["status"] == "Live Not Configured"
        t = (await admin.post("/api/v1/integrations/apollo/test")).json()
        assert t["ok"] and t["simulated"] is True and "no external service" in t["message"]
        await admin.aclose()


class TestIcpEngine:
    def test_scores_explains_and_disqualifies(self):
        from types import SimpleNamespace as NS
        from app.pipeline.icp import score_prospect
        icp = {"industries": ["Software"], "min_employees": 50, "max_employees": 1000, "countries": ["Germany"],
               "technologies": ["Salesforce", "HubSpot"], "titles": ["VP Sales"], "min_score": 60}
        good = score_prospect(NS(industry="Software", employee_count=200, country="Germany",
                                 technologies='["Salesforce", "HubSpot"]'), NS(title="VP Sales", department="Sales"), icp)
        assert good.qualified and good.score == 100 and good.fit == "strong" and len(good.dimensions) == 5
        bad = score_prospect(NS(industry="Retail", employee_count=200, country="Germany", technologies=None),
                             NS(title="VP Sales", department="Sales"), icp)
        assert not bad.qualified and bad.reasons_against[0].startswith("Disqualifier")
        near = score_prospect(NS(industry="Software", employee_count=40, country="France", technologies="Salesforce"),
                              NS(title="Engineer", department="Engineering"), icp)
        assert near.fit in ("weak", "poor") and any("near range" in d.detail for d in near.dimensions)


@pytest.mark.usefixtures("demo_profile")
class TestDemoModeGuard:
    @pytest.mark.anyio
    async def test_live_provider_refused_for_external_action_in_demo_mode(self):
        from app.core.database import AsyncSessionLocal
        from app.execution.executor import TaskExecutor
        from app.execution.registry import ExecutionRegistry
        from app.models.base import Lead, Task
        from tests.compliance_helpers import approve, make_contactable
        from tests.test_phase1_platform import FakeProvider, pc
        async with AsyncSessionLocal() as s:
            lead = Lead(source="guard", status="qualified")
            s.add(lead)
            await s.flush()
            await make_contactable(s, lead)
            t = Task(lead_id=lead.id, task_type="send_email", status="pending", payload="{}")
            s.add(t)
            await s.flush()
            await approve(s, t)
            await s.commit()
        live = FakeProvider("real_smtp", caps=("send_email",))
        reg = ExecutionRegistry()
        reg.register(live, config=pc("real_smtp", ("send_email",), resilience={"max_retries": 0, "timeout_s": 2}))
        async with AsyncSessionLocal() as s:
            out = await TaskExecutor(reg).execute(t.id, s)
        assert live.calls == 0 and not out.success and "refused in demo mode" in out.error_message


class TestFollowUpSequence:
    @pytest.mark.anyio
    async def test_scheduled_once_cancelled_on_reply_postponed_on_ooo(self):
        from datetime import datetime, timedelta, timezone
        from app.core.database import AsyncSessionLocal
        from app.models.base import Interaction, Lead
        from app.models.pipeline import FollowUp
        from app.pipeline import followups
        async with AsyncSessionLocal() as s:
            lead = Lead(source="fu-test", status="contacted")
            s.add(lead)
            await s.flush()
            s.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound", channel="email",
                              occurred_at=datetime.now(timezone.utc)))
            await s.flush()
            a = await followups.on_email_sent(s, lead, None)
            b = await followups.on_email_sent(s, lead, None)      # duplicate trigger
            await s.commit()
            assert a.id == b.id and a.step == 2 and a.status == "scheduled"
            count = (await s.execute(select(func.count(FollowUp.id)).where(FollowUp.lead_id == lead.id))).scalar()
            assert count == 1
            msg = await followups.on_reply(s, lead, "out_of_office", datetime.now(timezone.utc), 5)
            await s.commit()
            await s.refresh(a)
            assert "postponed" in msg and a.due_at > datetime.now(timezone.utc) + timedelta(days=4)
            msg = await followups.on_reply(s, lead, "question", datetime.now(timezone.utc))
            await s.commit()
            await s.refresh(a)
            assert a.status == "cancelled" and "cancelled" in msg
            await s.execute(FollowUp.__table__.delete().where(FollowUp.lead_id == lead.id))
            await s.execute(Interaction.__table__.delete().where(Interaction.lead_id == lead.id))
            await s.execute(Lead.__table__.delete().where(Lead.id == lead.id))
            await s.commit()


class TestResponseIntelligenceGate:
    def test_only_covered_questions_are_answered(self):
        from app.pipeline.demo_data import KNOWLEDGE_BASE
        from app.pipeline.responses import match
        kb = KNOWLEDGE_BASE["northstar"]
        hits, uncovered = match("How does it integrate with our CRM? And what does pricing look like?", kb, "question")
        assert {h["id"] for h in hits} == {"kb-crm", "kb-pricing"} and not uncovered
        hits, uncovered = match("Does it integrate with Salesforce? Can you deploy on-prem in Singapore?", kb, "question")
        assert uncovered == ["Can you deploy on-prem in Singapore?"]          # -> escalated, not answered
        hits, _ = match("We already use Outreach for this.", kb, "objection")
        assert [h["id"] for h in hits] == ["obj-competitor"]


@pytest.mark.usefixtures("demo_profile")
class TestReplyWithdrawsQueuedOutreach:
    @pytest.mark.anyio
    async def test_pending_outreach_approval_withdrawn_when_prospect_replies(self, app):
        from app.pipeline import demo_control
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        await demo_control.tick_now(8)
        appr = [a for a in (await admin.get("/api/v1/approvals")).json() if a["capability"] == "send_email"][0]
        res = await demo_control.simulate_reply(uuid.UUID(appr["lead_id"]), "question")
        assert any("withdrawn" in x for x in res["actions"]), res["actions"]
        after = (await admin.get(f"/api/v1/approvals/{appr['id']}")).json()
        assert after["status"] == "rejected" and after["decided_by"] == "system (prospect replied)"
        await admin.aclose()


@pytest.mark.usefixtures("demo_profile")
class TestReadinessFindings:
    @pytest.mark.anyio
    async def test_run_now_makes_follow_up_due_immediately(self, app):
        from app.pipeline import demo_control
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        r = await admin.post("/api/v1/demo/scenarios/no_reply/run", json={"auto_approve": True})
        assert r.json()["completed"], r.json()["log"]
        sched = [f for f in (await admin.get("/api/v1/follow-ups", params={"status": "scheduled"})).json()]
        assert sched, "the no-reply scenario schedules the next step"
        res = (await admin.post(f"/api/v1/follow-ups/{sched[0]['id']}/run-now")).json()
        assert res["made_due"] >= 1 and res["status"] == "due"          # persisted before the due query
        again = await admin.post(f"/api/v1/follow-ups/{sched[0]['id']}/run-now")
        assert again.status_code == 409                                    # no double trigger
        await demo_control.tick_now()
        await admin.aclose()

    @pytest.mark.anyio
    async def test_scripted_intent_assessments_are_labelled_demo_everywhere(self, app):
        from app.core.database import AsyncSessionLocal
        from app.models.memory import ResearchFinding
        from app.pipeline import demo_control
        admin = await _loaded(app)
        await admin.post("/api/v1/demo/start")
        await demo_control.tick_now(8)
        async with AsyncSessionLocal() as s:
            intents = (await s.execute(select(ResearchFinding).where(
                ResearchFinding.kind == "intent_assessment"))).scalars().all()
        assert intents
        for f in intents:                                     # stored provenance, not inferred by the UI
            assert f.body["demo"] is True and f.body["llm"]["vendor"] == "demo"
            assert f.source.endswith("(scripted demo AI)")
        res = (await admin.get("/api/v1/research", params={"kind": "intent_assessment"})).json()["items"]
        assert res and all(i["simulated"] for i in res)
        mem = (await admin.get(f"/api/v1/memory/leads/{intents[0].lead_id}")).json()
        shown = [f for f in mem["findings"] if f["kind"] == "intent_assessment"]
        assert shown and all(f["body"]["demo"] is True for f in shown)   # drawer / approvals read this flag
        await admin.aclose()

    @pytest.mark.anyio
    async def test_recalculate_note_is_consistent(self, app):
        admin = await _loaded(app)
        camp = (await admin.get("/api/v1/campaigns")).json()[0]
        note = (await admin.post(f"/api/v1/campaigns/{camp['id']}/qualification/recalculate")).json()["note"]
        assert note == "Only leads not yet contacted are re-scored; contacted leads keep their stage and score."
        await admin.aclose()
