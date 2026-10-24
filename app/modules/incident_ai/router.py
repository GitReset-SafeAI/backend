"""Incident AI HTTP routes used by the reporting module."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_report_repository, require_verified_reporter_id
from app.core.config import get_settings
from app.modules.incident_ai.classifier import GroqIncidentClassifier
from app.modules.incident_ai.repository import AnalysisStorageError, SupabaseAnalysisRepository
from app.modules.incident_ai.schemas import AnalyzeIncidentRequest, IncidentAnalysis
from app.modules.incident_ai.service import IncidentAnalysisService
from app.repositories.reports import ReportPersistenceError, ReportRepository

router = APIRouter(prefix="/incident-ai", tags=["incident_ai"])


def get_analysis_repository() -> SupabaseAnalysisRepository:
    settings = get_settings()
    return SupabaseAnalysisRepository(
        settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
    )


@router.get("/analyses", response_model=list[dict])
async def list_analyses(
    repository: Annotated[SupabaseAnalysisRepository, Depends(get_analysis_repository)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    """Retrieve all stored columns, newest first; increase offset for subsequent pages."""
    try:
        return await repository.list_analyses(limit, offset)
    except AnalysisStorageError as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve incident analyses") from exc


def get_incident_analysis_service() -> IncidentAnalysisService:
    settings = get_settings()
    classifier = GroqIncidentClassifier(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    repository = SupabaseAnalysisRepository(
        settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
    )
    return IncidentAnalysisService(classifier, repository)


@router.post("/analyze", response_model=IncidentAnalysis)
async def analyze_incident(
    request: AnalyzeIncidentRequest,
    reporter_id: Annotated[UUID, Depends(require_verified_reporter_id)],
    reports: Annotated[ReportRepository, Depends(get_report_repository)],
    service: Annotated[IncidentAnalysisService, Depends(get_incident_analysis_service)],
) -> IncidentAnalysis:
    """Analyze the authenticated owner's stored English text, never caller replacement text."""

    try:
        translated_text = await reports.get_owned_translation(request.report_id, reporter_id)
    except ReportPersistenceError:
        raise HTTPException(status_code=503, detail="Report storage is unavailable.") from None
    if translated_text is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    try:
        return await service.analyze(
            translated_text,
            request.report_id,
            request.reported_time,
            request.street_lights,
        )
    except AnalysisStorageError as exc:
        raise HTTPException(status_code=503, detail="Could not save incident analysis") from exc
