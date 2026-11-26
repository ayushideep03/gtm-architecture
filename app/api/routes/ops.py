"""
Operations endpoints for serverless hosting (Vercel), protected by ``CRON_SECRET``.

    GET  /api/v1/cron/tick        Vercel Cron: runs the autonomous loop (the worker's job)
    POST /api/v1/cron/bootstrap   migrate + (demo profile) load the dataset, on demand

Vercel Cron calls with ``Authorization: Bearer <CRON_SECRET>``. Without CRON_SECRET set
these endpoints refuse everything (503), so they are never open.
"""

from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, Header, HTTPException, status

router = APIRouter(prefix="/cron", tags=["ops"])
TICK_BUDGET_S = 40.0


def _check(authorization: str | None) -> None:
    secret = os.environ.get("CRON_SECRET") or ""
    if len(secret) < 16:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CRON_SECRET is not configured")
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented.encode(), secret.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid cron secret")


@router.get("/tick", summary="Run the autonomous loop for up to ~40 s (Vercel Cron)")
async def cron_tick(authorization: str | None = Header(None)) -> dict:
    _check(authorization)
    from app.pipeline.bootstrap import ensure_ready
    from app.services.autonomy import tick
    await ensure_ready()
    started, ticks, idle = time.monotonic(), 0, 0
    totals = {"cycles": 0, "tasks_executed": 0, "follow_ups_due": 0}
    while time.monotonic() - started < TICK_BUDGET_S:
        r = await tick(worker="vercel-cron")
        ticks += 1
        if r.get("paused"):
            break
        did = len(r["cycles"]) + r["tasks_executed"] + r.get("follow_ups_due", 0)
        totals["cycles"] += len(r["cycles"])
        totals["tasks_executed"] += r["tasks_executed"]
        totals["follow_ups_due"] += r.get("follow_ups_due", 0)
        idle = idle + 1 if did == 0 else 0
        if idle >= 2:
            break
    return {"ticks": ticks, **totals, "seconds": round(time.monotonic() - started, 1)}


@router.post("/bootstrap", summary="Migrate the database and load the demo dataset")
async def cron_bootstrap(authorization: str | None = Header(None)) -> dict:
    _check(authorization)
    from app.pipeline.bootstrap import ensure_ready
    return await ensure_ready(force=True)
