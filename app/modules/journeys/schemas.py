"""Wire shapes for a shared journey.

A shared journey carries a live position, which is the most sensitive thing
this application handles. What is published is therefore kept to exactly what a
viewer needs to follow along: where the traveller is, the path they chose, and
when the share stops working. No reporter, no report, and no account is
reachable from a share link.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.modules.safety_map.schemas import Coordinate

# A share is short-lived by construction. Someone who keeps a link cannot keep
# watching: the longest a journey may be shared is a single walk.
MAX_SHARE_MINUTES = 240
DEFAULT_SHARE_MINUTES = 120

# A label the traveller chose for either end, shown to the viewer. Free text is
# bounded so a link cannot carry a message, and it is never a report.
MAX_LABEL_LENGTH = 80

Label = Annotated[str, Field(max_length=MAX_LABEL_LENGTH)]


class JourneyStartRequest(BaseModel):
    """The route being walked, as the planner produced it."""

    origin: Coordinate
    destination: Coordinate
    origin_label: Label = ""
    destination_label: Label = ""
    # The chosen route's geometry, so a viewer sees the path rather than only a
    # moving dot. Bounded: this is a journey, not a track log.
    geometry: Annotated[list[Coordinate], Field(min_length=2, max_length=2000)]
    duration_s: Annotated[int, Field(ge=0, le=86_400)]
    share_minutes: Annotated[int, Field(ge=1, le=MAX_SHARE_MINUTES)] = DEFAULT_SHARE_MINUTES


class JourneyPositionRequest(BaseModel):
    position: Coordinate
    # Metres of reported GPS accuracy, shown to the viewer so a wide fix is not
    # read as a precise one.
    accuracy_m: Annotated[float, Field(ge=0, le=10_000)] | None = None


class JourneyShare(BaseModel):
    """Returned once, to the traveller who started the journey."""

    share_id: str
    # Held only by the traveller. Updating or ending a journey needs it, so a
    # link that was forwarded cannot move or stop the dot.
    owner_key: str
    expires_at: datetime


class SharedJourney(BaseModel):
    """What a viewer of the link may see."""

    share_id: str
    status: Literal["active", "arrived", "ended"]
    origin: Coordinate
    destination: Coordinate
    origin_label: str
    destination_label: str
    geometry: list[Coordinate]
    duration_s: int
    position: Coordinate | None
    accuracy_m: float | None
    started_at: datetime
    updated_at: datetime | None
    expires_at: datetime
