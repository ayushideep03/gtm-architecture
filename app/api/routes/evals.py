"""API routes for Evals."""

import uuid
from fastapi import APIRouter, HTTPException, status

from app.api.schemas.evals import EvalCaseListResponse, EvalRunResponse
from app.evals.cases import get_all_eval_cases
from app.evals.runner import eval_runner

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get("/cases", response_model=EvalCaseListResponse)
async def list_eval_cases() -> EvalCaseListResponse:
    """List all registered evaluation cases."""
    cases = get_all_eval_cases()
    return EvalCaseListResponse(count=len(cases), cases=cases)


@router.post("/run", response_model=EvalRunResponse)
async def execute_eval_run() -> EvalRunResponse:
    """Execute the full suite of evaluation cases in isolation and return the run report."""
    run = await eval_runner.run_all()
    return EvalRunResponse.model_validate(run)


@router.get("/runs/{run_id}", response_model=EvalRunResponse)
async def get_eval_run(run_id: uuid.UUID) -> EvalRunResponse:
    """Retrieve an evaluation run report by its UUID."""
    run = eval_runner.get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation run {run_id} not found",
        )
    return EvalRunResponse.model_validate(run)
