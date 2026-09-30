"""
Database session management (SQLAlchemy + PostgreSQL).

Provides:
- Async-compatible engine / session factory
- A FastAPI dependency: get_db()
- A health-check probe: check_db_health()
"""

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


def _build_async_url(url: str) -> str:
    """Convert sync postgres:// URL to async asyncpg driver URL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def create_engine_and_session() -> tuple:
    settings = get_settings()
    async_url = _build_async_url(settings.DATABASE_URL)

    engine = create_async_engine(
        async_url,
        echo=settings.is_development,
        pool_pre_ping=True,        # verify connections before use
        pool_size=10,
        max_overflow=20,
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
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected database error: %s", exc)
        return {"status": "error", "detail": str(exc)}
