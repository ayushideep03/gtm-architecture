"""Dashboard sign-in: exchange an API key for an HttpOnly session cookie."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.auth import COOKIE, LOCAL_PRINCIPAL, auth_required, configured_keys, match_key

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=512)


@router.get("/me", summary="Who am I, and is sign-in required?")
async def me(request: Request) -> dict:
    required = auth_required()
    if not configured_keys():
        return {"auth_required": required, "configured": False,
                "principal": None if required else LOCAL_PRINCIPAL}
    name = match_key(request.headers.get("x-api-key") or request.cookies.get(COOKIE))
    return {"auth_required": True, "configured": True, "principal": name}


@router.post("/login", summary="Sign in with an API key (sets an HttpOnly cookie)")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    name = match_key(body.api_key)
    if name is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    response.set_cookie(COOKIE, body.api_key, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=12 * 3600, path="/")
    return {"principal": name}


@router.post("/logout", summary="Sign out")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}
