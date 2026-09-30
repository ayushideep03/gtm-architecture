"""Definitions of the 10 deterministic core GTM evaluation test cases."""

from app.evals.models import EvalCase

CORE_EVAL_CASES: list[EvalCase] = [
    EvalCase(
        case_id="EVAL-001",
        name="Missing Enrichment Leads to Enrichment Recommendation",
        description="Verify that an unenriched lead causes Oxygen to decide on enrichment rather than outreach.",
        input_context={"is_company_enriched": False, "is_person_enriched": False},
        expected_behavior={"action": "enrich_company", "decision_reason": "company_enrichment_needed"},
    ),
    EvalCase(
        case_id="EVAL-002",
        name="Fully Enriched Lead Recommends Execution",
        description="Verify that a fully enriched lead with no active tasks causes Oxygen to recommend qualification.",
        input_context={"is_company_enriched": True, "is_person_enriched": True, "tasks": []},
        expected_behavior={"action": "qualify_lead", "decision_reason": "ready_for_execution"},
    ),
    EvalCase(
        case_id="EVAL-003",
        name="Existing Pending Task Causes Oxygen to Wait",
        description="Verify that Oxygen returns 'wait' when an active task for the intended action already exists.",
        input_context={"is_company_enriched": True, "is_person_enriched": True, "pending_task": "qualify_lead"},
        expected_behavior={"action": "wait", "decision_reason": "task_already_exists"},
    ),
    EvalCase(
        case_id="EVAL-004",
        name="Unknown Capability Blocked by Guardrails",
        description="Verify that Guardrails block any decision targeting an unknown or unregistered capability.",
        input_context={"capability": "unregistered_external_telepathy", "action": "mind_read"},
        expected_behavior={"allowed": False, "rule_id": "GR-001"},
    ),
    EvalCase(
        case_id="EVAL-005",
        name="Completed Task Prevents Duplicate Execution",
        description="Verify that executing an already completed task returns idempotent cached output without re-execution.",
        input_context={"task_status": "completed", "cached_result": {"qualified": True, "score": 85}},
        expected_behavior={"already_executed": True, "task_status": "completed"},
    ),
    EvalCase(
        case_id="EVAL-006",
        name="Provider Failure Marked and Event Emitted",
        description="Verify that a failing provider results in task_status='failed' and a task_failed event.",
        input_context={"force_fail": True, "error_message": "Network timeout"},
        expected_behavior={"task_status": "failed", "event_type": "task_failed"},
    ),
    EvalCase(
        case_id="EVAL-007",
        name="Successful Execution Persists Result and Event",
        description="Verify that a successful provider run completes the task and emits task_completed.",
        input_context={"task_type": "qualify_lead", "force_fail": False},
        expected_behavior={"task_status": "completed", "event_type": "task_completed", "success": True},
    ),
    EvalCase(
        case_id="EVAL-008",
        name="Outreach Send Simulation Has No External Side Effects",
        description="Verify that outreach send operates strictly in simulation mode without external SMTP calls.",
        input_context={"recipient_email": "prospect@acme.com", "subject": "Hello"},
        expected_behavior={"mode": "simulation", "sent": True},
    ),
    EvalCase(
        case_id="EVAL-009",
        name="Meeting Booking Simulation Has No External Side Effects",
        description="Verify that meeting booking operates in simulation mode without external calendar integration.",
        input_context={"topic": "Initial Demo"},
        expected_behavior={"mode": "simulation", "booked": True},
    ),
    EvalCase(
        case_id="EVAL-010",
        name="Event Timeline Preserves Chronological Order",
        description="Verify that domain events and touchpoints are ordered chronologically.",
        input_context={"events_count": 3},
        expected_behavior={"ordered": True},
    ),
]


def get_all_eval_cases() -> list[EvalCase]:
    """Return all predefined core eval cases."""
    return CORE_EVAL_CASES


def get_eval_case(case_id: str) -> EvalCase | None:
    """Retrieve an evaluation case by case ID."""
    for c in CORE_EVAL_CASES:
        if c.case_id == case_id:
            return c
    return None
