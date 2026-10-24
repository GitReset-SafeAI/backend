"""Saved analysis listing contract and pagination validation."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.incident_ai.repository import AnalysisStorageError
from app.modules.incident_ai.router import get_analysis_repository


@pytest.mark.parametrize("rows", [[], [{"id": "stored-id", "extracted_details": {}}]])
def test_listing_returns_stored_rows(rows: list) -> None:
    class Repository:
        async def list_analyses(self, limit: int, offset: int) -> list:
            assert (limit, offset) == (10, 20)
            return rows

    app = create_app()
    app.dependency_overrides[get_analysis_repository] = Repository
    with TestClient(app) as client:
        response = client.get("/api/v1/incident-ai/analyses?limit=10&offset=20")
    assert response.status_code == 200
    assert response.json() == rows


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1"])
def test_invalid_pagination(query: str) -> None:
    with TestClient(create_app()) as client:
        assert client.get(f"/api/v1/incident-ai/analyses?{query}").status_code == 422


def test_listing_database_failure() -> None:
    class Repository:
        async def list_analyses(self, limit: int, offset: int) -> list:
            raise AnalysisStorageError("private provider error")

    app = create_app()
    app.dependency_overrides[get_analysis_repository] = Repository
    with TestClient(app) as client:
        response = client.get("/api/v1/incident-ai/analyses")
    assert response.status_code == 503
    assert response.json() == {"detail": "Could not retrieve incident analyses"}
