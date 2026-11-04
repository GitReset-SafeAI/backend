"""Authenticated emergency contact management."""

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.api.dependencies import bearer_scheme, get_current_user
from app.core.config import Settings, get_settings
from app.core.security import AuthenticatedUser
from app.repositories.emergency_contacts import EmergencyContactRepository
from app.schemas.emergency_contacts import ContactInput, EmergencyContact

router = APIRouter(prefix="/emergency-contacts", tags=["emergency contacts"])


async def get_contact_repository(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[EmergencyContactRepository]:
    origin = settings.supabase_url.rstrip("/")
    key = settings.supabase_publishable_key.get_secret_value()
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not any(
                (
                    parsed.username,
                    parsed.password,
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
        )
    except ValueError:
        valid = False
    if not valid or not key:
        raise HTTPException(503, "Emergency contact storage is unavailable.")
    async with httpx.AsyncClient(
        base_url=f"{origin}/rest/v1/",
        headers={"apikey": key, "Authorization": f"Bearer {credentials.credentials}"},
        timeout=10.0,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        yield EmergencyContactRepository(client, user.id)


Repository = Annotated[EmergencyContactRepository, Depends(get_contact_repository)]


@router.get("", response_model=list[EmergencyContact], response_model_exclude_none=True)
async def list_contacts(repository: Repository, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await repository.list()


@router.post("", status_code=201, response_model=EmergencyContact, response_model_exclude_none=True)
async def add_contact(request: ContactInput, repository: Repository, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await repository.add(request)


@router.patch("/{contact_id}", response_model=EmergencyContact, response_model_exclude_none=True)
async def update_contact(
    contact_id: UUID,
    request: ContactInput,
    repository: Repository,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return await repository.change(contact_id, request.model_dump())


@router.delete("/{contact_id}", status_code=204)
async def remove_contact(contact_id: UUID, repository: Repository):
    await repository.change(contact_id, None)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/{contact_id}/primary", status_code=204)
async def make_primary(contact_id: UUID, repository: Repository):
    await repository.make_primary(contact_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
