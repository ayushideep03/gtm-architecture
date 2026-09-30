"""Evaluation Framework package."""

from app.evals.cases import CORE_EVAL_CASES, get_all_eval_cases, get_eval_case
from app.evals.metrics import summarize_eval_run
from app.evals.models import EvalCase, EvalResult, EvalRun
from app.evals.runner import EvalRunner, eval_runner

__all__ = [
    "EvalCase",
    "EvalResult",
    "EvalRun",
    "CORE_EVAL_CASES",
    "get_all_eval_cases",
    "get_eval_case",
    "EvalRunner",
    "eval_runner",
    "summarize_eval_run",
]
