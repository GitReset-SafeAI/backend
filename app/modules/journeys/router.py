"""Journey-sharing HTTP routes.

Three parties, three levels of access:
  * the traveller holds the owner key and may move or end the journey
  * whoever holds the link may watch it, and nothing else
  * everyone else cannot find it: share ids are unguessable, never listed
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.modules.journeys.schemas import (
    JourneyPositionRequest,
    JourneyShare,
    JourneyStartRequest,
    SharedJourney,
)
from app.modules.journeys.store import Journey, store

router = APIRouter(prefix="/journeys", tags=["journeys"])

OwnerKey = Annotated[str | None, Header(alias="X-Journey-Key")]


def _view(journey: Journey) -> SharedJourney:
    request = journey.request
    return SharedJourney(
        share_id=journey.share_id,
        status=journey.status,
        origin=request.origin,
        destination=request.destination,
        origin_label=request.origin_label,
        destination_label=request.destination_label,
        geometry=request.geometry,
        duration_s=request.duration_s,
        position=journey.position,
        accuracy_m=journey.accuracy_m,
        started_at=journey.started_at,
        updated_at=journey.updated_at,
        expires_at=journey.expires_at,
    )


def _owned(share_id: str, owner_key: str | None) -> Journey:
    if not owner_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This journey is not yours to change")
    journey = store.owned(share_id, owner_key)
    if journey is None:
        # Wrong key and missing journey answer the same way: holding a link
        # should not reveal whether a key was close.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journey not found")
    return journey


@router.post("", response_model=JourneyShare, status_code=status.HTTP_201_CREATED)
async def start_journey(request: JourneyStartRequest) -> JourneyShare:
    journey = store.start(request)
    return JourneyShare(
        share_id=journey.share_id,
        owner_key=journey.owner_key,
        expires_at=journey.expires_at,
    )


@router.patch("/{share_id}", response_model=SharedJourney)
async def update_position(
    share_id: str, request: JourneyPositionRequest, x_journey_key: OwnerKey = None
) -> SharedJourney:
    journey = _owned(share_id, x_journey_key)
    return _view(store.update_position(journey, request.position, request.accuracy_m))


@router.get("/{share_id}", response_model=SharedJourney)
async def read_journey(share_id: str) -> SharedJourney:
    """Anyone with the link. An expired or ended journey is simply not found."""
    journey = store.get(share_id)
    if journey is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journey not found")
    return _view(journey)


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_journey(share_id: str, x_journey_key: OwnerKey = None) -> None:
    """Stop sharing. The journey is removed immediately, not marked hidden."""
    store.end(_owned(share_id, x_journey_key))
