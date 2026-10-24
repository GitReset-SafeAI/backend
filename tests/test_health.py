"""Standalone application health and configuration checks."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in (
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWKS_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "LLM_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_VERSION", "0.1.0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health_without_external_credentials() -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "saferoute-ai-backend",
        "version": "0.1.0",
    }


def test_development_openapi() -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "SafeRoute AI Backend"
    assert "/api/v1/health" in schema["paths"]


def test_production_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_VERSION", "0.2.0")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").json()["version"] == "0.2.0"
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
