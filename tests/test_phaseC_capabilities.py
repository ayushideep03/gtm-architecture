"""
Phase C — LLM-backed capabilities: grounded research, intent, qualification,
policy-checked drafting, rep tasks, and the full controller loop end to end.
All LLM calls are scripted; website access uses fake transports.
"""

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.capabilities.llm_providers import (
    LLMDraftingProvider,
    LLMIntentProvider,
    LLMQualificationProvider,
    LLMResearchProvider,
    RepTaskProvider,
)
from app.capabilities.web import Page, WebsiteFetcher, html_to_text, same_site
from app.core.gtm_config import ResearchPolicy
from app.execution.models import ExecutionContext
from app.llm import LLMRouter
from tests.test_phaseA_memory import _prospect, db  # noqa: F401
from tests.test_phaseB_controller import Scripted, proposal

PAGE_TEXT = ("Acme Robotics builds warehouse automation. We are hiring 12 SDRs in Austin to expand outbound. "
             "Our 2027 priority is moving from spreadsheets to an automated sales pipeline.")


class FakeFetcher:
    def __init__(self, pages=None):
        self.pages = pages if pages is not None else [Page(url="https://acme.test/careers", title="Careers",
                                                           text=PAGE_TEXT)]

    async def fetch_site(self, domain, policy):
        return self.pages


def router(*responses):
    return LLMRouter(clients={"openai": Scripted("openai", *responses)})


async def _task(db, lead, capability, payload=None):
    from app.models.base import Task
    t = Task(lead_id=lead.id, task_type=capability, status="in_progress", payload=json.dumps(payload or {}))
    db.add(t)
    await db.flush()
    return t


def ctx(task, payload=None):
    return ExecutionContext(task_id=task.id, task_type=task.task_type, lead_id=task.lead_id,
                            payload=payload or json.loads(task.payload or "{}"))


# ── Web reading ──────────────────────────────────────────────────────────────

class TestWebFetcher:
    def test_html_to_text_and_same_site(self):
        title, text = html_to_text("<html><head><title>Acme</title><script>evil()</script></head>"
                                   "<body><h1>Hi</h1><p>We  build robots.</p></body></html>")
        assert title == "Acme" and text == "Hi We build robots." and "evil" not in text
        assert same_site("https://blog.acme.test/x", "acme.test") and not same_site("https://acme.test.evil.io", "acme.test")

    @pytest.mark.anyio
    async def test_respects_robots_and_rejects_offsite_redirects(self):
        def handler(request):
            path = request.url.path
            if path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /careers")
            if path == "/about":
                return httpx.Response(302, headers={"location": "https://other.example/about"})
            if request.url.host == "other.example":
                return httpx.Response(200, html="<p>offsite</p>")
            return httpx.Response(200, html=f"<title>{path}</title><p>Page {path} content</p>")
        pages = await WebsiteFetcher(transport=httpx.MockTransport(handler)).fetch_site(
            "acme.test", ResearchPolicy(paths=["/", "/about", "/careers", "/blog"]))
        urls = [p.url for p in pages]
        assert "https://acme.test/" in urls and "https://acme.test/blog" in urls
        assert not any("careers" in u or "other.example" in u for u in urls)


# ── Research ─────────────────────────────────────────────────────────────────

class TestResearch:
    @pytest.mark.anyio
    async def test_only_verbatim_grounded_findings_are_kept(self, db):
        from app.models.memory import Fact, ResearchFinding
        company, _, lead = await _prospect(db)
        task = await _task(db, lead, "research_prospect")
        out = {
            "company_summary": "Warehouse automation company expanding outbound.",
            "findings": [
                {"kind": "hiring", "statement": "Hiring 12 SDRs in Austin", "source_url": "https://acme.test/careers",
                 "quote": "We are hiring 12 SDRs in Austin", "confidence": 0.9},
                {"kind": "news", "statement": "Raised a Series C", "source_url": "https://acme.test/careers",
                 "quote": "We just raised our Series C", "confidence": 0.8},          # not on the page
            ],
            "facts": [
                {"entity": "company", "field": "description", "value": "Builds warehouse automation",
                 "source_url": "https://acme.test/careers", "quote": "Acme Robotics builds warehouse automation"},
                {"entity": "company", "field": "employee_count", "value": "5000",       # field not allowed
                 "source_url": "https://acme.test/careers", "quote": "Acme Robotics builds warehouse automation"},
            ],
            "gaps": ["no pricing information"],
        }
        res = await LLMResearchProvider(router=router(out), fetcher=FakeFetcher()).execute(ctx(task), db)
        assert res.success and res.result["findings_recorded"] == 1 and res.result["dropped_ungrounded"] == 2
        kinds = (await db.execute(select(ResearchFinding.kind).where(ResearchFinding.lead_id == lead.id))).scalars().all()
        assert sorted(kinds) == ["brief", "intent_signal"]
        fact = (await db.execute(select(Fact).where(Fact.entity_id == company.id, Fact.field == "description"))).scalar_one()
        assert fact.status == "proposed" and fact.band == "possible"   # website claim alone never auto-applies

    @pytest.mark.anyio
    async def test_unreachable_site_is_reported_not_invented(self, db):
        _, _, lead = await _prospect(db)
        task = await _task(db, lead, "research_prospect")
        scripted = Scripted("openai")
        res = await LLMResearchProvider(router=LLMRouter(clients={"openai": scripted}),
                                        fetcher=FakeFetcher(pages=[])).execute(ctx(task), db)
        assert res.success and res.result["findings_recorded"] == 0 and scripted.calls == []


