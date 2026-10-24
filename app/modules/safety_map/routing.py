"""Walking routes and exposure scoring.

Routes come from OSRM over the OpenStreetMap street network. The public demo
server rate-limits and is not guaranteed to be up, so a geometric fallback
keeps the feature demonstrable; a fallback route is marked as such and is never
presented as real walking directions.

Exposure is a relative indicator of how much of a route runs near areas with
reported incidents. It is not a safety score and never a guarantee.
"""

from dataclasses import dataclass, replace

import httpx
import numpy as np

from app.modules.safety_map.hotspots import EARTH_RADIUS_M, Cluster

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SECONDS = 6.0

# Identify the application to the public routing service, as its usage policy
# asks.
USER_AGENT = "SafeRouteAI/0.1 (student project; safety map journey planner)"

WALKING_SPEED_MPS = 1.35

# The public OSRM demo server serves only the car profile: foot, bike and
# driving all return identical geometry and timings. Driving therefore uses
# OSRM's own duration, which is accurate, while walking is derived from the
# distance at walking pace. The path itself follows the road network either
# way, which the app states rather than implying a pedestrian route.
TravelMode = str
DRIVING = "driving"
WALKING = "walking"

# Route points are compared against hotspots every 25 m.
SAMPLE_SPACING_M = 25.0

# Influence of an area decays smoothly rather than stopping at its edge.
EXPOSURE_SIGMA_M = 120.0

# A recommendation must be a real improvement, not a rounding difference.
MIN_EXPOSURE_REDUCTION = 0.25
MAX_EXTRA_SECONDS = 300
MAX_EXTRA_FRACTION = 0.3

# Exposure decays with distance, so a route that enters no reported area still
# scores slightly above zero simply by passing further from one. Below this the
# difference between two routes is noise, and spending extra minutes on it
# would be a recommendation with nothing behind it.
NEGLIGIBLE_EXPOSURE = 5.0
MIN_ABSOLUTE_EXPOSURE_DROP = 5.0


@dataclass(frozen=True, slots=True)
class ScoredRoute:
    id: str
    role: str
    duration_s: int
    distance_m: float
    geometry: list[tuple[float, float]]
    exposure_score: float
    hotspots_passed: int
    incident_categories: list[str]
    safety_concerns: list[str]
    extra_duration_s: int


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = np.radians([a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h)))


def _path_length_m(geometry: list[tuple[float, float]]) -> float:
    return sum(
        _haversine_m(geometry[index - 1], point)
        for index, point in enumerate(geometry)
        if index > 0
    )


