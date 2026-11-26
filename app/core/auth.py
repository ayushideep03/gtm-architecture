"""
Authentication, roles and tenant context.

Who is calling (principal) is established from, in order:
  1. ``X-API-Key`` / ``Authorization: Bearer`` header:
       - a key from OXYGEN_API_KEYS ("name=key,...")  -> admin of the default organisation
       - a user key (app_users.api_key_hash = sha256)  -> that user's organisation and role
  2. the ``oxygen_session`` cookie: an HMAC-signed, expiring token issued by
     /auth/login (API key) or, in the demo profile only, /auth/demo-login.
  3. nothing: only in the development/test profiles with no keys configured,
     an unauthenticated local admin ("local-dev") on the default organisation.

Fail closed: in any other profile (production, staging, demo) a request without
a valid principal gets 401, and with no way to sign in at all, 503.

The principal's organisation becomes the request's tenant context (row-level
security), and its role is checked against ROLE_POLICY before the route runs.
The principal's name is what approvals record as the reviewer.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

from app.core.tenancy import DEFAULT_TENANT_ID, current_tenant

LOCAL_PROFILES = {"development", "test"}        # open local mode allowed here (when no keys exist)
DEMO_PROFILES = {"demo"}                          # sign-in via demo user picker
COOKIE = "oxygen_session"
LOCAL_PRINCIPAL = "local-dev"
SESSION_TTL_S = 12 * 3600
ROLES = ("admin", "operator", "approver")


@dataclass(frozen=True)
class Principal:
    name: str
    tenant_id: uuid.UUID
    role: str
    authenticated: bool            # False only for the local-dev open mode
    user_id: Optional[uuid.UUID] = None
    kind: str = "user"             # user | env | local


# ── Role policy ───────────────────────────────────────────────────────────────
# First matching rule wins. GET/HEAD are reads (every role). Everything is relative to /api/v1.
ROLE_POLICY: list[tuple[set[str], re.Pattern, set[str]]] = [
    ({"POST"}, re.compile(r"^/approvals/[^/]+/(approve|reject)$"), {"admin", "approver"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/admin(/|$)"), {"admin"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/integrations(/|$)"), {"admin"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/demo/(reset|load)$"), {"admin"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/compliance/suppressions/[^/]+/lift$"), {"admin"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/execution/tasks/[^/]+/retry$"), {"admin", "operator"}),
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r".*"), {"admin", "operator"}),
]


def allowed_roles(method: str, path: str) -> set[str]:
    rel = path.split("/api/v1", 1)[-1] or "/"
    if method in ("GET", "HEAD", "OPTIONS"):
        return set(ROLES)
    for methods, pattern, roles in ROLE_POLICY:
        if method in methods and pattern.match(rel):
            return roles
    return {"admin"}


# ── Keys and sessions ─────────────────────────────────────────────────────────

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def configured_keys() -> dict[str, str]:
    """name -> key, from OXYGEN_API_KEYS. Malformed or short entries are ignored."""
    keys: dict[str, str] = {}
    for entry in (os.environ.get("OXYGEN_API_KEYS") or "").split(","):
        name, sep, key = entry.strip().partition("=")
        if sep and name.strip() and len(key.strip()) >= 16:
            keys[name.strip()] = key.strip()
    return keys


def match_key(candidate: Optional[str]) -> Optional[str]:
    """Principal name for an OXYGEN_API_KEYS key (constant-time compare against every key)."""
    if not candidate:
        return None
    found = None
    for name, key in configured_keys().items():
        if hmac.compare_digest(candidate.encode(), key.encode()):
            found = name
    return found


def _secret() -> bytes:
    from app.core.config import get_settings
    return get_settings().SECRET_KEY.encode()


def secret_is_default() -> bool:
    return _secret().startswith(b"change-me")


def issue_session(subject: str, ttl_s: int = SESSION_TTL_S) -> str:
    """subject = 'u:<user uuid>' or 'e:<env key name>'."""
    exp = str(int(time.time()) + ttl_s)
    msg = f"v1|{subject}|{exp}"
    sig = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}|{sig}"


def read_session(token: Optional[str]) -> Optional[str]:
    if not token or token.count("|") != 3:
        return None
    version, subject, exp, sig = token.split("|")
    expected = hmac.new(_secret(), f"{version}|{subject}|{exp}".encode(), hashlib.sha256).hexdigest()
    if version != "v1" or not hmac.compare_digest(sig, expected) or not exp.isdigit() or int(exp) < time.time():
        return None
    return subject


def _profile() -> str:
    from app.core.gtm_config import get_gtm_config
    return get_gtm_config().profile


def local_open_mode() -> bool:
    return _profile() in LOCAL_PROFILES and not configured_keys()


def auth_required() -> bool:
    return not local_open_mode()


def demo_login_enabled() -> bool:
    return _profile() in DEMO_PROFILES


# ── Resolution ────────────────────────────────────────────────────────────────

async def _user_principal(**where) -> Optional[Principal]:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.pipeline import AppUser
    async with AsyncSessionLocal() as s:   # no tenant context yet: users are looked up across organisations
        stmt = select(AppUser).where(AppUser.is_active.is_(True))
        for k, v in where.items():
            stmt = stmt.where(getattr(AppUser, k) == v)
        user = (await s.execute(stmt)).scalar_one_or_none()
    if user is None:
        return None
    return Principal(user.name, user.tenant_id, user.role, True, user.id, "user")


async def principal_from_key(key: Optional[str]) -> Optional[Principal]:
    if not key:
        return None
    name = match_key(key)
    if name:
        return Principal(name, DEFAULT_TENANT_ID, "admin", True, None, "env")
    return await _user_principal(api_key_hash=hash_key(key))


async def principal_from_session(token: Optional[str]) -> Optional[Principal]:
    subject = read_session(token)
    if not subject:
        return None
    kind, _, ref = subject.partition(":")
    if kind == "e" and ref in configured_keys():
        return Principal(ref, DEFAULT_TENANT_ID, "admin", True, None, "env")
    if kind == "u":
        try:
            return await _user_principal(id=uuid.UUID(ref))
        except ValueError:
            return None
    return None


def _presented_key(request: Request) -> Optional[str]:
    header = request.headers.get("x-api-key")
    if header:
        return header
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def resolve(request: Request) -> Optional[Principal]:
    return (await principal_from_key(_presented_key(request))
            or await principal_from_session(request.cookies.get(COOKIE)))


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency for every protected route: authenticate, authorise, set the tenant context."""
    principal = await resolve(request)
    if principal is None:
        if local_open_mode():
            principal = Principal(LOCAL_PRINCIPAL, DEFAULT_TENANT_ID, "admin", False, None, "local")
        elif not configured_keys() and not demo_login_enabled() and not await _any_user_keys():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "API authentication is not configured: set OXYGEN_API_KEYS")
        else:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required",
                                headers={"WWW-Authenticate": "Bearer"})
    roles = allowed_roles(request.method, request.url.path)
    if principal.role not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"your role ({principal.role}) cannot do this; requires: {', '.join(sorted(roles))}")
    request.state.principal = principal
    current_tenant.set(principal.tenant_id)      # row-level security for everything this request touches
    return principal


