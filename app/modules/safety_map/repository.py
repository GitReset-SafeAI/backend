"""Read eligible incident locations for the safety map.

Two sources, one shape. Supabase is used when it is configured; otherwise the
synthetic dataset is loaded so the map is demonstrable without a database.
Eligibility (completed analyses, no duplicates, nothing rejected) lives in the
`map_eligible_incidents` view, so neither path can widen it by accident.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx

SYNTHETIC_DATA = Path(__file__).resolve().parents[3] / "sample-data" / "synthetic_map_reports.json"


class MapDataError(RuntimeError):
    """Eligible incidents could not be read."""


@dataclass(frozen=True, slots=True)
class EligibleIncident:
    """One eligible report. Never published; only aggregates leave this module."""

    latitude: float
    longitude: float
    incident_category: str | None
    safety_concern_category: str | None
    street_lights: str
    reported_time: str
    severity: int | None
    confidence: float | None
    age_days: int


def _age_days(occurred_at: str | None) -> int:
    if not occurred_at:
        return 0
    try:
        moment = datetime.fromisoformat(occurred_at)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - moment).days)


def _from_row(row: dict) -> EligibleIncident | None:
    try:
        return EligibleIncident(
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            incident_category=row.get("incident_category"),
            safety_concern_category=row.get("safety_concern_category"),
            street_lights=row.get("street_lights") or "not_sure",
            reported_time=row.get("reported_time") or "not_sure",
            severity=row.get("severity"),
            confidence=row.get("confidence"),
            age_days=_age_days(row.get("occurred_at")),
        )
    except (KeyError, TypeError, ValueError):
        # A malformed row is skipped rather than failing the whole map.
        return None


@lru_cache
def _synthetic_rows() -> tuple[dict, ...]:
    if not SYNTHETIC_DATA.exists():
        return ()
    try:
        payload = json.loads(SYNTHETIC_DATA.read_text())
        return tuple(payload.get("reports", []))
    except (OSError, ValueError) as exc:
        raise MapDataError("The synthetic map dataset could not be read") from exc


class SyntheticMapRepository:
    """Serves the committed synthetic dataset. Demo data, never real reports."""

    uses_synthetic_data = True

    async def fetch_eligible(
        self,
        south_west: tuple[float, float],
        north_east: tuple[float, float],
        window_days: int,
    ) -> list[EligibleIncident]:
        min_lat, min_lon = south_west
        max_lat, max_lon = north_east
        incidents = []
        for row in _synthetic_rows():
            incident = _from_row(row)
            if incident is None or incident.age_days > window_days:
                continue
            if not (min_lat <= incident.latitude <= max_lat):
                continue
            if not (min_lon <= incident.longitude <= max_lon):
                continue
            incidents.append(incident)
        return incidents


class SupabaseMapRepository:
    """Reads the `map_eligible_incidents` view through PostgREST."""

    uses_synthetic_data = False

    def __init__(self, url: str, service_key: str) -> None:
        self._url = url.rstrip("/")
        self._service_key = service_key

    async def fetch_eligible(
        self,
        south_west: tuple[float, float],
        north_east: tuple[float, float],
        window_days: int,
    ) -> list[EligibleIncident]:
        min_lat, min_lon = south_west
        max_lat, max_lon = north_east
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self._url}/rest/v1/map_eligible_incidents",
                    headers={
                        "apikey": self._service_key,
                        "Authorization": f"Bearer {self._service_key}",
                    },
                    params=[
                        ("select", "*"),
                        ("latitude", f"gte.{min_lat}"),
                        ("latitude", f"lte.{max_lat}"),
                        ("longitude", f"gte.{min_lon}"),
                        ("longitude", f"lte.{max_lon}"),
                        ("limit", 5000),
                    ],
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError("Expected incident rows")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise MapDataError("Could not read eligible incidents") from exc

        incidents = [_from_row(row) for row in rows if isinstance(row, dict)]
        return [
            incident
            for incident in incidents
            if incident is not None and incident.age_days <= window_days
        ]


def build_repository(url: str, service_key: str):
    """Supabase when configured, otherwise the synthetic dataset."""
    if url and service_key:
        return SupabaseMapRepository(url, service_key)
    return SyntheticMapRepository()
