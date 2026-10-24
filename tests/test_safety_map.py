"""Safety-map behaviour, including the rules that protect reporters."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.safety_map.hotspots import find_hotspots
from app.modules.safety_map.repository import EligibleIncident
from app.modules.safety_map.routing import build_fallback_routes, compare_routes, score_route
from app.modules.safety_map.schemas import MIN_HOTSPOT_RADIUS_M, MIN_REPORTS_PER_HOTSPOT

COLOMBO_BBOX = "79.80,6.80,79.95,6.96"


def incident(latitude, longitude, **overrides) -> EligibleIncident:
    defaults = dict(
        incident_category="UNWANTED_FOLLOWING",
        safety_concern_category=None,
        street_lights="not_sure",
        reported_time="night",
        severity=5,
        confidence=0.8,
        age_days=1,
    )
    return EligibleIncident(latitude=latitude, longitude=longitude, **(defaults | overrides))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_hotspots_endpoint_returns_aggregated_areas(client):
    response = client.get(f"/api/v1/safety-map/hotspots?bbox={COLOMBO_BBOX}")

    assert response.status_code == 200
    body = response.json()
    assert body["hotspots"], "the synthetic dataset should produce areas"
    for hotspot in body["hotspots"]:
        assert hotspot["report_count"] >= MIN_REPORTS_PER_HOTSPOT
        assert hotspot["radius_m"] >= MIN_HOTSPOT_RADIUS_M
        # No report content may leave the backend.
        assert set(hotspot) == {
            "id",
            "center",
            "radius_m",
            "report_count",
            "intensity",
            "top_incident_categories",
            "top_safety_concerns",
            "peak_time_period",
            "most_recent_report_days_ago",
            "mean_confidence",
            "evidence_level",
        }


def test_empty_area_reports_insufficient_evidence_not_safety(client):
    response = client.get("/api/v1/safety-map/hotspots?bbox=80.50,8.00,80.60,8.10")

    body = response.json()
    assert body["hotspots"] == []
    assert body["evidence_level"] == "insufficient"


def test_oversized_bbox_is_rejected(client):
    assert client.get("/api/v1/safety-map/hotspots?bbox=78,5,82,10").status_code == 422


def test_lone_reports_never_become_a_hotspot():
    scattered = [incident(6.90 + index * 0.02, 79.86 + index * 0.02) for index in range(4)]

    clusters, _ = find_hotspots(scattered)

    assert clusters == []


def test_published_centre_is_rounded_away_from_the_reports():
    members = [incident(6.890123 + index * 0.0001, 79.856789) for index in range(4)]

    clusters, _ = find_hotspots(members)

    assert len(clusters) == 1
    # Three decimals is roughly 110 m, so the exact points cannot be recovered.
    assert clusters[0].latitude == round(clusters[0].latitude, 3)
    assert clusters[0].radius_m >= MIN_HOTSPOT_RADIUS_M


def test_poor_lighting_is_reported_only_when_most_reports_mention_it():
    dark = [incident(6.89, 79.856, street_lights="poorly_lit_or_dark") for _ in range(4)]
    unsure = [incident(6.89, 79.856, street_lights="not_sure") for _ in range(4)]

    dark_clusters, _ = find_hotspots(dark)
    unsure_clusters, _ = find_hotspots(unsure)

    assert "POOR_STREET_LIGHTING" in dark_clusters[0].safety_concerns
    assert "POOR_STREET_LIGHTING" not in unsure_clusters[0].safety_concerns


def test_low_confidence_reports_count_for_less():
    confident = [incident(6.89, 79.856, confidence=0.95) for _ in range(4)]
    unsure = [incident(6.89, 79.856, confidence=0.2) for _ in range(4)]

    route = [(6.889, 79.855), (6.891, 79.857)]
    confident_score, _ = score_route(route, find_hotspots(confident)[0])
    unsure_score, _ = score_route(route, find_hotspots(unsure)[0])

    assert unsure_score < confident_score


def test_a_route_is_recommended_only_when_it_is_a_real_improvement():
    origin, destination = (6.8872, 79.8597), (6.8951, 79.8541)
    clusters, _ = find_hotspots([incident(6.8905, 79.8565) for _ in range(6)])

    routes, recommended = compare_routes(build_fallback_routes(origin, destination), clusters)

    fastest = next(route for route in routes if route.role == "fastest")
    assert fastest.extra_duration_s == 0
    if recommended is not None:
        best = next(route for route in routes if route.id == recommended)
        assert best.exposure_score < fastest.exposure_score
        # A recommendation is never sold as the quicker option.
        assert best.duration_s >= fastest.duration_s


def test_route_comparison_never_returns_a_bare_recommendation(client):
    response = client.post(
        "/api/v1/safety-map/routes/compare",
        json={
            "origin": {"latitude": 6.8872, "longitude": 79.8597},
            "destination": {"latitude": 6.8951, "longitude": 79.8541},
            "time_period": "night",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["routes"], "every comparison explains at least one route"
    for route in body["routes"]:
        assert len(route["geometry"]) >= 2
        assert 0 <= route["exposure_score"] <= 100
    # The caller is always told whether the geometry is real street routing.
    assert isinstance(body["uses_street_network"], bool)


def test_place_search_rejects_a_too_short_query(client):
    assert client.get("/api/v1/safety-map/places?q=a").status_code == 422


def test_place_search_returns_public_place_names_only(client, monkeypatch):
    async def fake_search(query: str, limit: int):
        return [
            {
                "id": "N1",
                "name": "Nugegoda",
                "context": "Colombo District",
                "coordinate": {"latitude": 6.87, "longitude": 79.888},
            }
        ]

    monkeypatch.setattr("app.modules.safety_map.router.search_places", fake_search)
    response = client.get("/api/v1/safety-map/places?q=nugegoda")

    assert response.status_code == 200
    for place in response.json():
        # A place is reference data; nothing about a report may ride along.
        assert set(place) == {"id", "name", "context", "coordinate"}
