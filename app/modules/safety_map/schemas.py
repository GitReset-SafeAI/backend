"""Provisional safety-map request and response schemas.

These mirror the mobile contract in
``src/services/contracts/safetyMap.ts``. Field names and coordinate
conventions must change on both sides together.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

TimeBucket = Literal["day", "evening", "night"]
TimeBucketFilter = Literal["day", "evening", "night", "all"]

# How well the underlying reports support what is published. Absence of reports
# is "insufficient" evidence, never an indication of safety.
EvidenceLevel = Literal["insufficient", "limited", "moderate"]

# Aggregation floor. Clusters below this are withheld, both because they are
# weak evidence and because they could identify an individual report.
MIN_REPORTS_PER_HOTSPOT = 3
MIN_HOTSPOT_RADIUS_M = 150.0


class Coordinate(BaseModel):
    """Explicit latitude/longitude. Never a GeoJSON ``[lon, lat]`` pair."""

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]


class Hotspot(BaseModel):
    """An aggregated area, not an incident. No report content is included."""

    id: str
    center: Coordinate
    radius_m: Annotated[float, Field(ge=MIN_HOTSPOT_RADIUS_M)]
    report_count: Annotated[int, Field(ge=MIN_REPORTS_PER_HOTSPOT)]
    intensity: Annotated[float, Field(ge=0, le=1)]
    top_categories: Annotated[list[str], Field(max_length=3)]
    peak_time_bucket: TimeBucket | None
    most_recent_report_days_ago: Annotated[int, Field(ge=0)]
    evidence_level: EvidenceLevel


class HotspotsResponse(BaseModel):
    generated_at: datetime
    window_days: Annotated[int, Field(gt=0)]
    min_reports_per_hotspot: Annotated[int, Field(ge=MIN_REPORTS_PER_HOTSPOT)]
    hotspots: list[Hotspot]
    # Count of clusters withheld for falling under the aggregation floor, so the
    # map can state that areas were withheld rather than imply none exist.
    suppressed_area_count: Annotated[int, Field(ge=0)]
    evidence_level: EvidenceLevel


# Route comparison is outside the required initial scope; it is contracted here
# so it can be added without altering the hotspot response.

RouteRole = Literal["fastest", "lower-exposure", "alternative"]


class Route(BaseModel):
    id: str
    role: RouteRole
    duration_s: Annotated[int, Field(ge=0)]
    distance_m: Annotated[float, Field(ge=0)]
    geometry: Annotated[list[Coordinate], Field(min_length=2)]
    # Relative proximity to reported-incident areas. Not a safety score.
    exposure_score: Annotated[float, Field(ge=0, le=100)]
    hotspots_passed: Annotated[int, Field(ge=0)]
    # What kinds of issue were reported along this path, so every route can be
    # explained rather than only ranked.
    issue_categories: Annotated[list[str], Field(max_length=4)]
    # Relative to the fastest route; 0 for the fastest route itself.
    extra_duration_s: Annotated[int, Field(ge=0)]


class RouteComparisonRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    time_bucket: TimeBucketFilter = "all"


class RouteComparisonResponse(BaseModel):
    routes: Annotated[list[Route], Field(min_length=1, max_length=4)]
    # None when no route meaningfully lowers exposure within an acceptable
    # detour. Recommending nothing is a valid answer, not a failure.
    recommended_route_id: str | None
    evidence_level: EvidenceLevel
