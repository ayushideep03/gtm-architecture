"""
Tests for application startup and the /health endpoint.
"""

import pytest


def test_app_imports_cleanly():
    """The FastAPI application should import without raising exceptions."""
    from app.main import app  # noqa: F401
    assert app is not None


def test_app_has_correct_title():
    from app.main import app
    assert "GTM" in app.title


def test_health_endpoint_returns_200(client):
    """GET /health → 200 with status: ok."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "environment" in body


def test_health_endpoint_returns_app_name(client):
    """GET /health should include the app name."""
    response = client.get("/health")
    body = response.json()
    assert "GTM" in body["app"]


def test_health_endpoint_has_request_id_header(client):
    """The logging middleware should add an X-Request-ID header."""
    response = client.get("/health")
    assert "x-request-id" in response.headers


def test_openapi_schema_loads(client):
    """The OpenAPI schema endpoint must be reachable."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema


def test_docs_endpoint_loads(client):
    """Swagger UI must be accessible."""
    response = client.get("/docs")
    assert response.status_code == 200
