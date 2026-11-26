"""Sign-in: API key or (demo profile only) demo user picker -> HttpOnly signed session cookie."""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import (COOKIE, DEMO_ACCESS_COOKIE, SESSION_TTL_S, auth_required, check_demo_code,
                           demo_code_required, demo_login_enabled, demo_unlocked, hosted_secret_problem,
                           issue_session, local_open_mode, match_key, principal_from_key, resolve,
                           secret_is_default)
from app.core.database import AsyncSessionLocal

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=512)


class DemoLoginBody(BaseModel):
    user_id: uuid.UUID


class DemoUnlockBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=200)


def _demo_gate(request: Request) -> None:
    if not demo_login_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    problem = hosted_secret_problem()
    if problem:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, problem)
    if not demo_unlocked(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "demo access code required")


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=SESSION_TTL_S, path="/")


async def _org_name(tenant_id) -> str | None:
    from app.models.base import Organization
    async with AsyncSessionLocal() as s:
        org = await s.get(Organization, tenant_id)
        return org.name if org else None


@router.get("/me", summary="Who am I, and is sign-in required?")
async def me(request: Request) -> dict:
    from app.core.database import is_serverless
    from app.pipeline.bootstrap import ensure_ready
    await ensure_ready()                       # serverless: first visit migrates + loads the demo dataset
    principal = await resolve(request)
    body = {"auth_required": auth_required(), "demo_login": demo_login_enabled(), "principal": None,
            "role": None, "organisation": None, "tenant_id": None, "serverless": is_serverless(),
            "demo_code_required": demo_code_required(), "demo_unlocked": demo_unlocked(request)}
    if principal is None and local_open_mode():
        from app.core.tenancy import DEFAULT_TENANT_ID
        return {**body, "principal": "local-dev", "role": "admin", "tenant_id": str(DEFAULT_TENANT_ID),
                "organisation": await _org_name(DEFAULT_TENANT_ID)}
    if principal is not None:
        body.update(principal=principal.name, role=principal.role, tenant_id=str(principal.tenant_id),
                    organisation=await _org_name(principal.tenant_id))
    return body


@router.post("/login", summary="Sign in with an API key (sets an HttpOnly session cookie)")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    if secret_is_default() and not local_open_mode() and not demo_login_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SECRET_KEY is not configured on this server")
    principal = await principal_from_key(body.api_key)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    subject = f"e:{principal.name}" if match_key(body.api_key) else f"u:{principal.user_id}"
    _set_cookie(request, response, issue_session(subject))
    return {"principal": principal.name, "role": principal.role}


@router.post("/demo-unlock", summary="Enter the demo access code (hosted demos)")
async def demo_unlock(body: DemoUnlockBody, request: Request, response: Response) -> dict:
    if not demo_login_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    problem = hosted_secret_problem()
    if problem:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, problem)
    if not demo_code_required():
        return {"unlocked": True}
    if not check_demo_code(body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect access code")
    response.set_cookie(DEMO_ACCESS_COOKIE, issue_session("demo-access"), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=SESSION_TTL_S, path="/")
    return {"unlocked": True}


@router.get("/demo-users", summary="Demo sign-in choices (demo profile only)")
async def demo_users(request: Request) -> list[dict]:
    _demo_gate(request)
    from app.models.base import Organization
    from app.models.pipeline import AppUser
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(AppUser, Organization).join(Organization, Organization.id == AppUser.tenant_id)
                                .where(AppUser.is_demo.is_(True), AppUser.is_active.is_(True))
                                .order_by(Organization.name, AppUser.role))).all()
    return [{"id": str(u.id), "name": u.name, "email": u.email, "role": u.role, "organisation": o.name,
             "tenant_id": str(o.id)} for u, o in rows]


@router.post("/demo-login", summary="Sign in as a demo user (demo profile only)")
async def demo_login(body: DemoLoginBody, request: Request, response: Response) -> dict:
    _demo_gate(request)
    from app.models.pipeline import AppUser
    async with AsyncSessionLocal() as s:
        user = await s.get(AppUser, body.user_id)
    if user is None or not user.is_demo or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such demo user")
    _set_cookie(request, response, issue_session(f"u:{user.id}"))
    return {"principal": user.name, "role": user.role}


@router.post("/logout", summary="Sign out")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}
