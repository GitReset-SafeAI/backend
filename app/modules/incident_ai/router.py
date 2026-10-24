"""Incident AI HTTP routes used by the reporting module."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.modules.incident_ai.classifier import GroqIncidentClassifier
from app.modules.incident_ai.schemas import AnalyzeIncidentRequest, IncidentAnalysis
from app.modules.incident_ai.service import IncidentAnalysisService

router = APIRouter(prefix="/incident-ai", tags=["incident_ai"])


def get_incident_analysis_service() -> IncidentAnalysisService:
    settings = get_settings()
    classifier = GroqIncidentClassifier(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return IncidentAnalysisService(classifier)


@router.post("/analyze", response_model=IncidentAnalysis)
async def analyze_incident(
    request: AnalyzeIncidentRequest,
    service: Annotated[IncidentAnalysisService, Depends(get_incident_analysis_service)],
) -> IncidentAnalysis:
    """Convert translated English report text into a provisional structured record."""

    return await service.analyze(request.translated_text, request.report_id)
