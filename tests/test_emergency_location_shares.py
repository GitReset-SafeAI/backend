"""Expiring anonymous read links and authenticated SOS location ownership."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.routes.emergency_location_shares import LocationShareStore, get_location_store
from app.core.security import AuthenticatedUser
from app.main import create_app

OWNER = UUID("11111111-1111-4111-8111-111111111111")
OTHER = UUID("22222222-2222-4222-8222-222222222222")
PATH = "/api/v1/emergency-location-shares"
FIX = {"position": {"latitude": 6.92, "longitude": 79.86}, "accuracy_m": 12}


@pytest.fixture
def tracking():
    database = LocationShareStore()
    clock = [datetime(2026, 11, 4, tzinfo=UTC)]
    database.now = lambda: clock[0]
    app = create_app()
    app.dependency_overrides[get_location_store] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(OWNER)
    with TestClient(app) as client:
        yield client, database, clock


def test_share_has_initial_location_and_can_be_read_without_login(tracking):
    api, _, _ = tracking
    response = api.post(PATH, json=FIX)
    assert response.status_code == 201
    share = response.json()
    assert len(share["share_id"]) == 43
    assert share["position"] == FIX["position"]
    assert set(share) == {"share_id", "position", "accuracy_m", "updated_at", "expires_at"}
    assert response.headers["cache-control"] == "no-store"
    del api.app.dependency_overrides[get_current_user]
    public = api.get(f"{PATH}/{share['share_id']}")
    assert public.status_code == 200
    assert public.json() == share
    assert public.headers["referrer-policy"] == "no-referrer"
    assert api.patch(f"{PATH}/{share['share_id']}", json=FIX).status_code == 401


def test_updates_are_live_and_repeated_start_reuses_active_share(tracking):
    api, database, clock = tracking
    first = api.post(PATH, json=FIX).json()
    clock[0] += timedelta(seconds=10)
    moved = {"position": {"latitude": 6.93, "longitude": 79.87}, "accuracy_m": 8}
    assert api.patch(f"{PATH}/{first['share_id']}", json=moved).status_code == 200
    view = api.get(f"{PATH}/{first['share_id']}").json()
    assert view["position"] == moved["position"]
    assert view["updated_at"] != first["updated_at"]
    repeated = api.post(PATH, json=moved).json()
    assert repeated["share_id"] == first["share_id"]
    assert len(database.rows) == 1


def test_another_user_cannot_modify_or_stop_a_share(tracking):
    api, _, _ = tracking
    created = api.post(PATH, json=FIX).json()
    api.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(OTHER)
    path = f"{PATH}/{created['share_id']}"
    assert api.patch(path, json=FIX).status_code == 404
    assert api.delete(path).status_code == 404
    assert api.get(path).status_code == 200


def test_expired_or_stopped_shares_are_unavailable(tracking):
    api, database, clock = tracking
    created = api.post(PATH, json=FIX).json()
    path = f"{PATH}/{created['share_id']}"
    clock[0] += timedelta(minutes=30)
    assert api.get(path).status_code == 404
    assert database.rows == {}
    created = api.post(PATH, json=FIX).json()
    path = f"{PATH}/{created['share_id']}"
    assert api.delete(path).status_code == 204
    assert api.get(path).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"owner": str(OTHER)},
        {"accuracy_m": -1},
        {"accuracy_m": "NaN"},
        {"position": {"latitude": 91, "longitude": 79}},
    ],
)
def test_invalid_location_is_redacted_and_not_stored(tracking, change):
    api, database, _ = tracking
    response = api.post(PATH, json={**FIX, **change})
    assert response.status_code == 422
    assert all("input" not in detail for detail in response.json()["detail"])
    assert database.rows == {}
