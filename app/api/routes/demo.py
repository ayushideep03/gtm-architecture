"""
Demo endpoints (development / demo / test profiles only — 404 in production).

    GET  /api/v1/demo/scenarios                 the four scenarios and suggested replies
    POST /api/v1/demo/seed                      create the labelled SAMPLE prospects (idempotent)
    POST /api/v1/demo/leads/{id}/simulate-reply feed a prospect reply / calendar acceptance through
                                                the real inbound pipeline
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services import demo

router = APIRouter(prefix="/demo", tags=["demo"])


def _guard() -> None:
    if not demo.demo_allowed():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demo endpoints are disabled in this profile")


@router.get("/scenarios")
async def scenarios() -> dict:
    _guard()
    from app.core.gtm_config import get_gtm_config
    cfg = get_gtm_config()
    scripted = any(p.provider == "demo" for p in cfg.llm.profiles.values())
    return {"profile": cfg.profile, "scripted_ai": scripted, "scenarios": demo.scenario_catalogue(),
            "suggested_replies": demo.SUGGESTED_REPLIES}


@router.post("/seed")
async def seed(db: AsyncSession = Depends(get_db)) -> dict:
    _guard()
    rows = await demo.seed_scenarios(db)
    await db.commit()
    return {"seeded": rows}


class ReplyBody(BaseModel):
    body: str = ""
    kind: Literal["reply", "accept"] = "reply"


@router.post("/leads/{lead_id}/simulate-reply")
async def simulate(lead_id: uuid.UUID, body: ReplyBody, db: AsyncSession = Depends(get_db)) -> dict:
    _guard()
    if body.kind == "reply" and not body.body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "reply body is required")
    try:
        return await demo.simulate_reply(db, lead_id, body.body, body.kind)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


# ── Demo Control Centre ───────────────────────────────────────────────────────

from fastapi import Request  # noqa: E402
from pydantic import Field  # noqa: E402

from app.core.auth import principal_of  # noqa: E402
from app.pipeline import demo_control  # noqa: E402


def _who(request: Request):
    p = principal_of(request)
    return (p.name, p.role, p.tenant_id) if p else ("unknown", "operator", None)


@router.get("/control/status", summary="Demo environment status")
async def control_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    _guard()
    from sqlalchemy import func, select
    from app.core.gtm_config import get_gtm_config
    from app.models.base import Lead
    from app.models.pipeline import Campaign
    from app.pipeline.demo_clock import offset_hours
    from app.services.autonomy import is_paused
    name, role, tenant = _who(request)
    campaigns = (await db.execute(select(Campaign).order_by(Campaign.created_at))).scalars().all()
    leads = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
    return {"profile": get_gtm_config().profile, "presenter": name, "role": role,
            "clock_offset_hours": await offset_hours(db, tenant), "autonomy_paused": await is_paused(),
            "dataset_loaded": bool(campaigns), "leads": leads,
            "campaigns": [{"id": str(c.id), "name": c.name, "status": c.status} for c in campaigns],
            "scenarios": [{"key": k, **v} for k, v in demo_control.SCENARIOS.items()],
            "reply_types": list(demo_control.demo_data.REPLY_TEMPLATES)}


@router.post("/load", summary="Load the demo dataset (idempotent)")
async def control_load() -> dict:
    _guard()
    return await demo_control.load()


@router.post("/reset", summary="Delete all demo-organisation data and reload the dataset")
async def control_reset() -> dict:
    _guard()
    return await demo_control.reset()


@router.post("/start", summary="Start the complete pipeline for my organisation's demo campaign")
async def control_start(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    _guard()
    from sqlalchemy import select
    from app.models.pipeline import Campaign
    from app.pipeline import campaigns as camp
    name, _, _ = _who(request)
    c = (await db.execute(select(Campaign).where(Campaign.status != "stopped").order_by(Campaign.created_at))
         ).scalars().first()
    if c is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no campaign: load the demo dataset first")
    try:
        result = await camp.start(db, c, name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    return {"campaign": c.name, **result}


class AdvanceBody(BaseModel):
    hours: float = Field(..., gt=0, le=720)


@router.post("/clock/advance", summary="Advance the demo clock for my organisation")
async def control_advance(body: AdvanceBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    _guard()
    from app.pipeline import demo_clock
    _, _, tenant = _who(request)
    try:
        res = await demo_clock.advance(db, tenant, body.hours)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    ticks = await demo_control.tick_now(2)
    return {**res, "follow_ups_due": sum(t.get("follow_ups_due", 0) for t in ticks)}


@router.post("/tick", summary="Run the autonomous loop now")
async def control_tick() -> dict:
    _guard()
    reports = await demo_control.tick_now(1)
    r = reports[0]
    return {k: r[k] for k in ("paused", "reactions", "tasks_executed", "follow_ups_due", "held", "recovered")} | {
        "cycles": r["cycles"]}


@router.post("/leads/{lead_id}/reply", summary="Trigger a prospect reply through the simulated inbox")
async def control_reply(lead_id: uuid.UUID, body: dict) -> dict:
    _guard()
    kind = str(body.get("kind") or "")
    text = body.get("body")
    try:
        res = await demo_control.simulate_reply(lead_id, kind, text if isinstance(text, str) and text.strip() else None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await demo_control.tick_now()
    return res


class ScenarioBody(BaseModel):
    auto_approve: bool = True


@router.post("/scenarios/{key}/run", summary="Run a predefined scenario end to end")
async def control_scenario(key: str, body: ScenarioBody, request: Request) -> dict:
    _guard()
    name, role, tenant = _who(request)
    try:
        return await demo_control.run_scenario(key, tenant_id=tenant, actor=name, role=role,
                                               auto_approve=body.auto_approve)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
