"""Orchestrates eligible-incident reads, clustering and route comparison."""

from datetime import UTC, datetime

from app.modules.safety_map.hotspots import Cluster, find_hotspots, overall_evidence
from app.modules.safety_map.repository import MapDataError, build_repository
from app.modules.safety_map.routing import (
    avoidance_waypoints,
    build_fallback_routes,
    compare_routes,
    fetch_osrm_routes,
    fetch_route_via,
    score_route,
)
from app.modules.safety_map.schemas import (
    MIN_REPORTS_PER_HOTSPOT,
    Coordinate,
    Hotspot,
    HotspotsResponse,
    Route,
    RouteComparisonResponse,
    TimePeriodFilter,
)

DEFAULT_WINDOW_DAYS = 30


def _path_length(geometry: list[tuple[float, float]]) -> float:
    """Rough ordering only, so a cheap sum of coordinate steps is enough."""
    return sum(
        abs(geometry[index][0] - geometry[index - 1][0])
        + abs(geometry[index][1] - geometry[index - 1][1])
        for index in range(1, len(geometry))
    )


class SafetyMapService:
    def __init__(self, url: str = "", service_key: str = "") -> None:
        self._repository = build_repository(url, service_key)

    @property
    def uses_synthetic_data(self) -> bool:
        return self._repository.uses_synthetic_data

    async def _clusters(
        self,
        south_west: tuple[float, float],
        north_east: tuple[float, float],
        time_period: TimePeriodFilter,
        window_days: int,
    ) -> tuple[list[Cluster], int]:
        incidents = await self._repository.fetch_eligible(south_west, north_east, window_days)
        if time_period != "all":
            # not_sure reports are kept under every filter: an unknown time is
            # not evidence that the incident happened at another time.
            incidents = [
                incident
                for incident in incidents
                if incident.reported_time in (time_period, "not_sure")
            ]
        return find_hotspots(incidents)

    async def hotspots(
        self,
        south_west: tuple[float, float],
        north_east: tuple[float, float],
        time_period: TimePeriodFilter = "all",
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> HotspotsResponse:
        clusters, suppressed = await self._clusters(
            south_west, north_east, time_period, window_days
        )
        return HotspotsResponse(
            generated_at=datetime.now(UTC),
            window_days=window_days,
            min_reports_per_hotspot=MIN_REPORTS_PER_HOTSPOT,
            suppressed_area_count=suppressed,
            evidence_level=overall_evidence(clusters),
            hotspots=[
                Hotspot(
                    id=cluster.id,
                    center=Coordinate(latitude=cluster.latitude, longitude=cluster.longitude),
                    radius_m=cluster.radius_m,
                    report_count=cluster.report_count,
                    intensity=cluster.intensity,
                    top_incident_categories=cluster.incident_categories,
                    top_safety_concerns=cluster.safety_concerns,
                    peak_time_period=cluster.peak_time_period,
                    most_recent_report_days_ago=cluster.most_recent_report_days_ago,
                    mean_confidence=cluster.mean_confidence,
                    evidence_level=cluster.evidence_level,
                )
                for cluster in clusters
            ],
        )

    async def compare(
        self,
        origin: Coordinate,
        destination: Coordinate,
        time_period: TimePeriodFilter = "all",
        travel_mode: str = "walking",
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> RouteComparisonResponse:
        start = (origin.latitude, origin.longitude)
        end = (destination.latitude, destination.longitude)

        # Look for areas across the whole journey, with room either side for
        # routes that detour.
        margin = 0.02
        south_west = (min(start[0], end[0]) - margin, min(start[1], end[1]) - margin)
        north_east = (max(start[0], end[0]) + margin, max(start[1], end[1]) + margin)
        try:
            clusters, _ = await self._clusters(south_west, north_east, time_period, window_days)
        except MapDataError:
            clusters = []

        candidates = await fetch_osrm_routes(start, end)
        street_network = bool(candidates)
        if not candidates:
            candidates = build_fallback_routes(start, end)

        # If every candidate runs through a reported area, ask the street
        # network for one that goes round them. It may be much longer; that is
        # the traveller's call, so it is offered rather than filtered out.
        if street_network and all(
            score_route(geometry, clusters)[1] for geometry, _ in candidates
        ):
            quickest = min(candidates, key=lambda candidate: _path_length(candidate[0]))
            waypoints = avoidance_waypoints(quickest[0], clusters)
            detour = await fetch_route_via(start, waypoints, end)
            if detour is not None and not score_route(detour[0], clusters)[1]:
                candidates = [*candidates, detour]

        scored, recommended, lowest_exposure, clear = compare_routes(
            candidates, clusters, travel_mode
        )
        return RouteComparisonResponse(
            routes=[
                Route(
                    id=route.id,
                    role=route.role,
                    duration_s=route.duration_s,
                    distance_m=route.distance_m,
                    geometry=[
                        Coordinate(latitude=point[0], longitude=point[1])
                        for point in route.geometry
                    ],
                    exposure_score=route.exposure_score,
                    hotspots_passed=route.hotspots_passed,
                    incident_categories=route.incident_categories,
                    safety_concerns=route.safety_concerns,
                    extra_duration_s=route.extra_duration_s,
                )
                for route in scored[:4]
            ],
            recommended_route_id=recommended,
            lowest_exposure_route_id=lowest_exposure,
            clear_route_id=clear,
            evidence_level=overall_evidence(clusters),
            travel_mode=travel_mode,
            uses_street_network=street_network,
        )