# ── Intent & qualification ───────────────────────────────────────────────────

class TestIntentAndQualification:
    @pytest.mark.anyio
    async def test_intent_refs_must_exist_repair_then_record(self, db):
        from app.models.memory import ResearchFinding
        _, _, lead = await _prospect(db)
        task = await _task(db, lead, "analyze_intent")
        bad = {"intent_level": "high", "score": 80, "signals": [{"signal": "x", "evidence_ref": "nope", "strength": "strong"}],
               "counter_signals": [], "timing_hypothesis": "this quarter", "rationale": "r"}
        good = {**bad, "signals": [{"signal": "Active lead", "evidence_ref": str(lead.id), "strength": "moderate"}]}
        res = await LLMIntentProvider(router=router(bad, good)).execute(ctx(task), db)
        assert res.success and res.result["llm"]["repaired"] and res.result["intent_level"] == "high"
        f = (await db.execute(select(ResearchFinding).where(ResearchFinding.kind == "intent_assessment",
                                                           ResearchFinding.lead_id == lead.id))).scalar_one()
        assert f.confidence == 0.8

    @pytest.mark.anyio
    @pytest.mark.parametrize("score,model_says,disq,expected", [
        (72, True, [], "qualified"), (40, True, [], "disqualified"), (90, True, ["competitor"], "disqualified"),
    ])
    async def test_qualification_with_deterministic_floor(self, db, score, model_says, disq, expected):
        _, person, lead = await _prospect(db)
        lead.status = "new"
        task = await _task(db, lead, "qualify_lead")
        out = {"fit_score": score, "qualified": model_says, "disqualifiers": disq, "rationale": "fit analysis",
               "persona_fit": "strong",
               "dimensions": [{"name": "persona", "assessment": "VP-level buyer", "score": score,
                               "evidence_ref": str(person.id)},
                              {"name": "timing", "assessment": "unknown", "score": 30, "evidence_ref": None}]}
        res = await LLMQualificationProvider(router=router(out)).execute(ctx(task), db)
        assert res.success and lead.status == expected and lead.score == score
        assert res.result["qualified"] is (expected == "qualified")


# ── Drafting ─────────────────────────────────────────────────────────────────

def draft(lead_id, **kw):
    d = {"channel": "email", "subject": "Your SDR expansion in Austin",
         "body": "Hi Dana, saw Acme is hiring 12 SDRs in Austin. Teams scaling that fast often lose time on manual "
                 "research. Worth a 15-minute look at how others automate it?",
         "personalisation": [{"point": "SDR hiring in Austin", "evidence_ref": str(lead_id)}],
         "claims": [], "call_to_action": "15-minute call"}
    d.update(kw)
    return d


class TestDrafting:
    @pytest.mark.anyio
    async def test_policy_violations_are_repaired_and_footer_added(self, db):
        _, _, lead = await _prospect(db)
        task = await _task(db, lead, "draft_outreach", {"channel": "email"})
        bad = draft(lead.id, body="Just checking in — we guarantee 100% more meetings, {{first_name}}! " * 3,
                    personalisation=[{"point": "x", "evidence_ref": "invented"}])
        scripted = Scripted("openai", bad, draft(lead.id))
        res = await LLMDraftingProvider(router=LLMRouter(clients={"openai": scripted})).execute(ctx(task), db)
        assert res.success and res.result["llm"]["repaired"]
        feedback = scripted.calls[1]["user"]
        for expected in ("banned phrase", "placeholder", "does not exist"):
            assert expected in feedback
        assert res.result["body"].endswith('reply "no thanks" and I won\'t follow up.')

    @pytest.mark.anyio
    async def test_persistent_violation_fails_without_draft(self, db):
        _, _, lead = await _prospect(db)
        task = await _task(db, lead, "draft_outreach", {"channel": "email"})
        bad = draft(lead.id, subject=None)
        res = await LLMDraftingProvider(router=router(bad, bad)).execute(ctx(task), db)
        assert not res.success and res.error_code == "DRAFT_POLICY_VIOLATION" and "subject" in res.error_message

    @pytest.mark.anyio
    async def test_linkedin_message_and_rep_task(self, db):
        from app.models.base import Task
        from app.models.memory import WorkItem
        _, _, lead = await _prospect(db)
        task = await _task(db, lead, "draft_outreach", {"channel": "linkedin"})
        res = await LLMDraftingProvider(router=router(draft(lead.id, channel="linkedin", subject=None,
                                                            body="Hi Dana, congrats on the SDR expansion."))
                                        ).execute(ctx(task), db)
        assert res.success and res.result["subject"] is None and "no thanks" not in res.result["body"]
        drafted = await db.get(Task, task.id)
        drafted.status, drafted.result = "completed", json.dumps(res.result)
        rep_task = await _task(db, lead, "create_rep_task")
        out = await RepTaskProvider().execute(ctx(rep_task, {"channel": "linkedin", "draft_task_id": str(task.id),
                                                             "instructions": "Send the prepared LinkedIn note"}), db)
        item = await db.get(WorkItem, uuid.UUID(out.result["rep_task_id"]))
        assert item.kind == "rep_task_linkedin" and item.payload["prepared"]["body"].startswith("Hi Dana")


