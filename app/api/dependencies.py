"""Shared verified identity and separately privileged report storage dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.security import (
    AuthenticatedUser,
    AuthenticationUnavailable,
    InsufficientPermissions,
    InvalidCredentials,
    get_token_verifier,
)
from app.modules.reporting.service import ReportService
from app.modules.reporting.transcription.translation import GroqReportTranslator
from app.repositories.reports import ReportRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedUser:
    """Authenticate access tokens without consulting privileged database keys."""
    try:
        if credentials is None:
            raise InvalidCredentials
        verifier = get_token_verifier(settings.supabase_url, settings.supabase_jwks_url)
        return verifier.verify(credentials.credentials)
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except AuthenticationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is unavailable.",
        ) from None
    except InsufficientPermissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied.",
        ) from None


def require_verified_reporter_id(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> UUID:
    """Supply report ownership exclusively from a successfully verified token."""
    return user.id


async def get_report_repository(
    _reporter_id: Annotated[UUID, Depends(require_verified_reporter_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[ReportRepository]:
    """Open a privileged Data API client only after the identity gate succeeds."""
    secret = settings.supabase_secret_key.get_secret_value()
    if not settings.supabase_url.startswith("https://") or not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report storage is unavailable.",
        )
    try:
        async with httpx.AsyncClient(
            base_url=f"{settings.supabase_url.rstrip('/')}/rest/v1/",
            headers={"apikey": secret},
            timeout=10.0,
            trust_env=False,
        ) as client:
            yield ReportRepository(client)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report storage is unavailable.",
        ) from None


def get_report_service(
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReportService:
    """Keep translation credentials separate from verified identity and database access."""
    return ReportService(
        repository,
        GroqReportTranslator(
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
