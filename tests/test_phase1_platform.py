"""
Phase 1 — configuration, capability catalogue, unified provider registry,
provider selection, executor failover and metadata-driven guardrails.
"""

import ast
import asyncio
import json
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.capabilities import get_capability_catalogue
from app.capabilities.catalogue import CapabilityCatalogue
from app.capabilities.definitions import BUILTIN_CAPABILITIES
from app.core.gtm_config import (
    GTMConfig,
    ProviderConfig,
    RateLimitConfig,
    SideEffect,
    load_gtm_config,
)
from app.core.tenancy import DEFAULT_TENANT_ID
from app.events.types import register_event_type, validate_event_type
from app.execution.interfaces import ExecutionProvider
from app.execution.models import ExecutionResult
from app.guardrails.evaluator import GuardrailEvaluator
from app.guardrails.models import GuardrailContext
from app.guardrails.registry import create_default_guardrail_registry
from app.providers.loader import load_providers_from_config
from app.providers.registry import ProviderRegistry
from app.providers.runtime import InMemoryRateLimiter, ProviderHealth, ProviderStat, StaticProviderStats
from app.providers.selection import ProviderSelector, SelectionRequest

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))
    return path


class FakeProvider(ExecutionProvider):
    """Configurable test provider."""

    def __init__(self, name="fake", caps=("research_prospect",), outcome="ok", delay=0.0, **_):
        self._name, self._caps, self.outcome, self.delay, self.calls = name, list(caps), outcome, delay, 0

    @property
    def provider_name(self):
        return self._name

    @property
    def supported_task_types(self):
        return self._caps

    async def execute(self, context, session=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.outcome == "raise":
            raise RuntimeError("vendor 503")
        if self.outcome == "fail":
            return ExecutionResult(success=False, provider=self._name, error_code="BAD_INPUT", error_message="nope")
        return ExecutionResult(success=True, provider=self._name, result={"by": self._name})


def pc(name, caps=("research_prospect",), **kw):
    return ProviderConfig(name=name, factory="tests.test_phase1_platform:FakeProvider", capabilities=list(caps), **kw)


def selector_for(*records, config=None, stats=None):
    reg = ProviderRegistry()
    for instance, cfg in records:
        reg.register(instance, config=cfg)
    cfg = config or GTMConfig(providers=[c for _, c in records])
    return ProviderSelector(reg, config_getter=lambda: cfg, stats=stats), reg


# ── Configuration ─────────────────────────────────────────────────────────────

class TestConfiguration:
    def test_production_profile_has_no_mock_providers(self):
        cfg = load_gtm_config("production")
        assert all("mock" not in p.name and "mock" not in p.tags for p in cfg.providers)

    def test_development_and_test_profiles_enable_mocks_explicitly(self):
        for profile in ("development", "test"):
            names = [p.name for p in load_gtm_config(profile).providers]
            assert "mock_enrichment" in names and "mock_apollo" in names

    def test_overlay_merges_providers_by_name_and_include(self, tmp_path):
        write(tmp_path / "gtm.yaml", """
            providers:
              - {name: a, factory: "x:A", capabilities: [enrich_company], priority: 10}
        """)
        write(tmp_path / "extra/b.yaml", """
            providers:
              - {name: b, factory: "x:B", capabilities: [enrich_company]}
        """)
        write(tmp_path / "gtm.staging.yaml", """
            include: [extra/b.yaml]
            providers:
              - {name: a, priority: 90}
        """)
        cfg = load_gtm_config("staging", config_dir=tmp_path)
        assert [p.name for p in cfg.providers] == ["a", "b"]
        assert cfg.provider("a").priority == 90 and cfg.provider("a").factory == "x:A"

    def test_extra_file_switches_llm_vendor_without_code(self, tmp_path):
        write(tmp_path / "gtm.yaml", """
            llm:
              profiles:
                planner: {provider: openai, model: m1}
        """)
        extra = write(tmp_path / "deploy.yaml", """
            llm:
              profiles:
                planner: {provider: anthropic, model: m2}
        """)
        cfg = load_gtm_config("prod", config_dir=tmp_path, extra_file=extra)
        assert (cfg.llm.profile().provider, cfg.llm.profile().model) == ("anthropic", "m2")

    def test_default_llm_is_openai_and_configurable(self):
        cfg = load_gtm_config("production")
        assert cfg.llm.profile("planner").provider == "openai"
        assert cfg.oxygen.llm_profile in cfg.llm.profiles

    def test_duplicate_provider_names_rejected(self):
        with pytest.raises(ValueError):
            GTMConfig(providers=[pc("dup"), pc("dup")])

    def test_tenant_override_applies_only_to_that_tenant(self):
        t = str(uuid.uuid4())
        cfg = GTMConfig(
            providers=[pc("v1")],
            tenants={t: {"providers": {"v1": {"enabled": False}},
                         "capabilities": {"book_meeting": {"enabled": False}}}},
        )
        assert cfg.provider("v1").enabled is True
        tenant_cfg = cfg.for_tenant(t)
        assert tenant_cfg.provider("v1").enabled is False
        assert tenant_cfg.capabilities["book_meeting"].enabled is False

    def test_human_approval_default_for_external_communication(self):
        cfg = load_gtm_config("production")
        assert SideEffect.EXTERNAL_COMMUNICATION in cfg.approval.required_for_side_effects


# ── Capability catalogue ──────────────────────────────────────────────────────

class TestCapabilityCatalogue:
    def test_aliases_resolve_to_canonical(self):
        cat = get_capability_catalogue()
        assert cat.canonical("company_enrichment") == "enrich_company"
        assert cat.canonical("send_outreach_simulation") == "send_email"
        assert cat.canonical("nope") is None

    def test_parameters_validated_against_schema(self):
        spec = get_capability_catalogue().get("enrich_company")
        assert not spec.validate_parameters({}).ok
        assert spec.validate_parameters({"domain": "x.io"}).ok

    def test_context_fills_lead_id(self):
        lead = uuid.uuid4()
        res = get_capability_catalogue().get("qualify_lead").validate_parameters({}, {"lead_id": lead})
        assert res.ok and res.parameters["lead_id"] == str(lead)

    def test_repeatability_overridable_by_config(self):
        cfg = GTMConfig(capabilities={"send_email": {"repeatability": {"mode": "once_per_lead"}}})
        assert get_capability_catalogue().get("send_email", config=cfg).repeatability.mode == "once_per_lead"

    def test_approval_derives_from_side_effect_and_policy(self):
        cat = get_capability_catalogue()
        cfg = GTMConfig()
        assert cat.requires_approval("send_email", config=cfg) is True
        assert cat.requires_approval("enrich_company", config=cfg) is False
        assert cat.requires_approval("unknown_capability", config=cfg) is True
        relaxed = GTMConfig(capabilities={"send_email": {"requires_approval": False}})
        assert cat.requires_approval("send_email", config=relaxed) is False

    def test_alias_clash_rejected(self):
        cat = CapabilityCatalogue(BUILTIN_CAPABILITIES[:1])
        with pytest.raises(ValueError):
            cat.register(BUILTIN_CAPABILITIES[0])


# ── Provider registry ─────────────────────────────────────────────────────────

class TestProviderRegistry:
    def test_loads_from_config_with_multiple_providers_per_capability(self):
        cfg = GTMConfig(providers=[pc("r1"), pc("r2")])
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        assert [r.name for r in reg.candidates("research_prospect")] == ["r1", "r2"]
        assert reg.candidates("prospect_research")  # alias lookup

    def test_missing_credentials_mark_provider_unavailable(self, monkeypatch):
        monkeypatch.delenv("PHASE1_FAKE_KEY", raising=False)
        cfg = GTMConfig(providers=[pc("needs_key", credentials={"api_key": "PHASE1_FAKE_KEY"})])
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        rec = reg.record("needs_key")
        assert rec.available is False and "PHASE1_FAKE_KEY" in rec.unavailable_reason

    def test_credentials_passed_from_environment(self, monkeypatch):
        monkeypatch.setenv("PHASE1_FAKE_KEY", "secret")
        cfg = GTMConfig(providers=[pc("with_key", credentials={"api_key": "PHASE1_FAKE_KEY"})])
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        assert reg.record("with_key").available is True

    def test_bad_factory_does_not_break_loading(self):
        cfg = GTMConfig(providers=[
            ProviderConfig(name="broken", factory="nowhere.module:Nope", capabilities=["research_prospect"]),
            pc("fine"),
        ])
        reg = ProviderRegistry(loader=lambda r: load_providers_from_config(r, cfg))
        assert reg.record("broken").available is False
        assert reg.record("fine").available is True

    def test_interface_mismatch_marks_unavailable(self):
        class NotAnEnricher:
            provider_name = "x"
        reg = ProviderRegistry()
        rec = reg.register(NotAnEnricher(), capabilities=["enrich_company"])
        assert rec.available is False and "does not implement" in rec.unavailable_reason

    def test_global_registry_contains_only_configured_providers(self):
        from app.providers import get_provider_registry
        names = {r.name for r in get_provider_registry().records()}
        assert names == {p.name for p in load_gtm_config("test").providers}


# ── Provider selection ────────────────────────────────────────────────────────

class TestProviderSelection:
    @pytest.mark.anyio
    async def test_priority_orders_candidates(self):
        sel, _ = selector_for((FakeProvider("low"), pc("low", priority=10)),
                              (FakeProvider("high"), pc("high", priority=90)))
        result = await sel.rank(SelectionRequest("research_prospect"))
        assert [c.name for c in result.candidates] == ["high", "low"]

    @pytest.mark.anyio
    async def test_observed_reliability_outweighs_priority(self):
        stats = StaticProviderStats({"research_prospect": {
            "flaky": ProviderStat(successes=2, failures=40),
            "steady": ProviderStat(successes=40, failures=0),
        }})
        sel, _ = selector_for((FakeProvider("flaky"), pc("flaky", priority=90)),
                              (FakeProvider("steady"), pc("steady", priority=60)), stats=stats)
        assert (await sel.select(SelectionRequest("research_prospect"))).name == "steady"

    @pytest.mark.anyio
    async def test_cost_ceiling_and_constraints_filter(self):
        sel, _ = selector_for(
            (FakeProvider("pricey"), pc("pricey", cost_per_call=1.0)),
            (FakeProvider("eu_only"), pc("eu_only", constraints={"region": ["EU"]})),
            (FakeProvider("cheap"), pc("cheap", cost_per_call=0.01)),
        )
        res = await sel.rank(SelectionRequest("research_prospect", max_cost=0.1, attributes={"region": "US"}))
        assert [c.name for c in res.candidates] == ["cheap"]
        reasons = {r.provider: r.reason for r in res.rejected}
        assert "exceeds ceiling" in reasons["pricey"] and "constraint" in reasons["eu_only"]

    @pytest.mark.anyio
    async def test_rate_limit_and_failure_cooldown(self):
        sel, _ = selector_for((FakeProvider("a"), pc("a", rate_limit=RateLimitConfig(requests=1, per_seconds=60))),
                              (FakeProvider("b"), pc("b")))
        assert await sel.rate_limiter.try_acquire("a", RateLimitConfig(requests=1, per_seconds=60))
        for _ in range(3):
            await sel.health.record_failure("b", "TIMEOUT", threshold=3, cooldown_s=60)
        res = await sel.rank(SelectionRequest("research_prospect"))
        assert res.candidates == []
        assert {r.reason.split(" ")[0] for r in res.rejected} == {"rate", "cooling"}

    @pytest.mark.anyio
    async def test_tenant_policy_disables_provider_and_capability(self):
        t = uuid.uuid4()
        cfg = GTMConfig(providers=[pc("a"), pc("b")],
                        tenants={str(t): {"providers": {"a": {"enabled": False}}}})
        sel, _ = selector_for((FakeProvider("a"), pc("a")), (FakeProvider("b"), pc("b")), config=cfg)
        assert [c.name for c in (await sel.rank(SelectionRequest("research_prospect", tenant_id=t))).candidates] == ["b"]
        assert len((await sel.rank(SelectionRequest("research_prospect"))).candidates) == 2

        cfg2 = GTMConfig(providers=[pc("a")], capabilities={"research_prospect": {"enabled": False}})
        sel2, _ = selector_for((FakeProvider("a"), pc("a")), config=cfg2)
        assert (await sel2.rank(SelectionRequest("research_prospect"))).candidates == []

    @pytest.mark.anyio
    async def test_preferred_provider_and_explain(self):
        sel, _ = selector_for((FakeProvider("a"), pc("a", priority=90)), (FakeProvider("b"), pc("b")))
        res = await sel.rank(SelectionRequest("research_prospect", preferred="b"))
        assert [c.name for c in res.candidates] == ["b"]
        explained = res.explain()
        assert explained["ranked"][0]["provider"] == "b" and "reliability" in explained["ranked"][0]


# ── Executor failover (database) ──────────────────────────────────────────────

async def _db_ok() -> bool:
    from app.core.database import check_db_health
    return (await check_db_health())["status"] == "ok"


class TestExecutorFailover:
    @pytest.fixture(autouse=True)
    async def _fresh_pool(self):
        # Each anyio test runs on its own event loop; pooled asyncpg connections
        # must not leak between loops (same pattern as the existing DB tests).
        from app.core.database import engine
        await engine.dispose()
        yield
        await engine.dispose()

    async def _run(self, providers):
        from app.core.database import AsyncSessionLocal
        from app.execution.executor import TaskExecutor
        from app.execution.registry import ExecutionRegistry
        from app.models.base import Event, Lead, Task
        from sqlalchemy import select

        reg = ExecutionRegistry()
        for inst, cfg in providers:
            reg.register(inst, config=cfg)
        async with AsyncSessionLocal() as s:
            lead = Lead(source="phase1", status="new")
            s.add(lead)
            await s.flush()
            task = Task(lead_id=lead.id, task_type="research_prospect", status="pending", payload="{}")
            s.add(task)
            await s.commit()
            task_id = task.id
        async with AsyncSessionLocal() as s:
            outcome = await TaskExecutor(reg).execute(task_id, s)
        async with AsyncSessionLocal() as s:
            ev = (await s.execute(select(Event).where(
                Event.event_type.in_(["task_completed", "task_failed"]),
                Event.payload.contains(str(task_id))))).scalars().first()
            task_row = await s.get(Task, task_id)
        return outcome, json.loads(ev.payload), task_row

    @pytest.mark.anyio
    async def test_fails_over_on_retryable_error(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        bad, good = FakeProvider("primary", outcome="raise"), FakeProvider("backup")
        outcome, payload, task = await self._run([(bad, pc("primary", priority=90)), (good, pc("backup", priority=10))])
        assert outcome.success and payload["provider"] == "backup"
        # Phase 2: transient errors are retried on the same provider (resilience.max_retries=2), then fail over
        assert [a["provider"] for a in payload["attempts"]] == ["primary", "primary", "primary", "backup"]
        assert payload["attempts"][0]["error_code"] == "PROVIDER_EXCEPTION"
        assert task.tenant_id == DEFAULT_TENANT_ID

    @pytest.mark.anyio
    async def test_no_failover_on_non_retryable_error(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        bad, good = FakeProvider("primary", outcome="fail"), FakeProvider("backup")
        outcome, payload, _ = await self._run([(bad, pc("primary", priority=90)), (good, pc("backup", priority=10))])
        assert not outcome.success and good.calls == 0
        assert payload["error"]["code"] == "BAD_INPUT"

    @pytest.mark.anyio
    async def test_timeout_triggers_failover(self):
        if not await _db_ok():
            pytest.skip("PostgreSQL not available")
        slow, fast = FakeProvider("slow", delay=0.5), FakeProvider("fast")
        outcome, payload, _ = await self._run([
            (slow, pc("slow", priority=90, resilience={"timeout_s": 0.05})),
            (fast, pc("fast", priority=10)),
        ])
        assert outcome.success and payload["attempts"][0]["error_code"] == "TIMEOUT"


# ── Metadata-driven guardrails ────────────────────────────────────────────────

def _ctx(**kw):
    base = dict(lead_id=uuid.uuid4(), target_id=uuid.uuid4())
    base.update(kw)
    return GuardrailContext(**base)


class TestMetadataGuardrails:
    @pytest.mark.anyio
    async def test_requirement_person_email_enforced(self):
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        res = await ev.evaluate(_ctx(action="send_email", task_type="send_email",
                                     entity_facts={"person.email": False}))
        assert not res.allowed and "GR-008" in res.rule_ids and "person.email" in res.reason

    @pytest.mark.anyio
    async def test_cooldown_expired_allows_repeat(self):
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        res = await ev.evaluate(_ctx(action="qualify_lead", task_type="qualify_lead",
                                     task_history=[{"task_type": "qualify_lead", "status": "completed",
                                                    "completed_at": old}]))
        assert res.allowed, res.reason

    @pytest.mark.anyio
    async def test_max_per_lead_enforced(self):
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        hist = [{"task_type": "qualify_lead", "status": "completed", "completed_at": old}] * 3
        res = await ev.evaluate(_ctx(action="qualify_lead", task_type="qualify_lead", task_history=hist))
        assert not res.allowed and "GR-005" in res.rule_ids

    @pytest.mark.anyio
    async def test_alias_history_counts_as_same_capability(self):
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        res = await ev.evaluate(_ctx(action="send_email", task_type="send_email",
                                     task_history=[{"task_type": "send_outreach_simulation", "status": "pending"}]))
        assert not res.allowed and "GR-004" in res.rule_ids

    @pytest.mark.anyio
    async def test_capability_disabled_for_tenant_blocked(self, monkeypatch):
        t = uuid.uuid4()
        cfg = load_gtm_config("test").model_copy(
            update={"tenants": {str(t): {"capabilities": {"research_prospect": {"enabled": False}}}}})
        cfg = GTMConfig.model_validate(cfg.model_dump())
        monkeypatch.setattr("app.capabilities.catalogue.get_gtm_config", lambda: cfg)
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        res = await ev.evaluate(_ctx(action="research_prospect", task_type="research_prospect", tenant_id=t))
        assert not res.allowed and "GR-001" in res.rule_ids and "disabled" in res.reason

    @pytest.mark.anyio
    async def test_mismatched_capability_and_task_type_blocked(self):
        ev = GuardrailEvaluator(create_default_guardrail_registry())
        res = await ev.evaluate(_ctx(action="research_prospect", capability="send_email",
                                     task_type="research_prospect"))
        assert not res.allowed


# ── Events and static checks ──────────────────────────────────────────────────

class TestEventTypesAndStaticChecks:
    def test_event_types_validated_and_extensible(self):
        with pytest.raises(ValueError):
            validate_event_type("definitely_not_registered_xyz")
        register_event_type("phase1_custom_event")
        assert validate_event_type("phase1_custom_event") == "phase1_custom_event"

    def test_core_modules_do_not_name_mock_providers(self):
        """Mock provider names may only appear in mock modules and config, never in core logic."""
        core_dirs = ["oxygen", "guardrails", "providers", "capabilities", "services", "api", "core", "revops", "evals"]
        core_files = [p for d in core_dirs for p in (ROOT / "app" / d).rglob("*.py")]
        core_files.append(ROOT / "app/execution/executor.py")
        offenders = []
        for path in core_files:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and n.body and isinstance(n.body[0], ast.Expr)
                          and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value.startswith("mock_") and id(node) not in docstrings):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value}")
        assert offenders == []