async def _any_user_keys() -> bool:
    from sqlalchemy import func, select
    from app.core.database import AsyncSessionLocal
    from app.models.pipeline import AppUser
    async with AsyncSessionLocal() as s:
        return bool((await s.execute(select(func.count(AppUser.id)).where(AppUser.api_key_hash.is_not(None)))).scalar())


def reviewer_for(request: Request, claimed: Optional[str]) -> str:
    """Who to record as the reviewer: the authenticated principal, never a free-text claim.
    In local open mode the claimed name is kept but marked as unverified."""
    principal: Optional[Principal] = getattr(request.state, "principal", None)
    if principal is not None and principal.authenticated:
        return principal.name
    return f"{(claimed or 'anonymous').strip()[:100]} (unverified, local-dev)"


def principal_of(request: Request) -> Optional[Principal]:
    return getattr(request.state, "principal", None)


# ── Demo access code (hosted demos) ───────────────────────────────────────────
# When DEMO_ACCESS_CODE is set, the demo-user picker and demo sign-in require it first:
# POST /auth/demo-unlock {code} sets a signed, expiring cookie. Unset = local behaviour.

DEMO_ACCESS_COOKIE = "oxygen_demo_access"


def demo_code_required() -> bool:
    return bool(os.environ.get("DEMO_ACCESS_CODE"))


def check_demo_code(code: str) -> bool:
    expected = os.environ.get("DEMO_ACCESS_CODE") or ""
    return bool(expected) and hmac.compare_digest(code.strip().encode(), expected.encode())


def demo_unlocked(request: Request) -> bool:
    if not demo_code_required():
        return True
    return read_session(request.cookies.get(DEMO_ACCESS_COOKIE)) == "demo-access"


def hosted_secret_problem() -> Optional[str]:
    """A hosted (serverless) deployment must not sign sessions with the default SECRET_KEY:
    anyone could forge a session cookie and skip sign-in and the access code."""
    from app.core.database import is_serverless
    if is_serverless() and secret_is_default():
        return "SECRET_KEY is not configured on this deployment"
    return None
