"""Incident report creation using a verified Supabase user identity."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import (
    get_report_repository,
    get_report_service,
    require_verified_reporter_id,
)
from app.modules.reporting.service import ReportService
from app.modules.reporting.transcription.translation import TranslationError
from app.repositories.reports import ReportPersistenceError, ReportRepository
from app.schemas.report import ReportCreateRequest, ReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=list[ReportResponse])
async def list_reports(
    reporter_id: Annotated[UUID, Depends(require_verified_reporter_id)],
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReportResponse]:
    """List the authenticated user's reports, newest first."""
    try:
        return await repository.list_owned(reporter_id, limit, offset)
    except ReportPersistenceError:
        raise HTTPException(status_code=503, detail="Report storage is unavailable.") from None


@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Missing or invalid bearer access token."},
        403: {"description": "Verified token lacks the authenticated-user role."},
        503: {"description": "Authentication, translation, or report storage is unavailable."},
    },
    description="Create a privately owned report using a verified Supabase bearer access token.",
)
async def create_report(
    submission: ReportCreateRequest,
    reporter_id: Annotated[UUID, Depends(require_verified_reporter_id)],
    service: Annotated[ReportService, Depends(get_report_service)],
) -> ReportResponse:
    """Return a created report only after persistence returns validated saved data."""
    try:
        return await service.create(submission, reporter_id)
    except TranslationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report translation is unavailable. Report was not created.",
        ) from None
    except ReportPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report storage is unavailable.",
        ) from None
