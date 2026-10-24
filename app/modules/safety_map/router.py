"""Safety-map HTTP routes."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import get_settings
from app.modules.safety_map.places import PlaceLookupError, search_places
from app.modules.safety_map.repository import MapDataError
from app.modules.safety_map.schemas import (
    HotspotsResponse,
    PlaceSuggestion,
    RouteComparisonRequest,
    RouteComparisonResponse,
    TimePeriodFilter,
)
from app.modules.safety_map.service import DEFAULT_WINDOW_DAYS, SafetyMapService

router = APIRouter(prefix="/safety-map", tags=["safety_map"])

MAX_BBOX_DEGREES = 2.0


@lru_cache
def get_service() -> SafetyMapService:
    settings = get_settings()
    return SafetyMapService(
        settings.supabase_url,
        settings.supabase_service_role_key.get_secret_value(),
    )


def _parse_bbox(bbox: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """`min_lon,min_lat,max_lon,max_lat`, bounded so one call cannot scan the country."""
    parts = bbox.split(",")
    if len(parts) != 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bbox needs four values")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bbox must be numbers") from exc

    if not (-90 <= min_lat <= max_lat <= 90 and -180 <= min_lon <= max_lon <= 180):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bbox is out of range")
    if (max_lat - min_lat) > MAX_BBOX_DEGREES or (max_lon - min_lon) > MAX_BBOX_DEGREES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bbox is too large")
    return (min_lat, min_lon), (max_lat, max_lon)


@router.get("/hotspots", response_model=HotspotsResponse)
async def read_hotspots(
    bbox: Annotated[str, Query(description="min_lon,min_lat,max_lon,max_lat")],
    service: Annotated[SafetyMapService, Depends(get_service)],
    time_period: TimePeriodFilter = "all",
    window_days: Annotated[int, Query(ge=1, le=365)] = DEFAULT_WINDOW_DAYS,
) -> HotspotsResponse:
    south_west, north_east = _parse_bbox(bbox)
    try:
        return await service.hotspots(south_west, north_east, time_period, window_days)
    except MapDataError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Reported areas are unavailable"
        ) from exc


@router.get("/places", response_model=list[PlaceSuggestion])
async def read_places(
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=8)] = 6,
) -> list[PlaceSuggestion]:
    try:
        return [PlaceSuggestion.model_validate(place) for place in await search_places(q, limit)]
    except PlaceLookupError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Place search is unavailable"
        ) from exc


@router.post("/routes/compare", response_model=RouteComparisonResponse)
async def compare_routes(
    request: RouteComparisonRequest,
    service: Annotated[SafetyMapService, Depends(get_service)],
) -> RouteComparisonResponse:
    try:
        return await service.compare(
            request.origin,
            request.destination,
            request.time_period,
            request.travel_mode,
        )
    except MapDataError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Routes could not be compared"
        ) from exc
