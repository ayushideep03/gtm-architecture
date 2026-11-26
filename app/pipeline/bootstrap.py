"""
Serverless self-setup (Vercel): a fresh deployment has an empty database and no worker.

``ensure_ready`` (called by the first requests a browser makes and by the cron) brings the
database to the latest migration and, in the demo profile, loads the demo dataset. It runs
once per instance, under a PostgreSQL advisory lock so concurrent cold starts don't race.
Outside serverless it does nothing: there ``python start.py`` migrates and seeds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from app.core.logging import get_logger

logger = get_logger(__name__)
ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_LOCK = 0x6F787967656E02
_ready = False


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _upgrade() -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


async def _current_revision(conn) -> str | None:
    exists = (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar()
    if not exists:
        return None
    return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()


async def ensure_ready(*, force: bool = False) -> dict:
    global _ready
    from app.core.database import engine, is_serverless
    if (_ready and not force) or (not is_serverless() and not force):
        return {"ready": True, "migrated": False, "seeded": False}
    head = _head()
    migrated = seeded = False
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": BOOTSTRAP_LOCK})
        try:
            if await _current_revision(conn) != head:
                logger.info("Bootstrap: migrating database to %s", head)
                await asyncio.to_thread(_upgrade)       # alembic runs its own event loop
                migrated = True
            await conn.commit()
            from app.core.gtm_config import get_gtm_config
            if get_gtm_config().profile == "demo":
                users = (await conn.execute(text("SELECT count(*) FROM app_users WHERE is_demo"))).scalar()
                if not users:
                    from app.pipeline import demo_control
                    await demo_control.load()
                    seeded = True
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BOOTSTRAP_LOCK})
            await conn.commit()
    from app.core.database import rls_status
    rls = await rls_status()
    if not rls["enforced"]:
        # Fail closed: never serve a multi-tenant app whose database ignores the tenant policies.
        raise RuntimeError("Tenant isolation is not enforced: the database role bypasses row-level security "
                           "and the oxygen_app enforcing role is unavailable (migration 0013 needs CREATEROLE).")
    _ready = True
    return {"ready": True, "migrated": migrated, "seeded": seeded, "revision": head, "rls": rls}
