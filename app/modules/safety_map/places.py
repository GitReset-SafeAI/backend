"""Place lookup for the journey planner.

Geocoding is proxied here rather than called from the app, so the provider and
any future key stay on the server and one cache can serve every user.

Nominatim is OpenStreetMap's own geocoder: free, keyless, and the same data
underneath the walking routes. Its usage policy caps shared use at roughly one
request per second and asks that heavy use be cached; `_throttle` enforces the
first across the whole process, and area names are cached in memory. A
production deployment with real traffic should move to a paid provider
(Mapbox, LocationIQ) or a self-hosted Nominatim instance rather than lean on
the public one.

A place query carries no report content. Results are public place names only.
"""

import asyncio
import time

import httpx

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_TIMEOUT_SECONDS = 6.0

# Nominatim's own usage policy: at most one request per second, shared across
# every caller of this process. A lock plus a remembered timestamp enforces it
# regardless of how many people are searching at once.
MIN_REQUEST_INTERVAL_SECONDS = 1.05
_throttle_lock = asyncio.Lock()
_last_request_at = 0.0

# Restricts results to Sri Lanka, so a short query does not return results
# from across the world. An island nation, so a country filter is exact rather
# than an approximation the way a bounding box would be.
COUNTRY_CODES = "lk"

MAX_RESULTS = 8

# OpenStreetMap services require a request to identify the application, and
# reject a default client user agent outright.
USER_AGENT = "SafeRouteAI/0.1 (student project; safety map journey planner)"


class PlaceLookupError(RuntimeError):
    """Places could not be searched."""


async def _throttled_get(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """One Nominatim request, never closer than MIN_REQUEST_INTERVAL_SECONDS
    behind the last one this process made."""
    global _last_request_at
    async with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        response = await client.get(url, params=params)
        _last_request_at = time.monotonic()
    return response


def _context(address: dict) -> str:
    """The area line under a place name, closest level first, without repeats."""
    parts = [
        address.get("suburb") or address.get("neighbourhood"),
        address.get("city") or address.get("town") or address.get("village"),
        address.get("county") or address.get("state_district"),
        address.get("state"),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen[:2])


def _display_name(result: dict) -> str | None:
    """A short label for the suggestion itself, not its full address line."""
    return result.get("name") or (result.get("display_name") or "").split(",")[0].strip() or None


async def search_places(query: str, limit: int) -> list[dict]:
    query = query.strip()
    if len(query) < 2:
        return []

    try:
        async with httpx.AsyncClient(
            timeout=NOMINATIM_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await _throttled_get(
                client,
                NOMINATIM_SEARCH_URL,
                {
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": min(limit, MAX_RESULTS),
                    "countrycodes": COUNTRY_CODES,
                    "accept-language": "en",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise PlaceLookupError("Place search is unavailable") from exc

    if not isinstance(payload, list):
        raise PlaceLookupError("Place search returned an unexpected response")

    places = []
    seen: set[str] = set()
    for result in payload:
        if not isinstance(result, dict):
            continue
        name = _display_name(result)
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not name:
            continue

        # A single OSM feature can come back more than once, so the OSM id
        # alone is not unique. The position makes each suggestion distinct,
        # and an exact repeat is dropped rather than listed twice.
        identifier = (
            f"{result.get('osm_type', 'x')}{result.get('osm_id', 'x')}"
            f"-{latitude:.5f},{longitude:.5f}"
        )
        if identifier in seen:
            continue
        seen.add(identifier)

        places.append(
            {
                "id": identifier,
                "name": name,
                "context": _context(result.get("address") or {}),
                "coordinate": {"latitude": latitude, "longitude": longitude},
            }
        )
    return places


# Hotspot centres are named one at a time, in series: Nominatim's shared rate
# limit is process-wide, so naming several areas per map request still obeys
# it rather than racing to be first.
CACHE_PRECISION = 3  # ~110 m: just finer than the 150 m aggregation floor, so
# one cached label is never reused across two genuinely different areas.
CACHE_LIMIT = 512

_area_cache: dict[tuple[float, float], str | None] = {}


def _area_label(address: dict) -> str | None:
    """A district- or city-level label, and nothing finer.

    `road`, `house_number` and the feature's own name are deliberately
    ignored. An area is published at a 150 m floor precisely so it cannot
    point at the place an incident was reported; naming it after a street or a
    building would undo that in one line of JSON.
    """
    parts = [
        address.get("suburb") or address.get("neighbourhood"),
        address.get("city") or address.get("town") or address.get("village"),
        address.get("county") or address.get("state_district"),
        address.get("state"),
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
        response = await _throttled_get(
            client,
            NOMINATIM_REVERSE_URL,
            {
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 14,
                "accept-language": "en",
            },
        )
        response.raise_for_status()
        payload = response.json()
        label = _area_label(payload.get("address") or {}) if isinstance(payload, dict) else None
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
    try:
        async with httpx.AsyncClient(
            timeout=NOMINATIM_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        ) as client:
            # Serial, not gathered: the throttle would otherwise just queue the
            # same requests behind one lock while every call still holds a
            # client and a timeout running.
            return [await _reverse_one(client, lat, lon) for lat, lon in coordinates]
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        return [None] * len(coordinates)
