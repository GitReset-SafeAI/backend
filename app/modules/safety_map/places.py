"""Place lookup for the journey planner.

Geocoding is proxied here rather than called from the app, so the provider and
any future key stay on the server and one cache can serve every user. Photon is
an OpenStreetMap geocoder, which keeps place names and the walking routes on
the same underlying data.

A place query carries no report content. Results are public place names only.
"""

import asyncio

import httpx

PHOTON_URL = "https://photon.komoot.io/api/"
PHOTON_TIMEOUT_SECONDS = 5.0

# Sri Lanka, so a short query does not return results from across the world.
COUNTRY_BBOX = "79.5,5.8,82.0,9.9"

MAX_RESULTS = 8

# OpenStreetMap services require a request to identify the application, and
# reject a default client user agent outright.
USER_AGENT = "SafeRouteAI/0.1 (student project; safety map journey planner)"


class PlaceLookupError(RuntimeError):
    """Places could not be searched."""


def _context(properties: dict) -> str:
    """The area line under a place name, closest level first, without repeats."""
    parts = [
        properties.get("district"),
        properties.get("city"),
        properties.get("county"),
        properties.get("state"),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part != properties.get("name") and part not in seen:
            seen.append(part)
    return ", ".join(seen[:2])


async def search_places(query: str, limit: int) -> list[dict]:
    query = query.strip()
    if len(query) < 2:
        return []

    try:
        async with httpx.AsyncClient(
            timeout=PHOTON_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await client.get(
                PHOTON_URL,
                params={
                    "q": query,
                    "limit": min(limit, MAX_RESULTS),
                    "lang": "en",
                    "bbox": COUNTRY_BBOX,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise PlaceLookupError("Place search is unavailable") from exc

    places = []
    seen: set[str] = set()
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates")
        name = properties.get("name")
        if not name or not isinstance(coordinates, list) or len(coordinates) != 2:
            continue

        latitude, longitude = float(coordinates[1]), float(coordinates[0])
        # A single OSM feature can come back more than once, so the OSM id
        # alone is not unique. The position makes each suggestion distinct,
        # and an exact repeat is dropped rather than listed twice.
        identifier = (
            f"{properties.get('osm_type', 'x')}{properties.get('osm_id', 'x')}"
            f"-{latitude:.5f},{longitude:.5f}"
        )
        if identifier in seen:
            continue
        seen.add(identifier)

        places.append(
            {
                "id": identifier,
                "name": name,
                "context": _context(properties),
                # Photon returns [longitude, latitude]; the API boundary is the
                # other way round.
                "coordinate": {"latitude": latitude, "longitude": longitude},
            }
        )
    return places


REVERSE_URL = "https://photon.komoot.io/reverse"
REVERSE_TIMEOUT_SECONDS = 4.0
# Several areas are named per map request. They run together but never all at
# once: this is a shared public geocoder, not ours to flood.
REVERSE_CONCURRENCY = 4
# ~110 m, just finer than the 150 m aggregation floor, so one cached label is
# never reused across two genuinely different areas.
CACHE_PRECISION = 3
CACHE_LIMIT = 512

_area_cache: dict[tuple[float, float], str | None] = {}


def _area_label(properties: dict) -> str | None:
    """A district- or city-level label, and nothing finer.

    `street`, `housenumber` and the feature's own `name` are deliberately
    ignored. An area is published at a 150 m floor precisely so it cannot point
    at the place an incident was reported; naming it after a street or a
    building would undo that in one line of JSON.
    """
    parts = [
        properties.get("district"),
        properties.get("city"),
        properties.get("county"),
        properties.get("state"),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen[:2]) or None


async def _reverse_one(client: httpx.AsyncClient, latitude: float, longitude: float) -> str | None:
    key = (round(latitude, CACHE_PRECISION), round(longitude, CACHE_PRECISION))
    if key in _area_cache:
        return _area_cache[key]
    try:
        response = await client.get(
            REVERSE_URL, params={"lat": latitude, "lon": longitude, "lang": "en"}
        )
        response.raise_for_status()
        features = response.json().get("features") or []
        label = _area_label(features[0].get("properties") or {}) if features else None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        # A map that cannot name its areas is still a usable map, so a failed
        # lookup is never allowed to fail the request. It is also not cached:
        # the next request should be free to try again.
        return None
    if len(_area_cache) < CACHE_LIMIT:
        _area_cache[key] = label
    return label


async def area_names(coordinates: list[tuple[float, float]]) -> list[str | None]:
    """Coarse area labels for hotspot centres, in the order given.

    Best effort throughout: every entry is independently allowed to be None,
    and the whole batch degrades to None rather than failing the map.
    """
    if not coordinates:
        return []
    semaphore = asyncio.Semaphore(REVERSE_CONCURRENCY)

    async def run(client: httpx.AsyncClient, point: tuple[float, float]) -> str | None:
        async with semaphore:
            return await _reverse_one(client, point[0], point[1])

    try:
        async with httpx.AsyncClient(
            timeout=REVERSE_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        ) as client:
            return list(await asyncio.gather(*(run(client, point) for point in coordinates)))
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        return [None] * len(coordinates)
