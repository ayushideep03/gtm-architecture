"""Hosting on Vercel (serverless): connection URLs, cron secret, demo access code, secret guard."""

import pytest
from httpx import ASGITransport, AsyncClient


class TestHostedDatabaseUrl:
    def test_libpq_ssl_options_become_connect_args(self):
        from app.core.database import split_ssl
        url, args = split_ssl("postgresql+asyncpg://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require")
        assert url == "postgresql+asyncpg://u:p@ep-x.neon.tech/db" and args == {"ssl": True}
        assert split_ssl("postgresql+asyncpg://u:p@localhost/db?sslmode=disable") == (
            "postgresql+asyncpg://u:p@localhost/db", {})

    def test_unpooled_url_preferred(self, monkeypatch):
        from app.core.database import database_url
        monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgres://direct/db")
        assert database_url() == "postgres://direct/db"


class TestCronEndpoints:
    @pytest.mark.anyio
    async def test_cron_needs_a_configured_secret(self, app, monkeypatch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            monkeypatch.delenv("CRON_SECRET", raising=False)
            assert (await c.get("/api/v1/cron/tick")).status_code == 503          # never open
            monkeypatch.setenv("CRON_SECRET", "s" * 32)
            assert (await c.get("/api/v1/cron/tick")).status_code == 401
            assert (await c.get("/api/v1/cron/tick", headers={"Authorization": "Bearer wrong"})).status_code == 401
            r = await c.get("/api/v1/cron/tick", headers={"Authorization": "Bearer " + "s" * 32})
            assert r.status_code == 200 and "ticks" in r.json()


@pytest.fixture
def demo_hosted(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("runtime: {key_prefix: gtm-test-hosted}\n")
    monkeypatch.setenv("GTM_PROFILE", "demo")
    monkeypatch.setenv("GTM_CONFIG_FILE", str(overlay))
    monkeypatch.setenv("DEMO_ACCESS_CODE", "open-sesame-123")
    from app.providers import reload_providers
    reload_providers()
    yield
    monkeypatch.undo()
    reload_providers()


@pytest.mark.usefixtures("demo_hosted")
class TestDemoAccessCode:
    @pytest.mark.anyio
    async def test_code_gates_the_demo_sign_in(self, app):
        from app.pipeline import demo_control
        from app.pipeline.demo_data import demo_uuid
        await demo_control.load()
        uid = str(demo_uuid("user", "northstar", "admin"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            me = (await c.get("/api/v1/auth/me")).json()
            assert me["demo_code_required"] and not me["demo_unlocked"]
            assert (await c.get("/api/v1/auth/demo-users")).status_code == 401
            assert (await c.post("/api/v1/auth/demo-login", json={"user_id": uid})).status_code == 401
            assert (await c.post("/api/v1/auth/demo-unlock", json={"code": "wrong"})).status_code == 401
            r = await c.post("/api/v1/auth/demo-unlock", json={"code": "open-sesame-123"})
            assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
            assert (await c.get("/api/v1/auth/me")).json()["demo_unlocked"] is True
            assert len((await c.get("/api/v1/auth/demo-users")).json()) == 6
            assert (await c.post("/api/v1/auth/demo-login", json={"user_id": uid})).status_code == 200

    @pytest.mark.anyio
    async def test_hosted_demo_refuses_default_secret_key(self, app, monkeypatch):
        from app.core import auth
        monkeypatch.setenv("OXYGEN_SERVERLESS", "1")
        monkeypatch.setattr(auth, "secret_is_default", lambda: True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/auth/demo-unlock", json={"code": "open-sesame-123"})
            assert r.status_code == 503 and "SECRET_KEY" in r.text       # forgeable sessions are refused
