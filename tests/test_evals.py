"""Tests for the GTM Evaluation Framework (Stage 9)."""

import pytest
import uuid
from httpx import ASGITransport, AsyncClient

from app.evals.cases import CORE_EVAL_CASES, get_all_eval_cases, get_eval_case
from app.evals.metrics import summarize_eval_run
from app.evals.runner import EvalRunner
from app.main import app


class TestEvalCaseDefinitions:
    """Tests evaluating domain cases and specifications."""

    def test_core_cases_count(self) -> None:
        cases = get_all_eval_cases()
        assert len(cases) == 10
        case_ids = {c.case_id for c in cases}
        for i in range(1, 11):
            assert f"EVAL-{i:03d}" in case_ids

    def test_get_individual_case(self) -> None:
        case = get_eval_case("EVAL-001")
        assert case is not None
        assert case.name == "Missing Enrichment Leads to Enrichment Recommendation"
        assert case.expected_behavior["action"] == "enrich_company"

        non_existent = get_eval_case("NON-EXISTENT")
        assert non_existent is None


class TestEvalRunner:
    """Tests evaluating the EvalRunner execution in isolation."""

    @pytest.mark.anyio
    async def test_run_all_cases_deterministic_pass(self) -> None:
        runner = EvalRunner()
        run = await runner.run_all()

        assert run.total == 10
        assert run.passed == 10
        assert run.failed == 0
        assert run.pass_rate == 1.0
        assert len(run.results) == 10

        for res in run.results:
            assert res.passed is True
            assert res.duration_ms >= 0

        stored = runner.get_run(run.run_id)
        assert stored is not None
        assert stored.run_id == run.run_id

    @pytest.mark.anyio
    async def test_summarize_eval_metrics(self) -> None:
        runner = EvalRunner()
        run = await runner.run_all()
        summary = summarize_eval_run(run)

        assert summary["pass_rate"] == 1.0
        assert summary["avg_duration_ms"] >= 0.0


class TestEvalsAPI:
    """Tests evaluating the Evals HTTP endpoints."""

    @pytest.mark.anyio
    async def test_list_eval_cases(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/evals/cases")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 10
            assert len(data["cases"]) == 10

    @pytest.mark.anyio
    async def test_trigger_eval_run_and_retrieve(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Trigger run
            run_resp = await client.post("/api/v1/evals/run")
            assert run_resp.status_code == 200
            run_data = run_resp.json()
            assert run_data["total"] == 10
            assert run_data["passed"] == 10
            assert run_data["failed"] == 0
            assert run_data["pass_rate"] == 1.0
            run_id = run_data["run_id"]

            # Retrieve run by ID
            get_resp = await client.get(f"/api/v1/evals/runs/{run_id}")
            assert get_resp.status_code == 200
            fetched_data = get_resp.json()
            assert fetched_data["run_id"] == run_id
            assert fetched_data["total"] == 10

    @pytest.mark.anyio
    async def test_get_nonexistent_eval_run(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            fake_id = uuid.uuid4()
            resp = await client.get(f"/api/v1/evals/runs/{fake_id}")
            assert resp.status_code == 404
