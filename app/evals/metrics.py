"""Evaluation suite metrics and aggregation."""

from app.evals.models import EvalRun


def summarize_eval_run(run: EvalRun) -> dict[str, float]:
    """Compute aggregate pass rates and average test duration."""
    pass_rate = round(run.passed / run.total, 4) if run.total > 0 else 0.0
    avg_duration = (
        round(sum(r.duration_ms for r in run.results) / run.total, 2)
        if run.total > 0
        else 0.0
    )
    return {
        "pass_rate": pass_rate,
        "avg_duration_ms": avg_duration,
    }
