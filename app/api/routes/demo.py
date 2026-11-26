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
