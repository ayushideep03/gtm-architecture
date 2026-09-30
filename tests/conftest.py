"""
pytest configuration for the GTM system test suite.

Provides:
- Async test event loop setup
- TestClient fixture for API tests
- Environment overrides so tests use isolated settings
- DB isolation fixture for database integration tests
"""

import asyncio
import os

import pytest

# Force test environment before any app modules are imported
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://gtm_user:gtm_password@localhost:5432/gtm_db"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def app():
    """Return the FastAPI application instance."""
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    """Synchronous TestClient wrapping the FastAPI app."""
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session():
    """Ensure engine and redis pool are disposed cleanly when pytest session ends."""
    yield
    import asyncio
    from app.core.database import engine
    from app.core.redis import close_redis_pool

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(close_redis_pool())
        loop.run_until_complete(engine.dispose())
    except Exception:
        pass
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# DB integration test isolation
# ---------------------------------------------------------------------------

# Tables in FK-safe deletion order (children before parents).
_CLEANUP_TABLE_ORDER = [
    "event_processing_states",
    "events",
    "interactions",
    "tasks",
    "leads",
    "people",
    "companies",
]


async def _truncate_tables() -> None:
    """
    Truncate all GTM tables in FK-safe order.

    Creates its own short-lived engine so it does NOT share the
    module-level singleton across different event loops (asyncpg
    connections are loop-bound and mixing loops causes warnings).
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    from app.core.database import _build_async_url
    from app.core.config import get_settings

    settings = get_settings()
    async_url = _build_async_url(settings.DATABASE_URL)

    engine = create_async_engine(async_url, echo=False)
    try:
        # Discover which tables actually exist.
        try:
            async with engine.connect() as conn:
                existing: set[str] = set(
                    await conn.run_sync(
                        lambda sync_conn: inspect(sync_conn).get_table_names()
                    )
                )
        except OperationalError:
            return  # DB not available; tests will self-skip

        tables_to_clean = [t for t in _CLEANUP_TABLE_ORDER if t in existing]
        if not tables_to_clean:
            return

        table_list = ", ".join(tables_to_clean)
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            try:
                await session.execute(
                    text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
                )
                await session.commit()
            except OperationalError:
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _db_isolation(request):
    """
    Auto-use fixture: truncates all GTM tables before every test inside
    TestIngestionWithDatabase. Zero-overhead for all other tests.
    """
    cls = request.cls
    db_test_classes = {
        "TestIngestionWithDatabase",
        "TestEnrichmentWithDatabase",
        "TestOxygenWithDatabase",
        "TestOxygenAPI",
        "TestExecutionWithDatabase",
        "TestExecutionAPI",
        "TestGuardrailsWithDatabase",
        "TestGuardrailsAPI",
        "TestRevOpsWithDatabase",
        "TestRevOpsAPI",
        "TestEvalsWithDatabase",
        "TestEvalsAPI",
        "TestEndToEndGTM",
        "TestGTMCapabilitiesWithDatabase",
    }
    if cls is None or cls.__name__ not in db_test_classes:
        yield
        return

    # Drive the async cleanup in a fresh, isolated event loop so we do
    # not interfere with anyio's own loop that runs the test itself.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_truncate_tables())
    finally:
        loop.close()

    yield

