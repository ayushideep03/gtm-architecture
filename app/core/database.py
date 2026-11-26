"""
Database session management (SQLAlchemy + PostgreSQL).

Provides:
- Async-compatible engine / session factory
- A FastAPI dependency: get_db()
- A health-check probe: check_db_health()
"""

import os

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


def is_serverless() -> bool:
    """Running as short-lived serverless functions (Vercel sets VERCEL=1)."""
    return bool(os.environ.get("VERCEL") or os.environ.get("OXYGEN_SERVERLESS"))


def database_url() -> str:
    """The URL to connect with. Hosted Postgres (Neon via Vercel) also provides a direct,
    unpooled URL: prefer it, because a transaction-mode pooler (PgBouncer) breaks asyncpg's
    prepared statements, while the per-transaction tenant setting works on either."""
    for name in ("DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING"):   # Neon / Vercel Postgres names
        if os.environ.get(name):
            return os.environ[name]
    if not os.environ.get("DATABASE_URL") and os.environ.get("POSTGRES_URL"):
        return os.environ["POSTGRES_URL"]
    return get_settings().DATABASE_URL


def _build_async_url(url: str) -> str:
    """Convert sync postgres:// URL to async asyncpg driver URL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def split_ssl(url: str) -> tuple[str, dict]:
    """asyncpg rejects libpq URL options such as ``sslmode``/``channel_binding`` (hosted Postgres
    adds them). Strip them and express TLS as a connect argument instead."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    connect_args: dict = {}
    if sslmode and sslmode not in ("disable", "allow", "prefer"):
        connect_args["ssl"] = True
    return urlunsplit(parts._replace(query=urlencode(query))), connect_args


def async_url_and_args() -> tuple[str, dict]:
    return split_ssl(_build_async_url(database_url()))


def create_engine_and_session() -> tuple:
    async_url, connect_args = async_url_and_args()
    echo = os.environ.get("SQL_ECHO", "").lower() in ("1", "true", "yes")   # opt-in SQL logging
    if is_serverless():
        # Each invocation may run on a fresh event loop and the instance may freeze between
        # requests: keep no connections around (NullPool) instead of a long-lived pool.
        from sqlalchemy.pool import NullPool
        engine = create_async_engine(async_url, echo=echo, poolclass=NullPool, connect_args=connect_args)
    else:
        engine = create_async_engine(
            async_url,
            echo=echo,
            pool_pre_ping=True,        # verify connections before use
            pool_size=10,
            max_overflow=20,
            connect_args=connect_args,
        )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    return engine, session_factory


# Module-level singletons — initialised once on import
engine, AsyncSessionLocal = create_engine_and_session()


# ── Tenant isolation (row-level security) ─────────────────────────────────────
# Every transaction carries the current tenant (if any) as the transaction-local
# setting ``app.tenant_id``; the RLS policies from migration 0012 only expose and
# accept that tenant's rows. No tenant set (worker queue scans, migrations,
# tests) means the setting is absent and the policies allow every tenant.

from sqlalchemy import event as _sa_event, text as _sa_text  # noqa: E402
from sqlalchemy.orm import Session as _SyncSession  # noqa: E402


# Migration 0013's RLS-enforcing role, named after the database (roles are server-wide).
# The right to switch to it ("SET", PostgreSQL 16+; membership implied it before).
CAN_SET_ROLE = "(CASE WHEN current_setting('server_version_num')::int >= 160000 THEN 'SET' ELSE 'MEMBER' END)"
ENFORCING_ROLE_SQL = "'oxygen_app_' || left(regexp_replace(lower(current_database()), '[^a-z0-9_]', '_', 'g'), 50)"


async def rls_status() -> dict:
    """Whether tenant isolation is enforced by PostgreSQL for organisation-scoped transactions."""
    async with engine.connect() as c:
        row = (await c.execute(_sa_text(
            f"SELECT r.rolbypassrls, (SELECT pg_has_role(current_user, e.oid, {CAN_SET_ROLE}) FROM pg_roles e "
            f" WHERE e.rolname = {ENFORCING_ROLE_SQL}) FROM pg_roles r WHERE r.rolname = current_user"))).first()
    bypass, member = bool(row[0]), bool(row[1])
    return {"connection_bypasses_rls": bypass, "enforcing_role": member, "enforced": (not bypass) or member}


@_sa_event.listens_for(_SyncSession, "after_begin")
def _apply_tenant(session, transaction, connection) -> None:
    from app.core.tenancy import current_tenant
    tenant = current_tenant.get()
    if tenant is not None:
        # Same statement, transaction-local: switch to the RLS-enforcing role (migration 0013) where it
        # exists and this connection may assume it. Hosted owner roles often have BYPASSRLS, which would
        # make PostgreSQL ignore every tenant policy; ``oxygen_app`` never does.
        connection.execute(_sa_text(
            "SELECT set_config('app.tenant_id', :t, true), "
            "(SELECT set_config('role', r.rolname, true) FROM pg_roles r "
            f" WHERE r.rolname = {ENFORCING_ROLE_SQL} AND pg_has_role(current_user, r.oid, {CAN_SET_ROLE}))"),
            {"t": str(tenant)})


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session for use in route handlers.

    Usage::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Health check ─────────────────────────────────────────────────────────────

async def check_db_health() -> dict:
    """
    Execute a trivial query to verify the database is reachable.

    Returns a status dict compatible with the /health/db endpoint.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "detail": "PostgreSQL reachable"}
    except OperationalError as exc:
        logger.error("Database health check failed: %s", exc)
        return {"status": "error", "detail": type(exc).__name__}  # details are logged, not returned to unauthenticated callers
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected database error: %s", exc)
        return {"status": "error", "detail": type(exc).__name__}  # details are logged, not returned to unauthenticated callers
