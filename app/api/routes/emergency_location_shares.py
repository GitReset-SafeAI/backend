"""Short-lived SOS locations, readable by an unguessable link.

Prototype storage is intentionally process-local, matching journey sharing.
Run one worker; a restart invalidates shares. No SMS is sent by this API.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_current_user
from app.core.security import AuthenticatedUser
from app.modules.safety_map.schemas import Coordinate


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    position: Coordinate
    accuracy_m: float = Field(ge=0, le=100_000, allow_inf_nan=False)


class SharedLocation(LocationUpdate):
    share_id: str
    updated_at: datetime
    expires_at: datetime


@dataclass
class OwnedLocation:
    owner: UUID
    view: SharedLocation


class LocationShareStore:
    def __init__(self) -> None:
        self.rows: dict[str, OwnedLocation] = {}
        self.lock = RLock()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def _prune(self) -> None:
        now = self.now()
        for key in [key for key, row in self.rows.items() if row.view.expires_at <= now]:
            del self.rows[key]

    def start(self, owner: UUID, update: LocationUpdate) -> SharedLocation:
        with self.lock:
            self._prune()
            # Repeated SMS taps reuse the active link, not multiple trackers.
            for row in self.rows.values():
                if row.owner == owner:
                    row.view = row.view.model_copy(
                        update={
                            "position": update.position,
                            "accuracy_m": update.accuracy_m,
                            "updated_at": self.now(),
                        }
                    )
                    return row.view
            if len(self.rows) >= 1000:
                raise HTTPException(503, "Location sharing is temporarily unavailable.")
            now = self.now()
            view = SharedLocation(
                **update.model_dump(),
                share_id=secrets.token_urlsafe(32),
                updated_at=now,
                expires_at=now + timedelta(minutes=30),
            )
            self.rows[view.share_id] = OwnedLocation(owner, view)
            return view

    def get(self, share_id: str, owner: UUID | None = None) -> OwnedLocation:
        with self.lock:
            self._prune()
            row = self.rows.get(share_id)
            if row is None or (owner is not None and row.owner != owner):
                raise HTTPException(404, "Location share has ended or is unavailable.")
            return row

    def update(self, share_id: str, owner: UUID, update: LocationUpdate) -> SharedLocation:
        with self.lock:
            row = self.get(share_id, owner)
            row.view = row.view.model_copy(
                update={
                    "position": update.position,
                    "accuracy_m": update.accuracy_m,
                    "updated_at": self.now(),
                }
            )
            return row.view

    def end(self, share_id: str, owner: UUID) -> None:
        with self.lock:
            self.get(share_id, owner)
            del self.rows[share_id]


store = LocationShareStore()


def get_location_store() -> LocationShareStore:
    return store


router = APIRouter(prefix="/emergency-location-shares", tags=["emergency location sharing"])
Store = Annotated[LocationShareStore, Depends(get_location_store)]
User = Annotated[AuthenticatedUser, Depends(get_current_user)]


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post("", response_model=SharedLocation, status_code=201)
def start_share(update: LocationUpdate, user: User, database: Store, response: Response):
    private_response(response)
    return database.start(user.id, update)


@router.get("/{share_id}", response_model=SharedLocation)
def read_share(share_id: str, database: Store, response: Response):
    private_response(response)
    return database.get(share_id).view


@router.patch("/{share_id}", response_model=SharedLocation)
def update_share(
    share_id: str,
    update: LocationUpdate,
    user: User,
    database: Store,
    response: Response,
):
    private_response(response)
    return database.update(share_id, user.id, update)


@router.delete("/{share_id}", status_code=204)
def end_share(share_id: str, user: User, database: Store):
    database.end(share_id, user.id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
