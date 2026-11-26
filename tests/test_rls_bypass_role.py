"""
Tenant isolation must hold when the database owner role has BYPASSRLS (as Neon's
``neondb_owner`` does). Reproduces that setup in a scratch database and checks that
organisation-scoped transactions run as ``oxygen_app`` (migration 0013) and only see their
own rows. Needs local superuser access to create the roles; skipped otherwise.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHECK = r'''
import asyncio
from sqlalchemy import select, text
async def main():
    from app.core.database import AsyncSessionLocal, engine
    from app.core.tenancy import tenant_scope
    from app.models.base import Lead
    from app.pipeline import demo_control
    await demo_control.load()
    ns, hx = demo_control.org_id("northstar"), demo_control.org_id("helix")
    for t in (ns, hx):
        with tenant_scope(t):
            async with AsyncSessionLocal() as s:
                s.add(Lead(source="bypass-test", status="new")); await s.commit()
    async with engine.connect() as c:
        assert (await c.execute(text("select rolbypassrls from pg_roles where rolname=current_user"))).scalar()
        await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(hx)})
        assert (await c.execute(text("select count(distinct tenant_id) from leads"))).scalar() == 2  # the hazard
        await c.rollback()
    with tenant_scope(hx):
        async with AsyncSessionLocal() as s:
            assert (await s.execute(text("select current_user"))).scalar().startswith("oxygen_app_")
            assert set((await s.execute(select(Lead.tenant_id))).scalars().all()) == {hx}
            s.add(Lead(tenant_id=ns, source="x", status="new"))
            try:
                await s.flush(); raise SystemExit("cross-tenant write was allowed")
            except Exception as e:
                assert "row-level security" in str(e)
    from app.core.database import rls_status
    assert (await rls_status())["enforced"] is True
    print("OK")
asyncio.run(main())
'''


def _superuser_conn():
    try:
        import psycopg2
        return psycopg2.connect(dbname="postgres", host="localhost")
    except Exception:
        return None


@pytest.mark.skipif(_superuser_conn() is None, reason="needs local PostgreSQL superuser access")
def test_isolation_holds_with_bypassrls_owner():
    owner, db = f"bypass_owner_{uuid.uuid4().hex[:6]}", f"gtm_bypass_{uuid.uuid4().hex[:6]}"
    pw = uuid.uuid4().hex
    su = _superuser_conn()
    su.autocommit = True
    cur = su.cursor()
    cur.execute("select rolsuper from pg_roles where rolname = current_user")
    if not cur.fetchone()[0]:
        pytest.skip("local role is not a superuser")
    cur.execute(f"CREATE ROLE {owner} LOGIN PASSWORD %s BYPASSRLS CREATEROLE", (pw,))
    cur.execute(f"CREATE DATABASE {db} OWNER {owner}")
    try:
        env = {**os.environ, "DATABASE_URL": f"postgresql://{owner}:{pw}@localhost:5432/{db}", "GTM_PROFILE": "demo",
               "GTM_CONFIG_FILE": ""}
        env.pop("DATABASE_URL_UNPOOLED", None)
        mig = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                             capture_output=True, text=True)
        assert mig.returncode == 0, mig.stderr[-500:]
        run = subprocess.run([sys.executable, "-c", CHECK], cwd=ROOT, env=env, capture_output=True, text=True)
        assert run.returncode == 0 and "OK" in run.stdout, (run.stdout + run.stderr)[-1500:]
    finally:
        cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        cur.execute(f"DROP ROLE IF EXISTS oxygen_app_{db}")
        cur.execute(f"DROP ROLE IF EXISTS {owner}")
        su.close()
