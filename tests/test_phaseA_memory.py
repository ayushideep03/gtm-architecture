"""
Phase A — persistent sales memory (evidence ledger, findings, decisions, work
queue) and compliance foundations (suppression, lawful basis, frequency caps,
human approval).
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sa_text

from app.core.gtm_config import MemoryConfig
from app.memory.facts import EvidenceInput, FactLedger, score_evidence


def uniq(p="x"):
    return f"{p}-{uuid.uuid4().hex[:8]}"


async def _db_ok():
    from app.core.database import check_db_health
    return (await check_db_health())["status"] == "ok"


@pytest.fixture
async def db():
    from app.core.database import AsyncSessionLocal, engine
    await engine.dispose()
    if not await _db_ok():
        pytest.skip("PostgreSQL not available")
    async with AsyncSessionLocal() as s:
        yield s
    await engine.dispose()


async def _prospect(db, *, email=None, consent=True, domain=None):
    from app.models.base import Company, Lead, Person
    from app.models.memory import ContactConsent
    domain = domain or f"{uniq('co')}.io"
    company = Company(name="Acme", domain=domain)
    db.add(company)
    await db.flush()
    person = Person(first_name="Dana", last_name="Lee", email=email or f"dana@{domain}", company_id=company.id)
    db.add(person)
    await db.flush()
    lead = Lead(person_id=person.id, status="qualified")
    db.add(lead)
    await db.flush()
    if consent:
        db.add(ContactConsent(person_id=person.id, basis="legitimate_interest", source="test"))
        await db.flush()
    return company, person, lead


# ── Scoring ──────────────────────────────────────────────────────────────────

class TestScoring:
    def test_noisy_or_and_bands(self):
        cfg = MemoryConfig()
        assert score_evidence([EvidenceInput("lead_source")], cfg).band == "probable"
        assert score_evidence([EvidenceInput("pattern_guess")], cfg).band == "possible"
        two_weak = score_evidence([EvidenceInput("web_cited_claim"), EvidenceInput("web_cited_claim")], cfg)
        assert round(two_weak.score, 2) == 0.64 and two_weak.band == "probable"
        assert score_evidence([EvidenceInput("email_reply")], cfg).band == "verified"

    def test_verified_requires_primary_source(self):
        cfg = MemoryConfig()
        many_secondary = [EvidenceInput("lead_source")] * 3
        s = score_evidence(many_secondary, cfg)
        assert s.score >= cfg.verified_threshold and s.band == "probable"

    def test_contradiction_caps_score(self):
        cfg = MemoryConfig()
        s = score_evidence([EvidenceInput("email_reply"), EvidenceInput("contradiction")], cfg)
        assert s.score == cfg.contradiction_cap and s.band == "possible"

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            score_evidence([EvidenceInput("made_up")], MemoryConfig())


# ── Fact lifecycle ───────────────────────────────────────────────────────────

class TestFactLedger:
    @pytest.mark.anyio
    async def test_fill_blank_vs_weak_guess(self, db):
        _, person, _ = await _prospect(db)
        ledger = FactLedger()
        o1 = await ledger.record(db, person, "title", "VP Sales", [EvidenceInput("lead_source")], method="t")
        o2 = await ledger.record(db, person, "phone", "+1 555 0100", [EvidenceInput("pattern_guess")], method="t")
        assert o1.status == "applied" and person.title == "VP Sales"
        assert o2.status == "proposed" and person.phone is None

    @pytest.mark.anyio
    async def test_conflict_proposed_then_verified_supersedes(self, db):
        from app.models.memory import Fact
        _, person, _ = await _prospect(db)
        ledger = FactLedger()
        await ledger.record(db, person, "title", "VP Sales", [EvidenceInput("lead_source")])
        weak = await ledger.record(db, person, "title", "CRO", [EvidenceInput("web_cited_claim")])
        assert weak.status == "proposed" and person.title == "VP Sales"
        strong = await ledger.record(db, person, "title", "CRO", [EvidenceInput("email_reply")])
        assert strong.status == "applied" and person.title == "CRO"
        rows = (await db.execute(select(Fact.status).where(Fact.entity_id == person.id, Fact.field == "title"))).scalars().all()
        assert sorted(rows) == ["applied", "proposed", "superseded"]

    @pytest.mark.anyio
    async def test_corroboration_upgrades_band(self, db):
        _, person, _ = await _prospect(db)
        ledger = FactLedger()
        first = await ledger.record(db, person, "location", "Berlin", [EvidenceInput("web_cited_claim")])
        assert first.status == "proposed"  # possible band cannot fill a field
        await ledger.record(db, person, "location", "Berlin", [EvidenceInput("lead_source")])
        again = await ledger.record(db, person, "location", "Berlin", [EvidenceInput("crm_interaction")])
        assert again.status == "applied" and again.fact.band == "verified"

    @pytest.mark.anyio
    async def test_human_value_never_overwritten(self, db):
        _, person, _ = await _prospect(db)
        ledger = FactLedger()
        await ledger.record(db, person, "title", "Head of RevOps", [EvidenceInput("human_entered")],
                            decided_by="rep@test")
        vendor = await ledger.record(db, person, "title", "Ops Manager", [EvidenceInput("vendor_enrichment")])
        assert vendor.status == "proposed" and person.title == "Head of RevOps"

    @pytest.mark.anyio
    async def test_dismissed_value_not_reproposed_and_accept(self, db):
        _, person, _ = await _prospect(db)
        ledger = FactLedger()
        await ledger.record(db, person, "title", "VP Sales", [EvidenceInput("lead_source")])
        p = await ledger.record(db, person, "title", "Intern", [EvidenceInput("web_cited_claim")])
        await ledger.dismiss(db, p.fact.id, "rep@test", "wrong person")
        again = await ledger.record(db, person, "title", "Intern", [EvidenceInput("web_cited_claim")])
        assert again.status == "dismissed" and again.fact is None
        p2 = await ledger.record(db, person, "title", "CRO", [EvidenceInput("web_cited_claim")])
        accepted = await ledger.accept(db, p2.fact.id, "rep@test")
        assert accepted.status == "applied" and person.title == "CRO"

    @pytest.mark.anyio
    async def test_identity_key_collision_is_proposed(self, db):
        _, person_a, _ = await _prospect(db)
        from app.models.base import Person
        person_b = Person(first_name="Other")
        db.add(person_b)
        await db.flush()
        outcome = await FactLedger().record(db, person_b, "email", person_a.email, [EvidenceInput("vendor_enrichment")])
        assert outcome.status == "proposed" and person_b.email is None

    @pytest.mark.anyio
    async def test_enrichment_and_ingestion_write_through_ledger(self, db):
        from app.integrations.lead_source_record import LeadSourceRecord
        from app.models.memory import ContactConsent, Fact, FactEvidence
        from app.services.lead_ingestion import LeadIngestionService
        domain = f"{uniq('led')}.io"
        await LeadIngestionService().ingest_batch(
            [LeadSourceRecord(source="phaseA", external_id=uniq("e"), first_name="Ana", last_name="Ruiz",
                              email=f"ana@{domain}", job_title="CTO", company_name="Ledger Co", company_domain=domain)],
            db, "phaseA")
        kinds = (await db.execute(select(FactEvidence.kind).join(Fact, Fact.id == FactEvidence.fact_id)
                                  .where(Fact.field == "title", func.jsonb_build_array(Fact.value) == func.jsonb_build_array(func.to_jsonb(sa_text("'CTO'::text")))))).scalars().all()
        assert "lead_source" in kinds
        consents = (await db.execute(select(func.count()).select_from(ContactConsent)
                                     .where(ContactConsent.source == "default_policy"))).scalar()
        assert consents >= 1


# ── Compliance ───────────────────────────────────────────────────────────────

class TestComplianceRules:
    @pytest.mark.anyio
    async def test_domain_suppression_blocks_and_lift_restores(self, db):
        from app.compliance.rules import add_suppression, check_contact_allowed, lift_suppression
        company, _, lead = await _prospect(db)
        assert (await check_contact_allowed(db, lead)).allowed
        s = await add_suppression(db, tenant_id=lead.tenant_id, scope="domain", value=f"https://{company.domain}/",
                                  reason="do_not_contact_request", created_by="t")
        status = await check_contact_allowed(db, lead)
        assert status.suppressed and not status.allowed
        await lift_suppression(db, s.id, "t")
        assert (await check_contact_allowed(db, lead)).allowed

    @pytest.mark.anyio
    async def test_lawful_basis_required_and_withdrawal(self, db):
        from app.compliance.rules import check_contact_allowed, record_consent, withdraw_consent
        _, person, lead = await _prospect(db, consent=False)
        assert not (await check_contact_allowed(db, lead)).lawful_basis_ok
        await record_consent(db, tenant_id=lead.tenant_id, person_id=person.id, basis="consent", source="web_form")
        assert (await check_contact_allowed(db, lead)).lawful_basis_ok
        await withdraw_consent(db, tenant_id=lead.tenant_id, person_id=person.id)
        assert not (await check_contact_allowed(db, lead)).lawful_basis_ok

    @pytest.mark.anyio
    async def test_frequency_cap_and_min_gap(self, db):
        from app.compliance.rules import check_contact_allowed
        from app.models.base import Interaction
        _, _, lead = await _prospect(db)
        db.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound",
                           occurred_at=datetime.now(timezone.utc) - timedelta(hours=3)))
        await db.flush()
        status = await check_contact_allowed(db, lead)
        assert not status.frequency_ok and "minimum gap" in status.frequency_reason
        db.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound",
                           occurred_at=datetime.now(timezone.utc) - timedelta(days=4)))
        await db.flush()
        status = await check_contact_allowed(db, lead)
        assert not status.frequency_ok and "limit 2" in status.frequency_reason

    @pytest.mark.anyio
    async def test_guardrails_fail_closed_for_external_actions(self):
        from app.guardrails.evaluator import GuardrailEvaluator
        from app.guardrails.models import GuardrailContext
        from app.guardrails.registry import create_default_guardrail_registry
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        lead = uuid.uuid4()
        base = dict(lead_id=lead, target_id=lead, action="send_email", task_type="send_email",
                    entity_facts={"person.email": True})
        unknown = await ev.evaluate(GuardrailContext(**base))
        assert not unknown.allowed and "GR-011" in unknown.rule_ids
        ok = {"suppressed": False, "lawful_basis_ok": True, "frequency_ok": True}
        for flag, rule in (("suppressed", "GR-011"), ("lawful_basis_ok", "GR-012"), ("frequency_ok", "GR-013")):
            bad = {**ok, flag: not ok[flag]}
            res = await ev.evaluate(GuardrailContext(**base, compliance=bad))
            assert not res.allowed and rule in res.rule_ids
        assert (await ev.evaluate(GuardrailContext(**base, compliance=ok))).allowed


# ── Approval workflow ────────────────────────────────────────────────────────

class TestApprovalWorkflow:
    async def _send_task(self, db, lead, payload=None):
        from app.compliance.approvals import request_approval
        from app.models.base import Task
        task = Task(lead_id=lead.id, task_type="send_email", status="pending",
                    payload=json.dumps(payload or {"subject": "Hello", "body": "Draft body"}))
        db.add(task)
        await db.flush()
        approval = await request_approval(db, task, reasoning="Strong intent signal")
        await db.commit()
        return task, approval

    async def _execute(self, task_id):
        from app.core.database import AsyncSessionLocal
        from app.execution.executor import TaskExecutor
        async with AsyncSessionLocal() as s:
            return await TaskExecutor().execute(task_id, s)

    @pytest.mark.anyio
    async def test_cannot_execute_before_approval(self, db):
        from app.execution.models import InvalidStateTransitionError
        _, _, lead = await _prospect(db)
        task, _ = await self._send_task(db, lead)
        with pytest.raises(InvalidStateTransitionError):
            await self._execute(task.id)

    @pytest.mark.anyio
    async def test_edit_approve_execute_records_touch(self, db):
        from app.compliance import approvals
        from app.models.base import Interaction, Task
        _, _, lead = await _prospect(db)
        task, approval = await self._send_task(db, lead)
        edited = {"subject": "Edited subject", "body": "Edited body"}
        await approvals.approve(db, approval.id, reviewer="rep@test", payload=edited, note="tone")
        await db.commit()
        out = await self._execute(task.id)
        assert out.success, out.error_message
        await db.refresh(approval)
        assert approval.status == "approved" and approval.edited
        touches = (await db.execute(select(Interaction).where(Interaction.lead_id == lead.id))).scalars().all()
        assert len(touches) == 1 and touches[0].subject == "Edited subject" and touches[0].task_id == task.id
        await db.refresh(lead)
        assert lead.status == "contacted"          # stage follows the real send

    @pytest.mark.anyio
    async def test_payload_tampering_after_approval_is_refused(self, db):
        from app.compliance import approvals
        from app.models.base import Task
        _, _, lead = await _prospect(db)
        task, approval = await self._send_task(db, lead)
        await approvals.approve(db, approval.id, reviewer="rep@test")
        task.payload = json.dumps({"subject": "Changed after approval", "body": "x"})
        await db.commit()
        out = await self._execute(task.id)
        assert out.event_type == "compliance_blocked" and "APPROVAL_REQUIRED" in out.error_message

    @pytest.mark.anyio
    async def test_suppression_after_approval_blocks_send(self, db):
        from app.compliance import approvals
        from app.compliance.rules import add_suppression
        from app.models.base import Interaction
        _, person, lead = await _prospect(db)
        task, approval = await self._send_task(db, lead)
        await approvals.approve(db, approval.id, reviewer="rep@test")
        await add_suppression(db, tenant_id=lead.tenant_id, scope="email", value=person.email,
                              reason="unsubscribe", created_by="system")
        await db.commit()
        out = await self._execute(task.id)
        assert out.event_type == "compliance_blocked" and out.task_status == "cancelled"
        n = (await db.execute(select(func.count()).select_from(Interaction).where(Interaction.lead_id == lead.id))).scalar()
        assert n == 0

    @pytest.mark.anyio
    async def test_reject_cancels_and_approve_twice_fails(self, db):
        from app.compliance import approvals
        from app.models.base import Task
        _, _, lead = await _prospect(db)
        task, approval = await self._send_task(db, lead)
        await approvals.reject(db, approval.id, reviewer="rep@test", note="not relevant")
        await db.commit()
        await db.refresh(task)
        assert task.status == "cancelled"
        with pytest.raises(approvals.ApprovalError):
            await approvals.approve(db, approval.id, reviewer="rep@test")

    @pytest.mark.anyio
    async def test_invalid_edit_rejected_and_expiry(self, db):
        from app.compliance import approvals
        _, _, lead = await _prospect(db)
        task, approval = await self._send_task(db, lead)
        with pytest.raises(approvals.ApprovalError):
            await approvals.approve(db, approval.id, reviewer="rep@test", payload={"draft_task_id": "not-a-uuid"})
        approval.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.flush()
        assert await approvals.expire_stale(db) == 1
        await db.refresh(task)
        assert task.status == "cancelled"


async def _completed_draft(db, lead, subject="Idea for Acme's outbound team", body="Hi Dana, ..."):
    from app.models.base import Task
    draft = Task(lead_id=lead.id, task_type="draft_outreach", status="completed",
                 result=json.dumps({"subject": subject, "body": body, "is_draft": True}),
                 completed_at=datetime.now(timezone.utc))
    db.add(draft)
    await db.flush()
    return draft


class TestOrchestratorRouting:
    @pytest.mark.anyio
    async def test_external_proposal_waits_for_approval_and_is_recorded(self, db):
        from app.models.base import Task
        from app.models.memory import AgentDecision, Approval
        from app.oxygen.interfaces import DecisionEngine
        from app.oxygen.models import Decision
        from app.oxygen.orchestrator import OxygenOrchestrator

        _, _, lead = await _prospect(db)
        draft = await _completed_draft(db, lead)
        await db.commit()

        class Proposes(DecisionEngine):
            def decide(self, context):
                return Decision(action="send_email", capability="send_email", reason="High intent; personalised note",
                                target_id=context.lead_id, target_type="lead", confidence=0.8,
                                parameters={"draft_task_id": str(draft.id)})

        outcome = await OxygenOrchestrator(decision_engine=Proposes()).orchestrate(db, lead.id)
        await db.commit()
        assert outcome.status == "awaiting_approval" and outcome.requires_approval
        task = await db.get(Task, outcome.task_id)
        approval = await db.get(Approval, outcome.approval_id)
        record = await db.get(AgentDecision, outcome.decision_record_id)
        assert task.status == "awaiting_approval" and task.idempotency_key
        assert approval.status == "pending" and approval.reasoning.startswith("High intent")
        # the reviewer sees the real draft text, copied from the completed draft task
        assert approval.original_payload["subject"] == "Idea for Acme's outbound team"
        assert record.status == "awaiting_approval" and record.guardrail_result["allowed"] is True

    @pytest.mark.anyio
    async def test_suppressed_prospect_proposal_blocked_and_recorded(self, db):
        from app.compliance.rules import add_suppression
        from app.models.memory import AgentDecision
        from app.oxygen.interfaces import DecisionEngine
        from app.oxygen.models import Decision
        from app.oxygen.orchestrator import OxygenOrchestrator

        company, _, lead = await _prospect(db)
        draft = await _completed_draft(db, lead)

        class Proposes(DecisionEngine):
            def decide(self, context):
                return Decision(action="send_email", capability="send_email", reason="follow up",
                                target_id=context.lead_id, target_type="lead",
                                parameters={"draft_task_id": str(draft.id)})

        await add_suppression(db, tenant_id=lead.tenant_id, scope="company", value=str(company.id),
                              reason="legal", created_by="t")
        await db.commit()
        outcome = await OxygenOrchestrator(decision_engine=Proposes()).orchestrate(db, lead.id)
        await db.commit()
        record = await db.get(AgentDecision, outcome.decision_record_id)
        assert outcome.status == "blocked" and "GR-011" in record.guardrail_result["rule_ids"]


# ── Work queue ───────────────────────────────────────────────────────────────

class TestWorkQueue:
    @pytest.mark.anyio
    async def test_upsert_keeps_one_open_item_with_earliest_due(self, db):
        from app.memory.records import schedule_work
        _, _, lead = await _prospect(db)
        later = datetime.now(timezone.utc) + timedelta(days=5)
        sooner = datetime.now(timezone.utc) + timedelta(days=1)
        a = await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind="recheck",
                                reason="check funding news", due_at=later)
        b = await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind="recheck",
                                reason="prospect asked to reconnect", due_at=sooner)
        assert a.id == b.id and b.due_at == sooner and "reconnect" in b.reason

    @pytest.mark.anyio
    async def test_concurrent_workers_claim_disjoint_items(self, db):
        from app.core.database import AsyncSessionLocal
        from app.memory.records import claim_due, schedule_work
        leads = [(await _prospect(db))[2] for _ in range(4)]
        for lead in leads:
            await schedule_work(db, tenant_id=lead.tenant_id, lead_id=lead.id, kind="evaluate", reason="new info")
        await db.commit()

        async def worker(name):
            async with AsyncSessionLocal() as s:
                items = await claim_due(s, worker=name, limit=10, kinds=["evaluate"])
                ids = [i.id for i in items]
                await asyncio.sleep(0.2)
                await s.commit()
                return ids
        a, b = await asyncio.gather(worker("w1"), worker("w2"))
        assert set(a).isdisjoint(b) and len(a) + len(b) >= 4


# ── API ──────────────────────────────────────────────────────────────────────

class TestMemoryAPI:
    @pytest.mark.anyio
    async def test_memory_approval_and_suppression_endpoints(self, db, app):
        from httpx import ASGITransport, AsyncClient
        from app.compliance.approvals import request_approval
        from app.models.base import Task
        company, person, lead = await _prospect(db)
        task = Task(lead_id=lead.id, task_type="send_email", status="pending", payload=json.dumps({"subject": "S"}))
        db.add(task)
        await db.flush()
        approval = await request_approval(db, task, reasoning="why")
        await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            mem = (await c.get(f"/api/v1/memory/leads/{lead.id}")).json()
            assert mem["person"]["email"] == person.email and mem["approvals"][0]["status"] == "pending"
            assert mem["compliance"]["allowed"] is True
            queue = (await c.get("/api/v1/approvals")).json()
            assert any(a["id"] == str(approval.id) and a["prospect"]["company"] == "Acme" for a in queue)
            r = await c.post(f"/api/v1/approvals/{approval.id}/approve", json={"reviewer": "rep@test"})
            assert r.status_code == 200 and r.json()["status"] == "approved"
            r = await c.post("/api/v1/compliance/suppressions",
                             json={"scope": "domain", "value": company.domain, "reason": "manual", "created_by": "rep"})
            assert r.status_code == 200
            status_now = (await c.get(f"/api/v1/compliance/leads/{lead.id}")).json()
            assert status_now["suppressed"] is True
            fact = await c.post("/api/v1/memory/facts", json={"entity_type": "person", "entity_id": str(person.id),
                                                              "field": "title", "value": "COO", "actor": "rep@test"})
            assert fact.json()["status"] == "applied"
