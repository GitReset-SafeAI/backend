"""Safety-map behaviour, including the rules that protect reporters."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.safety_map.hotspots import find_hotspots
from app.modules.safety_map.repository import EligibleIncident
from app.modules.safety_map.router import get_service
from app.modules.safety_map.routing import (
    _haversine_m,
    avoidance_waypoints,
    build_fallback_routes,
    clusters_containing,
    compare_routes,
    score_route,
)
from app.modules.safety_map.schemas import MIN_HOTSPOT_RADIUS_M, MIN_REPORTS_PER_HOTSPOT
from app.modules.safety_map.service import SafetyMapService

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
    application = create_app()
    application.dependency_overrides[get_service] = lambda: SafetyMapService("", "")
    return TestClient(application)


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

    routes, recommended, _, _ = compare_routes(build_fallback_routes(origin, destination), clusters)

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


def test_no_recommendation_when_the_fastest_route_passes_nothing():
    origin, destination = (6.8872, 79.8597), (6.8951, 79.8541)
    # A reported area far from every candidate: all routes score near zero.
    clusters, _ = find_hotspots([incident(6.95, 79.95) for _ in range(5)])

    routes, recommended, _, _ = compare_routes(build_fallback_routes(origin, destination), clusters)

    assert all(route.hotspots_passed == 0 for route in routes)
    # Nothing to avoid, so no detour may be recommended.
    assert recommended is None


def test_no_recommendation_for_a_negligible_exposure_difference():
    origin, destination = (6.8872, 79.8597), (6.8951, 79.8541)
    clusters, _ = find_hotspots([incident(6.9, 79.88) for _ in range(5)])

    routes, recommended, _, _ = compare_routes(build_fallback_routes(origin, destination), clusters)

    fastest = next(route for route in routes if route.role == "fastest")
    if fastest.exposure_score < 5.0:
        assert recommended is None


@pytest.mark.anyio
async def test_place_suggestions_have_unique_ids(monkeypatch):
    """Photon can repeat an OSM id, which breaks list rendering in the app."""
    from app.modules.safety_map import places as places_module

    repeated = {
        "features": [
            {
                "properties": {"osm_type": "W", "osm_id": 27712654, "name": "Galle Road"},
                "geometry": {"coordinates": [79.85, 6.89]},
            },
            {
                "properties": {"osm_type": "W", "osm_id": 27712654, "name": "Galle Road"},
                "geometry": {"coordinates": [79.86, 6.90]},
            },
            {
                "properties": {"osm_type": "W", "osm_id": 27712654, "name": "Galle Road"},
                "geometry": {"coordinates": [79.85, 6.89]},
            },
        ]
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return repeated

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(places_module.httpx, "AsyncClient", FakeClient)
    results = await places_module.search_places("galle road", 6)

    identifiers = [place["id"] for place in results]
    assert len(identifiers) == len(set(identifiers))
    # The exact repeat is dropped; the two distinct positions are both kept.
    assert len(results) == 2


def test_the_least_exposed_route_is_named_even_without_a_recommendation():
    origin, destination = (6.8872, 79.8597), (6.8951, 79.8541)
    clusters, _ = find_hotspots([incident(6.8905, 79.8565) for _ in range(6)])

    routes, recommended, lowest, _ = compare_routes(
        build_fallback_routes(origin, destination), clusters
    )

    assert lowest is not None
    least = next(route for route in routes if route.id == lowest)
    # Whatever the recommendation decides, the named route really is the least
    # exposed of those compared.
    assert least.exposure_score == min(route.exposure_score for route in routes)
    if recommended is not None:
        assert recommended == lowest


def test_driving_is_quicker_than_walking_over_the_same_roads():
    # A service duration is supplied, as OSRM would.
    candidates = [([(6.8872, 79.8597), (6.8910, 79.8570), (6.8951, 79.8541)], 120.0)]

    walking, _, _, _ = compare_routes(candidates, [], "walking")
    driving, _, _, _ = compare_routes(candidates, [], "driving")

    # Walking time is derived from the distance; driving trusts the service.
    assert driving[0].duration_s == 120
    assert walking[0].duration_s > driving[0].duration_s
    # The path is identical: only the timing differs.
    assert walking[0].geometry == driving[0].geometry


def test_route_comparison_states_its_travel_mode(client):
    response = client.post(
        "/api/v1/safety-map/routes/compare",
        json={
            "origin": {"latitude": 6.8872, "longitude": 79.8597},
            "destination": {"latitude": 6.8951, "longitude": 79.8541},
            "travel_mode": "driving",
        },
    )

    body = response.json()
    assert body["travel_mode"] == "driving"
    # The caller is always told a pedestrian profile was not used.
    assert body["pedestrian_profile_available"] is False


def test_a_clear_route_is_offered_however_long_it_is():
    clusters, _ = find_hotspots([incident(6.8905, 79.8565) for _ in range(6)])

    direct = [(6.8872, 79.8597), (6.8905, 79.8565), (6.8951, 79.8541)]
    # Far longer, but it enters no reported area.
    long_way_round = [
        (6.8872, 79.8597),
        (6.8870, 79.8700),
        (6.8960, 79.8700),
        (6.8951, 79.8541),
    ]

    routes, _, _, clear = compare_routes([(direct, 0.0), (long_way_round, 0.0)], clusters)

    assert clear is not None
    offered = next(route for route in routes if route.id == clear)
    assert offered.hotspots_passed == 0
    assert offered.role == "avoids-all"
    # The point of the feature: it is offered even though it takes much longer.
    fastest = next(route for route in routes if route.role == "fastest")
    assert offered.duration_s > fastest.duration_s


def test_avoidance_waypoints_sit_clear_of_the_area_they_route_around():
    clusters, _ = find_hotspots([incident(6.8905, 79.8565) for _ in range(6)])
    through = [(6.8872, 79.8597), (6.8905, 79.8565), (6.8951, 79.8541)]

    waypoints = avoidance_waypoints(through, clusters)

    assert waypoints
    area = clusters[0]
    for point in waypoints:
        distance = _haversine_m(point, (area.latitude, area.longitude))
        # Outside the area, not merely on its edge.
        assert distance > area.radius_m


def test_an_area_around_the_destination_is_not_counted_as_avoidable():
    clusters, _ = find_hotspots([incident(6.8951, 79.8541) for _ in range(6)])
    destination = (6.8951, 79.8541)

    containing = clusters_containing(destination, clusters)

    # You cannot route away from where you are going, so this area must not
    # make every route look unavoidably exposed.
    assert [cluster.id for cluster in containing] == [clusters[0].id]


def test_avoidance_steps_off_the_road_rather_than_along_it():
    clusters, _ = find_hotspots([incident(6.8905, 79.8565) for _ in range(6)])
    # A path running due north straight through the area.
    northward = [(6.8860, 79.8565), (6.8905, 79.8565), (6.8950, 79.8565)]

    east = avoidance_waypoints(northward, clusters, side=1)
    west = avoidance_waypoints(northward, clusters, side=-1)

    # Offsetting along the road would keep the same longitude and simply snap
    # back onto it, so the step must be sideways and opposite on each side.
    assert east and west
    assert east[0][1] > 79.8565 > west[0][1]
    assert abs(east[0][0] - 6.8905) < abs(east[0][1] - 79.8565)
