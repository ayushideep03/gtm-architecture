"""
Integration tests for database and Redis health-check probes.

These tests call the actual /health/db and /health/redis endpoints,
which attempt real connections. They will:
- PASS with "ok" status if services are running
- PASS with "error" status if services are NOT running (API still returns 503 gracefully)

In both cases the endpoint must return a parseable JSON body — the point
is to verify that the health-check machinery works, not that the services
are up.
"""

import pytest


class TestDatabaseHealthEndpoint:
    """Tests for GET /health/db."""

    def test_db_health_endpoint_reachable(self, client):
        """Endpoint must return 200 (ok) or 503 (error) — never 500."""
        response = client.get("/health/db")
        assert response.status_code in (200, 503)

    def test_db_health_returns_json_with_status(self, client):
        """Response body must include a 'status' key."""
        response = client.get("/health/db")
        body = response.json()
        assert "status" in body
        assert body["status"] in ("ok", "error")

    def test_db_health_returns_detail(self, client):
        """Response body must include a 'detail' key."""
        response = client.get("/health/db")
        body = response.json()
        assert "detail" in body


class TestRedisHealthEndpoint:
    """Tests for GET /health/redis."""

    def test_redis_health_endpoint_reachable(self, client):
        """Endpoint must return 200 (ok) or 503 (error) — never 500."""
        response = client.get("/health/redis")
        assert response.status_code in (200, 503)

    def test_redis_health_returns_json_with_status(self, client):
        """Response body must include a 'status' key."""
        response = client.get("/health/redis")
        body = response.json()
        assert "status" in body
        assert body["status"] in ("ok", "error")

    def test_redis_health_returns_detail(self, client):
        """Response body must include a 'detail' key."""
        response = client.get("/health/redis")
        body = response.json()
        assert "detail" in body


class TestHealthProbeModules:
    """Unit tests for the health probe functions (no HTTP, no TestClient)."""

    @pytest.mark.anyio
    async def test_check_db_health_returns_dict(self):
        """check_db_health() must always return a dict with 'status'."""
        from app.core.database import check_db_health
        result = await check_db_health()
        assert isinstance(result, dict)
        assert "status" in result
        assert result["status"] in ("ok", "error")

    @pytest.mark.anyio
    async def test_check_redis_health_returns_dict(self):
        """check_redis_health() must always return a dict with 'status'."""
        from app.core.redis import check_redis_health
        result = await check_redis_health()
        assert isinstance(result, dict)
        assert "status" in result
        assert result["status"] in ("ok", "error")