def _resample(geometry: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Even samples along the path, so a long straight leg is not under-counted."""
    if len(geometry) < 2:
        return geometry
    samples = [geometry[0]]
    for index in range(1, len(geometry)):
        start, end = geometry[index - 1], geometry[index]
        segment = _haversine_m(start, end)
        steps = max(1, int(segment // SAMPLE_SPACING_M))
        for step in range(1, steps + 1):
            t = step / steps
            samples.append(
                (
                    start[0] + (end[0] - start[0]) * t,
                    start[1] + (end[1] - start[1]) * t,
                )
            )
    return samples


def score_route(
    geometry: list[tuple[float, float]], clusters: list[Cluster]
) -> tuple[float, list[Cluster]]:
    """Length-weighted exposure, so a longer route is not penalised for length."""
    samples = _resample(geometry)
    if not samples or not clusters:
        return 0.0, []

    passed: list[Cluster] = []
    total = 0.0
    for sample in samples:
        for cluster in clusters:
            distance = _haversine_m(sample, (cluster.latitude, cluster.longitude))
            if distance <= cluster.radius_m and cluster not in passed:
                passed.append(cluster)
            # Confidence weights the contribution: an area the AI understood
            # poorly moves a recommendation less than one it read clearly.
            total += (
                cluster.intensity
                * cluster.mean_confidence
                * float(np.exp(-(distance**2) / (2 * EXPOSURE_SIGMA_M**2)))
            )

    mean = total / len(samples)
    return round(min(100.0, mean * 100), 1), passed


async def _osrm_request(
    client: httpx.AsyncClient, points: list[tuple[float, float]], alternatives: bool
) -> list[tuple[list[tuple[float, float]], float]]:
    coordinates = ";".join(f"{point[1]},{point[0]}" for point in points)
    try:
        response = await client.get(
            f"{OSRM_BASE_URL}/route/v1/foot/{coordinates}",
            params={
                "alternatives": "3" if alternatives else "false",
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        # Routing being unavailable is not an error worth failing the map for.
        return []

    if payload.get("code") != "Ok":
        return []

    routes = []
    for route in payload.get("routes", []):
        # GeoJSON is [longitude, latitude]; the API boundary is the other way.
        geometry = [
            (float(point[1]), float(point[0]))
            for point in route.get("geometry", {}).get("coordinates", [])
            if isinstance(point, list) and len(point) == 2
        ]
        if len(geometry) >= 2:
            routes.append((geometry, float(route.get("duration") or 0.0)))
    return routes


def _detour_waypoint(
    origin: tuple[float, float], destination: tuple[float, float], bend: float
) -> tuple[float, float]:
    """A point to one side of the direct line, used to force a different street."""
    midpoint = ((origin[0] + destination[0]) / 2, (origin[1] + destination[1]) / 2)
    normal = (-(destination[1] - origin[1]), destination[0] - origin[0])
    return (midpoint[0] + normal[0] * bend, midpoint[1] + normal[1] * bend)


def _is_distinct(
    candidate: list[tuple[float, float]],
    existing: list[tuple[list[tuple[float, float]], float]],
) -> bool:
    """Keep a route only if it is a materially different path."""
    length = _path_length_m(candidate)
    for geometry, _ in existing:
        other = _path_length_m(geometry)
        if other and abs(length - other) / other < 0.05:
            return False
    return True


async def fetch_osrm_routes(
    origin: tuple[float, float], destination: tuple[float, float]
) -> list[tuple[list[tuple[float, float]], float]]:
    """Walking routes from OSRM, or an empty list if it cannot be reached.

    OSRM returns alternatives only when it finds them, which is uncommon on
    short walks. When it does not, routes are requested through a waypoint to
    one side so there are genuinely different streets to compare; those are
    still real street-network routes, not drawn lines.
    """
    async with httpx.AsyncClient(
        timeout=OSRM_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    ) as client:
        routes = await _osrm_request(client, [origin, destination], alternatives=True)
        if not routes:
            return []

        for bend in (0.25, -0.25, 0.45):
            if len(routes) >= 3:
                break
            waypoint = _detour_waypoint(origin, destination, bend)
            for candidate, duration in await _osrm_request(
                client, [origin, waypoint, destination], alternatives=False
            ):
                if _is_distinct(candidate, routes):
                    routes.append((candidate, duration))
    return routes


def build_fallback_routes(
    origin: tuple[float, float], destination: tuple[float, float]
) -> list[tuple[list[tuple[float, float]], float]]:
    """Bent lines between two points, used only when OSRM is unreachable.

    These ignore the street network and are never real walking directions.
    """
    normal = (-(destination[1] - origin[1]), destination[0] - origin[0])
    routes = []
    for bend in (0.0, 0.22, -0.3):
        points = []
        for step in range(7):
            t = step / 6
            offset = bend * 4 * t * (1 - t)
            points.append(
                (
                    origin[0] + (destination[0] - origin[0]) * t + normal[0] * offset,
                    origin[1] + (destination[1] - origin[1]) * t + normal[1] * offset,
                )
            )
        # No service duration is available for a drawn line.
        routes.append((points, 0.0))
    return routes


def _metres_to_degrees(latitude: float, north_m: float, east_m: float) -> tuple[float, float]:
    return north_m / 111_320.0, east_m / (111_320.0 * max(0.2, np.cos(np.radians(latitude))))


def avoidance_waypoints(
    geometry: list[tuple[float, float]],
    clusters: list[Cluster],
    max_points: int = 3,
) -> list[tuple[float, float]]:
    """Points to route through so a path clears every reported area it meets.

    For each area the path enters, a waypoint is placed on the far side of it,
    away from where the path currently runs. Routing through those points lets
    the street network find a way round rather than guessing one.
    """
    waypoints: list[tuple[float, float]] = []
    for cluster in clusters:
        centre = (cluster.latitude, cluster.longitude)
        nearest = min(geometry, key=lambda point: _haversine_m(point, centre))
        if _haversine_m(nearest, centre) > cluster.radius_m:
            continue

        north = nearest[0] - centre[0]
        east = nearest[1] - centre[1]
        norm = float(np.hypot(north, east))
        if norm == 0:
            # The path runs through the centre, so either side will do.
            north, east, norm = 1.0, 0.0, 1.0

        # Far enough out that the route leaves the area rather than skirting it.
        clearance_m = cluster.radius_m * 2.0 + 200.0
        d_lat, d_lon = _metres_to_degrees(
            centre[0], (north / norm) * clearance_m, (east / norm) * clearance_m
        )
        waypoints.append((centre[0] + d_lat, centre[1] + d_lon))

    # Keep them in the order the path meets them, so the detour stays sensible.
    waypoints.sort(
        key=lambda point: min(range(len(geometry)), key=lambda i: _haversine_m(geometry[i], point))
    )
    return waypoints[:max_points]


async def fetch_route_via(
    origin: tuple[float, float],
    waypoints: list[tuple[float, float]],
    destination: tuple[float, float],
) -> tuple[list[tuple[float, float]], float] | None:
    """One street route passing through the given waypoints, if one exists."""
    if not waypoints:
        return None
    async with httpx.AsyncClient(
        timeout=OSRM_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    ) as client:
        routes = await _osrm_request(client, [origin, *waypoints, destination], alternatives=False)
    return routes[0] if routes else None


def compare_routes(
    candidates: list[tuple[list[tuple[float, float]], float]],
    clusters: list[Cluster],
    travel_mode: TravelMode = WALKING,
) -> tuple[list[ScoredRoute], str | None, str | None, str | None]:
    """Score every candidate and name the useful ones.

    Three separate answers, because they answer different questions:
      * the recommendation, a real improvement worth a modest detour
      * the least-exposed route among those compared
      * a clear route that enters no reported area at all, reported however
        long it is, because "longer but clear" is a choice to offer rather
        than one to make on someone's behalf
    """
    scored: list[ScoredRoute] = []
    for index, (geometry, service_duration_s) in enumerate(candidates):
        exposure, passed = score_route(geometry, clusters)
        distance = _path_length_m(geometry)
        # Driving trusts the routing service's own timing; walking is derived,
        # because that service has no pedestrian profile to time against.
        duration_s = (
            int(service_duration_s)
            if travel_mode == DRIVING and service_duration_s > 0
            else int(distance / WALKING_SPEED_MPS)
        )
        scored.append(
            ScoredRoute(
                id=f"route-{index + 1}",
                role="fastest" if index == 0 else "alternative",
                duration_s=duration_s,
                distance_m=round(distance, 1),
                geometry=geometry,
                exposure_score=exposure,
                hotspots_passed=len(passed),
                incident_categories=sorted(
                    {category for cluster in passed for category in cluster.incident_categories}
                )[:3],
                safety_concerns=sorted(
                    {concern for cluster in passed for concern in cluster.safety_concerns}
                )[:3],
                extra_duration_s=0,
            )
        )

    if not scored:
        return [], None, None, None

    fastest = min(scored, key=lambda route: route.duration_s)
    scored = [
        replace(
            route,
            role="fastest" if route is fastest else route.role,
            extra_duration_s=max(0, route.duration_s - fastest.duration_s),
        )
        for route in scored
    ]

    # The least-exposed option, reported whether or not it is worth a detour.
    # Ties go to the quicker route.
    least_exposed = min(scored, key=lambda route: (route.exposure_score, route.duration_s))

    # A route that enters no reported area at all. Deliberately not subject to
    # the detour allowance: how much longer is worth it is the traveller's
    # call, not this function's.
    clear_routes = [route for route in scored if route.hotspots_passed == 0]
    clear = min(clear_routes, key=lambda route: route.duration_s) if clear_routes else None

    def mark(routes: list[ScoredRoute], route_id: str | None, role: str) -> list[ScoredRoute]:
        """Label one route's part in the comparison, wherever it was chosen."""
        if route_id is None:
            return routes
        return [replace(route, role=role) if route.id == route_id else route for route in routes]

    # Marked here rather than on one branch, so a clear route is labelled
    # whether or not a recommendation was also made.
    if clear is not None and clear.id != fastest.id:
        scored = mark(scored, clear.id, "avoids-all")

    # Nothing to avoid: the quickest way is also the one with no reported areas
    # on it, so there is no case for travelling further.
    if fastest.hotspots_passed == 0 or fastest.exposure_score < NEGLIGIBLE_EXPOSURE:
        # The quickest way is already clear, so there is nothing to offer past it.
        return scored, None, least_exposed.id, None

    allowance = max(MAX_EXTRA_SECONDS, int(fastest.duration_s * MAX_EXTRA_FRACTION))
    eligible = [
        route
        for route in scored
        if route.id != fastest.id
        and route.exposure_score <= fastest.exposure_score * (1 - MIN_EXPOSURE_REDUCTION)
        # Relative alone is not enough: 0.4 to 0.3 is a 25% drop and means
        # nothing. The gap must also be large enough to be real.
        and (fastest.exposure_score - route.exposure_score) >= MIN_ABSOLUTE_EXPOSURE_DROP
        and route.extra_duration_s <= allowance
    ]
    if not eligible:
        # Recommending nothing is a valid answer, not a failure: the caller
        # still learns which route is least exposed and can decide for itself.
        return scored, None, least_exposed.id, clear.id if clear else None

    best = min(eligible, key=lambda route: route.exposure_score)
    # A recommendation takes precedence over the clear label: it is the route
    # being put forward, and two badges on one row would compete.
    scored = mark(scored, best.id, "lower-exposure")
    return scored, best.id, least_exposed.id, clear.id if clear else None
