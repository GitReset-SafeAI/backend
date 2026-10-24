"""Place lookup for the journey planner.

Geocoding is proxied here rather than called from the app, so the provider and
any future key stay on the server and one cache can serve every user. Photon is
an OpenStreetMap geocoder, which keeps place names and the walking routes on
the same underlying data.

A place query carries no report content. Results are public place names only.
"""

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