# ── Provider availability ────────────────────────────────────────────────────

class TestAvailability:
    def test_llm_providers_load_when_key_present_and_outrank_mocks(self, monkeypatch):
        from app.core.gtm_config import load_gtm_config
        from app.providers.loader import load_providers_from_config
        from app.providers.registry import ProviderRegistry
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        cfg = load_gtm_config("test")
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        assert reg.record("llm_research").available and reg.record("llm_drafting").available
        best = reg.get_provider_for_task_type("research_prospect")
        assert best.provider_name == "llm_research"

    def test_without_key_they_are_unavailable_with_reason(self, monkeypatch):
        from app.core.gtm_config import load_gtm_config
        from app.providers.loader import load_providers_from_config
        from app.providers.registry import ProviderRegistry
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        cfg = load_gtm_config("test")
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        rec = reg.record("llm_intent")
        assert not rec.available and "OPENAI_API_KEY" in rec.unavailable_reason


# ── End to end: controller + capabilities + approval + send ──────────────────

class TestEndToEndLoop:
    @pytest.mark.anyio
    async def test_research_draft_approve_send_then_wait(self, db):
        from app.compliance import approvals
        from app.core.database import AsyncSessionLocal
        from app.execution.executor import TaskExecutor
        from app.execution.registry import ExecutionRegistry
        from app.integrations.gtm.outreach import MockOutreachSendProvider
        from app.memory.context import build_prospect_memory
        from app.oxygen.llm_engine import LLMDecisionEngine
        from app.oxygen.orchestrator import OxygenOrchestrator

        _, _, lead = await _prospect(db)
        await db.commit()
        reg = ExecutionRegistry()
        research_out = {"company_summary": "Warehouse automation; scaling outbound.", "gaps": [], "facts": [],
                        "findings": [{"kind": "hiring", "statement": "Hiring 12 SDRs", "confidence": 0.9,
                                      "source_url": "https://acme.test/careers",
                                      "quote": "We are hiring 12 SDRs in Austin"}]}
        reg.register(LLMResearchProvider(name="llm_research", router=router(research_out), fetcher=FakeFetcher()))
        reg.register(LLMDraftingProvider(name="llm_drafting", router=router(draft(lead.id))))
        reg.register(MockOutreachSendProvider())
        executor = TaskExecutor(reg)

        async def cycle(proposal_dict):
            engine = LLMDecisionEngine(router=router(proposal_dict))
            async with AsyncSessionLocal() as s:
                out = await OxygenOrchestrator(decision_engine=engine).orchestrate(s, lead.id)
                await s.commit()
            if out.task_created and not out.requires_approval:
                async with AsyncSessionLocal() as s:
                    await executor.execute(out.task_id, s)
            return out

        r1 = await cycle(proposal("research_prospect"))
        assert r1.status == "success"
        r2 = await cycle(proposal("draft_outreach", params={"channel": "email", "angle": "SDR hiring"}))
        draft_task_id = str(r2.task_id)
        r3 = await cycle(proposal("send_email", params={"draft_task_id": draft_task_id}, channel="email"))
        assert r3.status == "awaiting_approval"
        async with AsyncSessionLocal() as s:
            await approvals.approve(s, r3.approval_id, reviewer="manager@test")
            await s.commit()
        async with AsyncSessionLocal() as s:
            sent = await executor.execute(r3.task_id, s)
        assert sent.success
        r4 = await cycle(proposal("wait", wait_hours=72, reasoning="Give Dana time to reply"))
        assert r4.status == "waiting"

        async with AsyncSessionLocal() as s:
            memory = await build_prospect_memory(s, lead.id)
        assert any(f["kind"] == "intent_signal" for f in memory["findings"])
        assert [d["status"] for d in memory["decisions"]][:4] == ["waiting", "executed", "executed", "executed"]
        assert memory["interactions"][0]["subject"] == "Your SDR expansion in Austin"
        assert memory["approvals"][0]["decided_by"] == "manager@test"
        assert memory["work_items"][0]["kind"] == "evaluate"
