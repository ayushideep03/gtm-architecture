"""Phase F — outcome attribution, RevOps dashboard, learning (insights + provider outcome quality)."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.gtm_config import load_gtm_config
from app.revops.outcomes import attribute_outcomes, employee_band
from tests.test_phaseA_memory import _prospect, db  # noqa: F401
from tests.test_phaseB_controller import Scripted, proposal

NOW = datetime.now(timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


async def touch(db, lead, when, channel="email", task_id=None, provider="mock_outreach_send"):
    from app.models.base import Interaction
    t = Interaction(lead_id=lead.id, interaction_type="email_sent" if channel == "email" else "linkedin_message",
                    direction="outbound", channel=channel, provider=provider, occurred_at=when, task_id=task_id,
                    channel_message_id=f"m-{uuid.uuid4().hex[:6]}")
    db.add(t)
    await db.flush()
    return t


async def event(db, lead, event_type, when, **payload):
    from app.models.base import Event
    db.add(Event(lead_id=lead.id, event_type=event_type, source="test", occurred_at=when,
                 payload=json.dumps(payload)))
    await db.flush()


async def rows_for(db, lead):
    from app.models.memory import OutcomeAttribution
    return {r.touch_id: r for r in (await db.execute(select(OutcomeAttribution).where(
        OutcomeAttribution.lead_id == lead.id))).scalars().all()}


class TestAttribution:
    def test_employee_bands(self):
        assert employee_band(30, [50, 200]) == "1-50" and employee_band(150, [50, 200]) == "51-200"
        assert employee_band(999, [50, 200]) == "201+" and employee_band(None, [50]) == "unknown"

    @pytest.mark.anyio
    async def test_latest_touch_strongest_outcome_and_window(self, db):
        _, _, lead = await _prospect(db)
        old = await touch(db, lead, ago(days=30))
        t1 = await touch(db, lead, ago(days=10))
        t2 = await touch(db, lead, ago(days=2))
        await event(db, lead, "reply_classified", ago(days=1), category="question")
        await event(db, lead, "meeting_booked", ago(hours=5))
        await attribute_outcomes(db)
        rows = await rows_for(db, lead)
        assert rows[t2.id].outcome == "meeting" and rows[t1.id].outcome == "none"
        assert rows[old.id].outcome == "none" and rows[old.id].final is True and rows[t2.id].final is False

    @pytest.mark.anyio
    async def test_negative_outcomes_and_ignored_ooo(self, db):
        _, _, a = await _prospect(db)
        _, _, b = await _prospect(db)
        _, _, c = await _prospect(db)
        ta, tb, tc = await touch(db, a, ago(days=3)), await touch(db, b, ago(days=3)), await touch(db, c, ago(days=3))
        await event(db, a, "email_bounced", ago(days=2), bounce_type="hard")
        await event(db, b, "reply_classified", ago(days=2), category="unsubscribe")
        await event(db, c, "reply_classified", ago(days=2), category="out_of_office")
        await attribute_outcomes(db)
        assert (await rows_for(db, a))[ta.id].outcome == "bounced"
        assert (await rows_for(db, b))[tb.id].outcome == "unsubscribed"
        assert (await rows_for(db, c))[tc.id].outcome == "none"

    @pytest.mark.anyio
    async def test_touch_context_and_idempotence(self, db):
        from app.models.base import Event, Task
        from app.models.memory import AgentDecision, OutcomeAttribution, ResearchFinding
        company, _, lead = await _prospect(db)
        company.industry, company.employee_count = "Software", 300
        db.add(ResearchFinding(lead_id=lead.id, entity_type="company", entity_id=company.id, kind="brief",
                               summary="b", created_at=ago(days=5)))
        draft = Task(lead_id=lead.id, task_type="draft_outreach", status="completed",
                     payload=json.dumps({"channel": "email", "angle": "SDR hiring"}),
                     result=json.dumps({"personalisation": [{"point": "x"}, {"point": "y"}]}))
        db.add(draft)
        await db.flush()
        db.add(Event(lead_id=lead.id, event_type="task_completed", source="llm_drafting",
                     payload=json.dumps({"task_id": str(draft.id), "provider": "llm_drafting"})))
        send = Task(lead_id=lead.id, task_type="send_email", status="completed",
                    payload=json.dumps({"draft_task_id": str(draft.id)}))
        db.add(send)
        await db.flush()
        db.add(AgentDecision(lead_id=lead.id, engine="llm", action="send_email", status="executed", task_id=send.id))
        t = await touch(db, lead, ago(days=3), task_id=send.id)
        await event(db, lead, "reply_classified", ago(days=2), category="positive_interest")
        assert await attribute_outcomes(db) >= 1
        await attribute_outcomes(db)
        row = (await rows_for(db, lead))[t.id]
        assert (row.angle, row.personalisation_points, row.draft_provider, row.engine) == ("SDR hiring", 2,
                                                                                           "llm_drafting", "llm")
        assert row.research_before_touch and row.outcome == "positive_reply"
        assert row.segment == {"industry": "Software", "size_band": "201-1000", "seniority": "unknown"}
        n = (await db.execute(select(func.count()).select_from(OutcomeAttribution).where(
            OutcomeAttribution.lead_id == lead.id))).scalar()
        assert n == 1


async def _scenario(db, n_email=6, n_linkedin=6):
    """Email converts well, LinkedIn does not."""
    leads = []
    for i in range(n_email):
        company, _, lead = await _prospect(db)
        company.industry = "Software"
        await touch(db, lead, ago(days=3), channel="email")
        if i < 4:
            await event(db, lead, "reply_classified", ago(days=2), category="positive_interest")
        leads.append(lead)
    for _ in range(n_linkedin):
        company, _, lead = await _prospect(db)
        company.industry = "Software"
        await touch(db, lead, ago(days=3), channel="linkedin")
        leads.append(lead)
    await attribute_outcomes(db)
    return leads


class TestDashboard:
    @pytest.mark.anyio
    async def test_dashboard_sections_and_recommendations(self, db, monkeypatch):
        from app.revops import dashboard as dash
        await _scenario(db)
        cfg = load_gtm_config("test")
        cfg.revops.min_sample_for_recommendation = 5
        monkeypatch.setattr(dash, "get_gtm_config", lambda: cfg)
        d = await dash.revops_dashboard(db, days=30)
        email, linkedin = d["outreach"]["by_channel"]["email"], d["outreach"]["by_channel"]["linkedin"]
        assert (email["n"], email["positive_rate"], email["weak"]) == (6, round(4 / 6, 4), False)
        assert linkedin["positive_rate"] == 0.0
        funnel = {s["stage"]: s["count"] for s in d["pipeline"]["funnel"]}
        assert funnel["contacted"] == 12 and funnel["positive"] == 4
        assert any(r["area"] == "channels" and "linkedin" in r["suggestion"] for r in d["recommendations"])
        for section in ("segments", "qualification", "providers", "costs", "approvals", "decisions"):
            assert section in d

    @pytest.mark.anyio
    async def test_api_endpoints(self, db, app):
        from httpx import ASGITransport, AsyncClient
        _, _, lead = await _prospect(db)
        await touch(db, lead, ago(days=1))
        await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/v1/revops/attribute")).json()["attributions_written"] >= 1
            assert (await c.get("/api/v1/revops/outcomes")).json()[0]["lead_id"] == str(lead.id)
            d = (await c.get("/api/v1/revops/dashboard?days=7")).json()
            assert d["outreach"]["overall"]["n"] == 1 and d["outreach"]["overall"]["weak"] is True
            ins = (await c.get(f"/api/v1/revops/insights/leads/{lead.id}")).json()
            assert "segment" in ins


class TestLearning:
    @pytest.mark.anyio
    async def test_planner_insights_use_peers_not_self(self, db):
        from app.revops.insights import planner_insights
        await _scenario(db)
        company, _, me = await _prospect(db)
        company.industry = "Software"
        await db.flush()
        ins = await planner_insights(db, me)
        assert ins["basis"] == "same industry" and ins["peer_outcomes"]["n"] == 12
        assert ins["by_channel"]["email"]["positive_rate"] > ins["by_channel"]["linkedin"]["positive_rate"]
        assert len(ins["converted_journeys"]) == 3 and ins["converted_journeys"][0]["channel"] == "email"

    @pytest.mark.anyio
    async def test_planner_sees_learned_insights(self, db):
        from app.llm import LLMRouter
        from app.oxygen.context import OxygenContextBuilder
        from app.oxygen.llm_engine import LLMDecisionEngine
        await _scenario(db, n_email=2, n_linkedin=0)
        _, _, me = await _prospect(db)
        scripted = Scripted("openai", proposal())
        ctx = await OxygenContextBuilder().build(db, me.id)
        await LLMDecisionEngine(router=LLMRouter(clients={"openai": scripted})).decide(ctx)
        doc = json.loads(scripted.calls[0]["user"])
        assert doc["learned_insights"]["baseline"]["n"] == 2

    @pytest.mark.anyio
    async def test_outcome_quality_steers_provider_selection(self):
        from app.core.gtm_config import GTMConfig
        from app.providers.selection import ProviderSelector, SelectionRequest
        from tests.test_phase1_platform import FakeProvider, pc
        from app.providers.registry import ProviderRegistry

        class Outcomes:
            async def get(self, session, capability):
                return {"writer_a": (1, 20), "writer_b": (9, 20)}

        reg = ProviderRegistry()
        for name in ("writer_a", "writer_b"):
            reg.register(FakeProvider(name, caps=("draft_outreach",)), config=pc(name, ("draft_outreach",)))
        cfg = GTMConfig()
        learned = ProviderSelector(reg, config_getter=lambda: cfg, outcomes=Outcomes())
        res = await learned.rank(SelectionRequest("draft_outreach"))
        assert res.best.name == "writer_b" and res.best.breakdown["outcome_observations"] == 20
        plain = await ProviderSelector(reg, config_getter=lambda: cfg).rank(SelectionRequest("draft_outreach"))
        assert plain.best.name == "writer_a"     # without outcome data: deterministic tie-break

    @pytest.mark.anyio
    async def test_worker_tick_attributes(self, db):
        from app.services.autonomy import tick
        _, _, lead = await _prospect(db)
        await touch(db, lead, ago(days=1))
        await db.commit()
        report = await tick(worker="test")
        assert report["attributions"] >= 1
