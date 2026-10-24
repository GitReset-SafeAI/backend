"""Incident report creation using a verified Supabase user identity."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_report_repository, require_verified_reporter_id
from app.repositories.reports import ReportPersistenceError, ReportRepository
from app.schemas.report import ReportResponse, ReportSubmission

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Missing or invalid bearer access token."},
        403: {"description": "Verified token lacks the authenticated-user role."},
        503: {"description": "Authentication or report storage is unavailable."},
    },
    description="Create a privately owned report using a verified Supabase bearer access token.",
)
async def create_report(
    submission: ReportSubmission,
    reporter_id: Annotated[UUID, Depends(require_verified_reporter_id)],
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
) -> ReportResponse:
    """Return a created report only after persistence returns validated saved data."""
    try:
        return await repository.create(submission, reporter_id)
    except ReportPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report storage is unavailable.",
        ) from None
