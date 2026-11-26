"""
API authentication.

Every /api/v1 route except health, signed webhooks and the login endpoints
requires a principal. Keys come from the environment, never from config files:

    OXYGEN_API_KEYS="alex=<long random key>,ops-bot=<another key>"

A client authenticates with ``X-API-Key: <key>`` (scripts) or with the
``oxygen_session`` cookie set by ``POST /api/v1/auth/login`` (the dashboard;
HttpOnly, SameSite=Strict). The principal's name is what approvals record as
the reviewer, so a caller cannot approve "as" someone else.

Fail closed: outside the local profiles (development, demo, test) a server with
no keys configured refuses every protected request with 503. In the local
profiles, no keys means open access as the principal "local-dev", which is
reported by GET /api/v1/auth/me so the dashboard can show it.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

LOCAL_PROFILES = {"development", "demo", "test"}
COOKIE = "oxygen_session"
LOCAL_PRINCIPAL = "local-dev"


@dataclass(frozen=True)
class Principal:
    name: str
    authenticated: bool   # False only for the local-dev open mode


def configured_keys() -> dict[str, str]:
    """name -> key, from OXYGEN_API_KEYS. Malformed or short entries are ignored."""
    keys: dict[str, str] = {}
    for entry in (os.environ.get("OXYGEN_API_KEYS") or "").split(","):
        name, sep, key = entry.strip().partition("=")
        if sep and name.strip() and len(key.strip()) >= 16:
            keys[name.strip()] = key.strip()
    return keys


def _profile() -> str:
    from app.core.gtm_config import get_gtm_config
    return get_gtm_config().profile


def auth_required() -> bool:
    return bool(configured_keys()) or _profile() not in LOCAL_PROFILES


def match_key(candidate: Optional[str]) -> Optional[str]:
    """Return the principal name for a key (constant-time compare against every key)."""
    if not candidate:
        return None
    found = None
    for name, key in configured_keys().items():
        if hmac.compare_digest(candidate.encode(), key.encode()):
            found = name
    return found


def _presented_key(request: Request) -> Optional[str]:
    header = request.headers.get("x-api-key")
    if header:
        return header
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE)


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency for every protected route."""
    keys = configured_keys()
    if not keys:
        if _profile() in LOCAL_PROFILES:
            principal = Principal(LOCAL_PRINCIPAL, authenticated=False)
            request.state.principal = principal
            return principal
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "API authentication is not configured: set OXYGEN_API_KEYS")
    name = match_key(_presented_key(request))
    if name is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    principal = Principal(name, authenticated=True)
    request.state.principal = principal
    return principal


def reviewer_for(request: Request, claimed: Optional[str]) -> str:
    """Who to record as the reviewer: the authenticated principal, never a free-text claim.
    In local open mode the claimed name is kept but marked as unverified."""
    principal: Optional[Principal] = getattr(request.state, "principal", None)
    if principal is not None and principal.authenticated:
        return principal.name
    return f"{(claimed or 'anonymous').strip()[:100]} (unverified, local-dev)"
