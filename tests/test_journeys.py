"""Sharing a journey. The rules here protect a live position, so they are tested
as decisions rather than as mechanics."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.journeys.schemas import MAX_SHARE_MINUTES
from app.modules.journeys.store import store

JOURNEY = {
    "origin": {"latitude": 6.9335, "longitude": 79.8507},
    "destination": {"latitude": 6.9271, "longitude": 79.8425},
    "origin_label": "Fort",
    "destination_label": "Galle Face",
    "geometry": [
        {"latitude": 6.9335, "longitude": 79.8507},
        {"latitude": 6.9271, "longitude": 79.8425},
    ],
    "duration_s": 1500,
}


@pytest.fixture
def client() -> TestClient:
    store._journeys.clear()
    return TestClient(create_app())


def start(client) -> tuple[str, str]:
    response = client.post("/api/v1/journeys", json=JOURNEY)
    assert response.status_code == 201
    body = response.json()
    return body["share_id"], body["owner_key"]


def test_a_share_link_never_carries_the_key_that_controls_the_journey(client):
    share_id, owner_key = start(client)
    response = client.get(f"/api/v1/journeys/{share_id}")

    assert "owner_key" not in response.json()
    # Not anywhere in the payload, under any name.
    assert owner_key not in response.text


def test_only_the_traveller_can_move_or_end_the_journey(client):
    share_id, _ = start(client)
    position = {"position": {"latitude": 6.931, "longitude": 79.848}}

    # Holding the link is not holding the key.
    assert client.patch(f"/api/v1/journeys/{share_id}", json=position).status_code == 401
    assert (
        client.patch(
            f"/api/v1/journeys/{share_id}", json=position, headers={"X-Journey-Key": "guess"}
        ).status_code
        == 404
    )
    # A wrong key answers exactly as a missing journey does, so trying keys
    # never confirms that a journey is there.
    assert (
        client.delete(
            f"/api/v1/journeys/{share_id}", headers={"X-Journey-Key": "guess"}
        ).status_code
        == 404
    )


def test_ending_a_journey_removes_it_rather_than_hiding_it(client):
    share_id, owner_key = start(client)
    assert (
        client.delete(
            f"/api/v1/journeys/{share_id}", headers={"X-Journey-Key": owner_key}
        ).status_code
        == 204
    )
    assert client.get(f"/api/v1/journeys/{share_id}").status_code == 404
    assert share_id not in store._journeys


def test_arrival_is_noticed_so_following_stops_at_the_destination(client):
    share_id, owner_key = start(client)
    headers = {"X-Journey-Key": owner_key}

    moving = client.patch(
        f"/api/v1/journeys/{share_id}",
        json={"position": {"latitude": 6.9310, "longitude": 79.8480}},
        headers=headers,
    ).json()
    assert moving["status"] == "active"

    arriving = client.patch(
        f"/api/v1/journeys/{share_id}",
        json={"position": JOURNEY["destination"]},
        headers=headers,
    ).json()
    assert arriving["status"] == "arrived"


def test_a_share_cannot_outlive_a_single_walk(client):
    response = client.post(
        "/api/v1/journeys", json={**JOURNEY, "share_minutes": MAX_SHARE_MINUTES + 1}
    )
    assert response.status_code == 422, "an unbounded share would be a tracking link"


def test_share_ids_are_unguessable_and_distinct(client):
    ids = {start(client)[0] for _ in range(20)}
    assert len(ids) == 20
    assert all(len(share_id) >= 16 for share_id in ids)


def test_a_label_cannot_be_used_to_carry_a_message(client):
    response = client.post("/api/v1/journeys", json={**JOURNEY, "destination_label": "x" * 200})
    assert response.status_code == 422


def test_a_journey_carries_no_report_or_account_data(client):
    share_id, _ = start(client)
    view = client.get(f"/api/v1/journeys/{share_id}").json()
    assert set(view) == {
        "share_id",
        "status",
        "origin",
        "destination",
        "origin_label",
        "destination_label",
        "geometry",
        "duration_s",
        "position",
        "accuracy_m",
        "started_at",
        "updated_at",
        "expires_at",
    }
