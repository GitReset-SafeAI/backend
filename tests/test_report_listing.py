"""Report listing requires verified ownership and bounded pagination."""

import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_report_repository, require_verified_reporter_id
from app.main import create_app
from app.repositories.reports import ReportPersistenceError, ReportRepository

OWNER = UUID("00000000-0000-0000-0000-000000000001")


def test_repository_filters_by_owner_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_id"] == f"eq.{OWNER}"
        assert request.url.params["limit"] == "10"
        assert request.url.params["offset"] == "20"
        assert request.url.params["order"] == "submitted_at.desc,id.desc"
        return httpx.Response(200, json=[])

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://example.test/rest/v1/", transport=httpx.MockTransport(handler)
        ) as client:
            assert await ReportRepository(client).list_owned(OWNER, 10, 20) == []

    asyncio.run(run())


def test_listing_requires_authentication() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/reports").status_code == 401


@pytest.mark.parametrize("failed", [False, True])
def test_listing_endpoint(failed: bool) -> None:
    class Repository:
        async def list_owned(self, owner: UUID, limit: int, offset: int) -> list:
            assert owner == OWNER
            assert (limit, offset) == (10, 20)
            if failed:
                raise ReportPersistenceError
            return []

    app = create_app()
    app.dependency_overrides[require_verified_reporter_id] = lambda: OWNER
    app.dependency_overrides[get_report_repository] = Repository
    with TestClient(app) as client:
        response = client.get("/api/v1/reports?limit=10&offset=20")
    assert response.status_code == (503 if failed else 200)
    if not failed:
        assert response.json() == []
