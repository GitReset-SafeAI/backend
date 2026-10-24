"""Provisional safety-map request and response schemas.

These mirror the mobile contract in
``src/services/contracts/safetyMap.ts``. Field names and coordinate
conventions must change on both sides together.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Mirrors incident_ai_analyses.reported_time, so the map filter and the stored
# value are the same vocabulary and need no translation between modules.
TimePeriod = Literal["morning", "afternoon", "evening", "night", "late_night", "not_sure"]
TimePeriodFilter = Literal[
    "all", "morning", "afternoon", "evening", "night", "late_night", "not_sure"
]

# How well the underlying reports support what is published. Absence of reports
# is "insufficient" evidence, never an indication of safety.
EvidenceLevel = Literal["insufficient", "limited", "moderate"]

# Aggregation floor. Clusters below this are withheld, both because they are
# weak evidence and because they could identify an individual report.
MIN_REPORTS_PER_HOTSPOT = 3
MIN_HOTSPOT_RADIUS_M = 150.0

# Vocabularies are owned by the incident-AI module
# (`incident_ai_analyses.IncidentCategory` / `SafetyConcernCategory`). They are
# carried as separate fields because OTHER and UNCLEAR appear in both. Values
# are passed through as plain strings so a vocabulary change there cannot
# break the map before this module is updated.
INCIDENT_CATEGORIES = (
    "UNWANTED_FOLLOWING",
    "VERBAL_SEXUAL_HARASSMENT",
    "UNWANTED_PHYSICAL_CONTACT",
    "THREATENING_BEHAVIOUR",
    "UNCOMFORTABLE_STARING",
    "OTHER",
    "UNCLEAR",
)
SAFETY_CONCERN_CATEGORIES = (
    "POOR_STREET_LIGHTING",
    "ISOLATED_OR_DESERTED_ROAD",
    "UNSAFE_BUS_STOP",
    "FEELING_UNSAFE_WITHOUT_CONFIRMED_INCIDENT",
    "PREVIOUS_SAFETY_CONCERNS_AT_LOCATION",
    "OTHER",
    "UNCLEAR",
)

# `incident_ai_analyses.severity` is a smallint 0-10, so hotspot weighting uses
# severity / MAX_SEVERITY rather than a low/medium/high lookup.
MAX_SEVERITY = 10

# Agreed eligibility: only completed analyses contribute to the map. A report
# the AI could not read is not evidence of anything and waits for review.
ELIGIBLE_ANALYSIS_STATUS = "completed"

# Confidence weights a report's contribution rather than excluding it, so a
# genuine report the AI read poorly is quietly discounted, not deleted.
CONFIDENCE_FLOOR = 0.0

# incident_ai_analyses.street_lights. Only poorly_lit_or_dark is treated as a
# lighting concern; not_sure and day_light say nothing about the location.
POORLY_LIT = "poorly_lit_or_dark"
# Share of an area's reports that must mention poor lighting before the map
# shows it as a lighting concern.
POOR_LIGHTING_SHARE = 0.5


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
    top_incident_categories: Annotated[list[str], Field(max_length=3)]
    top_safety_concerns: Annotated[list[str], Field(max_length=3)]
    peak_time_period: TimePeriod | None
    most_recent_report_days_ago: Annotated[int, Field(ge=0)]
    # Mean AI confidence across the reports behind this area. needs_review
    # analyses are excluded upstream and never reach here.
    mean_confidence: Annotated[float, Field(ge=0, le=1)]
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
    # What was reported along this path, so every route can be explained
    # rather than only ranked.
    incident_categories: Annotated[list[str], Field(max_length=3)]
    safety_concerns: Annotated[list[str], Field(max_length=3)]
    # Relative to the fastest route; 0 for the fastest route itself.
    extra_duration_s: Annotated[int, Field(ge=0)]


class RouteComparisonRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    time_period: TimePeriodFilter = "all"


class RouteComparisonResponse(BaseModel):
    routes: Annotated[list[Route], Field(min_length=1, max_length=4)]
    # None when no route meaningfully lowers exposure within an acceptable
    # detour. Recommending nothing is a valid answer, not a failure.
    recommended_route_id: str | None
    evidence_level: EvidenceLevel
    # False when the routing service could not be reached and the geometry is
    # a straight-line approximation rather than real walking directions.
    uses_street_network: bool


class PlaceSuggestion(BaseModel):
    """A public place name for the journey planner. Carries no report data."""

    id: str
    name: str
    # Area line shown under the name, as in a familiar map app.
    context: str
    coordinate: Coordinate
