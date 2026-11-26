"""Organisation, users and integrations settings (admin writes; any signed-in role may read)."""

import secrets
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_key, principal_of
from app.core.database import get_db
from app.models.base import Organization
from app.models.pipeline import AppUser
from app.pipeline import integrations

admin_router = APIRouter(prefix="/admin", tags=["admin"])
integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])


def _user_view(u: AppUser) -> dict:
    return {"id": str(u.id), "name": u.name, "email": u.email, "role": u.role, "is_active": u.is_active,
            "is_demo": u.is_demo, "has_api_key": bool(u.api_key_hash), "created_at": u.created_at}


@admin_router.get("/organisation", summary="My organisation and its users")
async def organisation(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    p = principal_of(request)
    org = await db.get(Organization, p.tenant_id) if p else None
    users = (await db.execute(select(AppUser).order_by(AppUser.role, AppUser.name))).scalars().all()
    return {"organisation": {"id": str(org.id), "name": org.name, "slug": org.slug} if org else None,
            "you": {"name": p.name, "role": p.role} if p else None,
            "users": [_user_view(u) for u in users],
            "roles": {"admin": "Everything, including users, integrations and demo reset",
                      "operator": "Run campaigns and workflows, draft and submit messages; cannot approve",
                      "approver": "Approve or reject external actions; read everything; cannot run workflows"}}


class UserBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    email: str = Field(..., min_length=3, max_length=255)
    role: Literal["admin", "operator", "approver"]


class UserPatch(BaseModel):
    role: Optional[Literal["admin", "operator", "approver"]] = None
    is_active: Optional[bool] = None


@admin_router.post("/users", summary="Add a user (returns their API key ONCE)")
async def add_user(body: UserBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    key = "oxy_" + secrets.token_urlsafe(32)
    u = AppUser(name=body.name, email=body.email, role=body.role, api_key_hash=hash_key(key))
    db.add(u)
    await db.commit()
    return {**_user_view(u), "api_key": key, "note": "Shown once. Store it securely; only its hash is kept."}


@admin_router.patch("/users/{user_id}", summary="Change a user's role or deactivate them")
async def patch_user(user_id: uuid.UUID, body: UserPatch, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    u = await db.get(AppUser, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    p = principal_of(request)
    if p and p.user_id == u.id and (body.is_active is False or (body.role and body.role != "admin")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot remove your own admin access")
    if body.role:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    await db.commit()
    return _user_view(u)


@integrations_router.get("", summary="Integration settings and status")
async def list_integrations() -> dict:
    from app.core.gtm_config import get_gtm_config
    return {"profile": get_gtm_config().profile, "demo_mode": get_gtm_config().profile == "demo",
            "integrations": integrations.overview()}


@integrations_router.post("/{key}/test", summary="Connection test (simulated in demo mode)")
async def test_integration(key: str) -> dict:
    try:
        return await integrations.test_connection(key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
