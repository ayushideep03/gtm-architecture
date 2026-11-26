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
# Test profile: config/gtm.test.yaml (mock providers enabled explicitly).
os.environ.setdefault("GTM_PROFILE", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Tests use their OWN database so running them never wipes development/demo data.
# Override with TEST_DATABASE_URL; DATABASE_URL from .env is deliberately ignored here.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://gtm_user:gtm_password@localhost:5432/gtm_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")


def _prepare_test_database() -> None:
    """Create the test database if needed and migrate it to head (once per session)."""
    import subprocess
    import sys
    from urllib.parse import urlparse

    url = urlparse(os.environ["DATABASE_URL"])
    dbname = url.path.lstrip("/")
    try:
        import psycopg2
        conn = psycopg2.connect(dbname="postgres", user=url.username, password=url.password,
                                host=url.hostname, port=url.port or 5432, connect_timeout=3)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{dbname}"')
        conn.close()
    except Exception:
        return  # PostgreSQL unavailable: DB tests self-skip
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=dict(os.environ),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_prepare_test_database()


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
    "follow_ups",
    "meetings",
    "drafts",
    "demo_clock",
    "campaign_accounts",
    "app_users",
    "approvals",
    "work_items",
    "agent_decisions",
    "research_findings",
    "fact_evidence",
    "facts",
    "suppressions",
    "contact_consents",
    "source_records",
    "event_processing_states",
    "events",
    "interactions",
    "tasks",
    "leads",
    "campaigns",
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
        "TestInboundEvents",
        "TestAutonomy",
        "TestLeadSources",
        "TestAttribution",
        "TestDashboard",
        "TestLearning",
        "TestDemoPipeline", "TestTenantIsolation", "TestRolePermissions", "TestCampaignControls",
        "TestReadinessFindings", "TestReplyWithdrawsQueuedOutreach", "TestDemoModeGuard",
        "TestSmtp", "TestRoundTrip", "TestBillionMail", "TestReplyGapWaiver", "TestDemoScenarios",
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



# ---------------------------------------------------------------------------
# Shared runtime state isolation (Redis provider health / rate limits)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_runtime_state():
    """Provider health and rate limits live in Redis under the test profile's
    key prefix; clear them so one test's failures can't cool down providers
    for the next."""
    try:
        import redis as sync_redis
        from app.core.config import get_settings
        from app.core.gtm_config import get_gtm_config
        prefix = get_gtm_config().runtime.key_prefix
        client = sync_redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1)
        keys = list(client.scan_iter(f"{prefix}:*"))
        if keys:
            client.delete(*keys)
        client.close()
    except Exception:
        pass  # Redis unavailable: trackers fall back to in-memory state
    yield


@pytest.fixture(autouse=True)
def _fresh_connection_pool():
    """Each test runs on its own event loop; asyncpg connections pooled on a previous loop
    cannot be reused. Drop the pool (without awaiting the old loop) before every test."""
    from app.core.database import engine
    engine.sync_engine.dispose(close=False)
    yield
