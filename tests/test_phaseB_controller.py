"""
Phase B — adaptive AI Sales Controller (LLM decision engine).

All LLM calls use scripted vendor clients: no network, no API spend. Vendor
adapters are tested against fake SDK clients to verify the exact request shape.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.llm import LLMError, LLMRouter
from app.llm.base import LLMRefusal
from app.oxygen.llm_engine import LLMDecisionEngine
from app.oxygen.orchestrator import OxygenOrchestrator
from app.oxygen.planner import ActionProposal, proposal_schema
from tests.test_phaseA_memory import _completed_draft, _prospect, db  # noqa: F401  (fixture)


def proposal(action="research_prospect", params=None, confidence=0.8, refs=None, wait_hours=None, **kw):
    base = {
        "situation_summary": "VP Sales at a 200-person SaaS company; enriched; no outreach yet.",
        "missing_information": ["recent initiatives"],
        "interest_signals": ["hiring SDRs"],
        "worth_pursuing": True,
        "action": action,
        "channel": None,
        "parameters_json": json.dumps(params or {}),
        "reasoning": "We know the firmographics but nothing about current priorities; research first.",
        "evidence_refs": refs or [],
        "expected_outcome": "Structured findings to personalise outreach",
        "confidence": confidence,
        "uncertainty": "Public information may be thin",
        "risks": ["Research may not surface anything new"],
        "needs_human_approval": False,
        "wait_hours": wait_hours,
        "alternatives_considered": [{"action": "draft_outreach", "why_not": "Nothing specific to say yet"}],
    }
    base.update(kw)
    return base


class Scripted:
    """Vendor client returning queued responses (dict) or raising queued exceptions."""

    def __init__(self, vendor, *responses):
        self.vendor = vendor
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"input_tokens": 100, "output_tokens": 50}, kwargs["model"]


def engine(*responses, backup=None):
    clients = {"openai": Scripted("openai", *responses)}
    if backup is not None:
        clients["anthropic"] = backup
    return LLMDecisionEngine(router=LLMRouter(clients=clients)), clients["openai"]


async def _context(db, lead):
    from app.oxygen.context import OxygenContextBuilder
    return await OxygenContextBuilder().build(db, lead.id)


# ── Schema & prompt ──────────────────────────────────────────────────────────

class TestPlannerContract:
    def test_schema_is_strict_and_action_enum_is_dynamic(self):
        schema = proposal_schema(["research_prospect", "wait", "stop", "escalate"])
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        assert schema["properties"]["action"]["enum"] == ["research_prospect", "wait", "stop", "escalate"]

    def test_proposal_parameters_must_be_object(self):
        p = ActionProposal.model_validate(proposal(parameters_json="[1,2]"))
        with pytest.raises(ValueError):
            p.parameters()

    @pytest.mark.anyio
    async def test_prompt_contains_memory_and_menu_respects_data(self, db):
        _, person, lead = await _prospect(db)
        person.email = None   # no email -> no external contact capabilities
        await db.flush()
        eng, client = engine(proposal())
        await eng.decide(await _context(db, lead))
        call = client.calls[0]
        doc = json.loads(call["user"])
        enum = call["schema"]["properties"]["action"]["enum"]
        assert "research_prospect" in enum and "send_email" not in enum and "find_leads" not in enum
        unavailable = {c["name"]: c.get("reason") for c in doc["capabilities"] if c.get("available") is False}
        assert "missing data" in unavailable["send_email"]
        assert doc["prospect"]["person"]["first_name"] == "Dana" and doc["campaign"]["goal"]
        assert "compliance" in doc and "decision_history" in doc


# ── Decisions ────────────────────────────────────────────────────────────────

class TestControllerDecisions:
    @pytest.mark.anyio
    async def test_valid_proposal_becomes_rich_decision_and_task(self, db):
        from app.models.memory import AgentDecision
        company, _, lead = await _prospect(db)
        await db.commit()
        ctx = await _context(db, lead)
        real_fact = ctx.memory["facts"]["company"][0]["id"] if ctx.memory["facts"]["company"] else str(lead.id)
        eng, _ = engine(proposal(refs=[str(lead.id), "made-up-id"]))
        outcome = await OxygenOrchestrator(decision_engine=eng).orchestrate(db, lead.id)
        await db.commit()
        d = outcome.decision
        assert outcome.task_created and outcome.status == "success" and not outcome.requires_approval
        assert d.engine == "llm" and d.model == "openai/gpt-4.1" and d.prompt_version.startswith("planner-v1-")
        assert d.evidence_refs == [str(lead.id)] and d.llm_meta["dropped_evidence_refs"] == ["made-up-id"]
        record = await db.get(AgentDecision, outcome.decision_record_id)
        assert record.engine == "llm" and record.confidence == 0.8 and record.risks
        assert record.context_snapshot["alternatives"][0]["action"] == "draft_outreach"
        assert real_fact  # memory was available to cite

    @pytest.mark.anyio
    async def test_invalid_proposal_is_repaired_once(self, db):
        _, _, lead = await _prospect(db)
        bad = proposal(action="crm_update", params={})           # missing required crm_action
        good = proposal(action="crm_update", params={"crm_action": "append_interaction"})
        eng, client = engine(bad, good)
        decision = await eng.decide(await _context(db, lead))
        assert decision.engine == "llm" and decision.llm_meta["repaired"] is True
        assert "VALIDATION" in client.calls[1]["user"] and "crm_action" in client.calls[1]["user"]

    @pytest.mark.anyio
    async def test_still_invalid_after_repair_falls_back_to_rules(self, db):
        _, _, lead = await _prospect(db)
        eng, _ = engine(proposal(action="crm_update", params={}), proposal(action="crm_update", params={}))
        decision = await eng.decide(await _context(db, lead))
        assert decision.engine == "rules_fallback" and "invalid after repair" in decision.fallback_reason

    @pytest.mark.anyio
    async def test_no_llm_available_uses_rules_and_says_so(self, db, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        _, _, lead = await _prospect(db)
        decision = await LLMDecisionEngine(router=LLMRouter()).decide(await _context(db, lead))
        assert decision.engine == "rules_fallback" and "OPENAI_API_KEY" in decision.fallback_reason
        assert decision.reasoning.startswith("[Rules fallback")

    @pytest.mark.anyio
    async def test_vendor_failover_to_backup_profile(self, db):
        _, _, lead = await _prospect(db)
        backup = Scripted("anthropic", proposal())
        eng, _ = engine(LLMError("503 from vendor"), backup=backup)
        decision = await eng.decide(await _context(db, lead))
        assert decision.engine == "llm" and decision.model == "anthropic/claude-opus-5-5"
        assert decision.llm_meta["fallback_attempts"][0]["profile"] == "planner"

    @pytest.mark.anyio
    async def test_refusal_everywhere_falls_back(self, db):
        _, _, lead = await _prospect(db)
        eng, _ = engine(LLMRefusal("declined"), backup=Scripted("anthropic", LLMRefusal("declined")))
        decision = await eng.decide(await _context(db, lead))
        assert decision.engine == "rules_fallback"

    @pytest.mark.anyio
    async def test_low_confidence_is_escalated_to_human(self, db):
        from app.models.memory import WorkItem
        _, _, lead = await _prospect(db)
        await db.commit()
        eng, _ = engine(proposal(confidence=0.1))
        outcome = await OxygenOrchestrator(decision_engine=eng).orchestrate(db, lead.id)
        await db.commit()
        assert outcome.status == "escalated" and not outcome.task_created
        item = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id))).scalar_one()
        assert item.kind == "human_review" and "confidence 0.10" in item.reason

    @pytest.mark.anyio
    async def test_wait_schedules_reevaluation_and_stop_closes_work(self, db):
        from app.models.memory import WorkItem
        _, _, lead = await _prospect(db)
        await db.commit()
        eng, _ = engine(proposal(action="wait", wait_hours=48, reasoning="Prospect is on holiday until Monday"))
        out = await OxygenOrchestrator(decision_engine=eng).orchestrate(db, lead.id)
        await db.commit()
        item = (await db.execute(select(WorkItem).where(WorkItem.lead_id == lead.id))).scalar_one()
        assert out.status == "waiting" and item.kind == "evaluate" and "holiday" in item.reason
        assert timedelta(hours=47) < item.due_at - datetime.now(timezone.utc) < timedelta(hours=49)
        eng2, _ = engine(proposal(action="stop", reasoning="Prospect asked us not to follow up"))
        out2 = await OxygenOrchestrator(decision_engine=eng2).orchestrate(db, lead.id)
        await db.commit()
        await db.refresh(item)
        assert out2.status == "stopped" and item.finished_at is not None and item.outcome == "stopped_by_oxygen"

    @pytest.mark.anyio
    async def test_send_email_proposal_carries_draft_and_waits_for_approval(self, db):
        from app.models.memory import Approval
        _, _, lead = await _prospect(db)
        draft = await _completed_draft(db, lead, subject="Your SDR hiring push", body="Hi Dana, saw the SDR roles...")
        await db.commit()
        eng, _ = engine(proposal(action="send_email", channel="email", params={"draft_task_id": str(draft.id)},
                                 refs=[str(draft.id)], needs_human_approval=True))
        out = await OxygenOrchestrator(decision_engine=eng).orchestrate(db, lead.id)
        await db.commit()
        approval = await db.get(Approval, out.approval_id)
        assert out.status == "awaiting_approval"
        assert approval.original_payload["subject"] == "Your SDR hiring push"
        assert approval.reasoning.startswith("We know the firmographics")


# ── Vendor adapters (request shape, refusals) ────────────────────────────────

class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return {k: v for k, v in self.__dict__.items()}


class TestVendorAdapters:
    @pytest.mark.anyio
    async def test_anthropic_request_shape_and_refusal(self, monkeypatch):
        import app.llm.anthropic_client as mod
        captured = {}

        class FakeMessages:
            def __init__(self, stop_reason):
                self.stop_reason = stop_reason

            async def create(self, **kw):
                captured.update(kw)
                return _NS(stop_reason=self.stop_reason, stop_details=_NS(category="cyber"),
                           content=[_NS(type="text", text='{"ok": true}')], usage=_NS(input_tokens=1),
                           model=kw["model"])

        def factory(stop_reason):
            class FakeClient:
                def __init__(self, **kw):
                    self.beta = _NS(messages=FakeMessages(stop_reason))
                    self.messages = FakeMessages(stop_reason)

                async def close(self):
                    pass
            return FakeClient

        monkeypatch.setattr(mod, "AsyncAnthropic", factory("end_turn"))
        client = mod.AnthropicJSONClient(api_key="k")
        args = dict(model="claude-opus-5-5", system="s", user="u", schema={"type": "object"}, schema_name="x",
                    max_output_tokens=100, timeout_s=5, temperature=0.2, effort="medium",
                    refusal_fallbacks="default", max_retries=0)
        data, _, model = await client.complete_json(**args)
        assert data == {"ok": True} and model == "claude-opus-5-5"
        assert captured["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}},
                                             "effort": "medium"}
        assert captured["betas"] == [mod.FALLBACK_BETA] and captured["fallbacks"] == "default"
        assert "temperature" not in captured
        monkeypatch.setattr(mod, "AsyncAnthropic", factory("refusal"))
        with pytest.raises(LLMRefusal):
            await mod.AnthropicJSONClient(api_key="k").complete_json(**args)

    @pytest.mark.anyio
    async def test_openai_request_shape_and_refusal(self, monkeypatch):
        import app.llm.openai_client as mod
        captured = {}

        def factory(refusal):
            class FakeCompletions:
                async def create(self, **kw):
                    captured.update(kw)
                    msg = _NS(content='{"ok": 1}', refusal=refusal)
                    return _NS(choices=[_NS(message=msg, finish_reason="stop")], usage=None, model=kw["model"])

            class FakeClient:
                def __init__(self, **kw):
                    self.chat = _NS(completions=FakeCompletions())

                async def close(self):
                    pass
            return FakeClient

        monkeypatch.setattr(mod, "AsyncOpenAI", factory(None))
        args = dict(model="gpt-4.1", system="s", user="u", schema={"type": "object"}, schema_name="action_proposal",
                    max_output_tokens=100, timeout_s=5, temperature=0.2, effort=None, refusal_fallbacks=None,
                    max_retries=0)
        data, _, _ = await mod.OpenAIJSONClient(api_key="k").complete_json(**args)
        assert data == {"ok": 1}
        rf = captured["response_format"]
        assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
        assert captured["messages"][0]["role"] == "system"
        monkeypatch.setattr(mod, "AsyncOpenAI", factory("I can't help with that"))
        with pytest.raises(LLMRefusal):
            await mod.OpenAIJSONClient(api_key="k").complete_json(**args)

    def test_router_status_reflects_keys(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        st = LLMRouter().status("planner")
        rows = {r["profile"]: r for r in st["profiles"]}
        assert rows["planner"]["available"] is False and rows["planner_backup"]["available"] is True
        assert st["available"] is True


# ── API ──────────────────────────────────────────────────────────────────────

class TestControllerAPI:
    @pytest.mark.anyio
    async def test_engine_status_history_and_cycle(self, db, app):
        from httpx import ASGITransport, AsyncClient
        _, _, lead = await _prospect(db)
        await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            eng = (await c.get("/api/v1/oxygen/engine")).json()
            assert eng["decision_engine"] == "rules" and eng["campaign"]["goal"]
            cycle = (await c.post(f"/api/v1/oxygen/leads/{lead.id}/cycle")).json()
            assert cycle["decision"]["action"] == "enrich_company"
            assert cycle["guardrails"]["allowed"] is True
            assert cycle["execution"]["task_status"] in ("completed", "pending", "failed")
            history = (await c.get(f"/api/v1/oxygen/leads/{lead.id}/decisions")).json()
            assert history[0]["action"] == "enrich_company" and history[0]["status"] in ("executed", "failed",
                                                                                          "retry_scheduled")
