"""Where a shared journey lives while it is being walked.

In process memory, on purpose. A live position is the most sensitive thing this
application holds, and the shortest-lived: it is useful for one walk and then
never again. Keeping it out of the database means it cannot be queried later,
cannot be joined to a reporter, and cannot outlive a restart.

The cost is honest and recorded in the module README: a share does not survive a
deploy, and a second worker process does not see the first one's journeys. A
deployment that runs more than one worker needs a shared store with the same
expiry behaviour, not a longer-lived one.
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.modules.journeys.schemas import JourneyStartRequest
from app.modules.safety_map.schemas import Coordinate

# A journey ends when the traveller says so, or when the share expires. It is
# also marked arrived once they are close enough to the destination that
# following further would just be watching someone's doorstep.
ARRIVAL_RADIUS_M = 75.0

# Sweep expired journeys whenever the store is touched, so nothing lingers in
# memory because no one asked for it again.
_SWEEP_EVERY = timedelta(minutes=1)


@dataclass
class Journey:
    share_id: str
    owner_key: str
    request: JourneyStartRequest
    started_at: datetime
    expires_at: datetime
    position: Coordinate | None = None
    accuracy_m: float | None = None
    updated_at: datetime | None = None
    ended: bool = False
    arrived: bool = False

    @property
    def status(self) -> str:
        if self.ended:
            return "ended"
        return "arrived" if self.arrived else "active"


@dataclass
class JourneyStore:
    _journeys: dict[str, Journey] = field(default_factory=dict)
    _last_sweep: datetime = field(default_factory=lambda: datetime.now(UTC))

    def _sweep(self, now: datetime) -> None:
        if now - self._last_sweep < _SWEEP_EVERY:
            return
        self._last_sweep = now
        for share_id in [k for k, j in self._journeys.items() if j.expires_at <= now]:
            del self._journeys[share_id]

    def start(self, request: JourneyStartRequest) -> Journey:
        now = datetime.now(UTC)
        self._sweep(now)
        journey = Journey(
            # Unguessable: a share link is the only thing protecting a live
            # position, so it must not be enumerable.
            share_id=secrets.token_urlsafe(12),
            owner_key=secrets.token_urlsafe(24),
            request=request,
            started_at=now,
            expires_at=now + timedelta(minutes=request.share_minutes),
        )
        self._journeys[journey.share_id] = journey
        return journey

    def get(self, share_id: str) -> Journey | None:
        now = datetime.now(UTC)
        self._sweep(now)
        journey = self._journeys.get(share_id)
        if journey is None:
            return None
        if journey.expires_at <= now:
            # Expired is gone, not merely hidden.
            del self._journeys[share_id]
            return None
        return journey

    def update_position(
        self, journey: Journey, position: Coordinate, accuracy_m: float | None
    ) -> Journey:
        journey.position = position
        journey.accuracy_m = accuracy_m
        journey.updated_at = datetime.now(UTC)
        if _near(position, journey.request.destination, ARRIVAL_RADIUS_M):
            journey.arrived = True
        return journey

    def end(self, journey: Journey) -> None:
        """Stop sharing now. The journey is removed, not flagged."""
        self._journeys.pop(journey.share_id, None)

    def owned(self, share_id: str, owner_key: str) -> Journey | None:
        journey = self.get(share_id)
        if journey is None or not secrets.compare_digest(journey.owner_key, owner_key):
            return None
        return journey


def _near(a: Coordinate, b: Coordinate, metres: float) -> bool:
    from app.modules.safety_map.routing import _haversine_m

    return _haversine_m((a.latitude, a.longitude), (b.latitude, b.longitude)) <= metres


store = JourneyStore()
