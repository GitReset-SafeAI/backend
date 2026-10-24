"""Analysis orchestration and provider failure fallback."""

from typing import Protocol
from uuid import UUID

from app.modules.incident_ai.classifier import ClassificationError
from app.modules.incident_ai.schemas import AnalysisOutput, ExtractedDetails, IncidentAnalysis


class Classifier(Protocol):
    async def classify(self, translated_text: str) -> AnalysisOutput: ...


class AnalysisRepository(Protocol):
    async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis: ...


class IncidentAnalysisService:
    def __init__(self, classifier: Classifier, repository: AnalysisRepository) -> None:
        self._classifier = classifier
        self._repository = repository

    async def analyze(self, translated_text: str, report_id: UUID) -> IncidentAnalysis:
        try:
            output = await self._classifier.classify(translated_text)
        except ClassificationError:
            output = AnalysisOutput(
                analysis_status="needs_review",
                IncidentCategory="UNCLEAR",
                SafetyConcernCategory=None,
                reported_time=None,
                immediate_danger_reported=None,
                severity=None,
                confidence=None,
                extracted_details=ExtractedDetails(
                    summary=None, reported_behaviours=[], reported_location=None
                ),
            )
        analysis = IncidentAnalysis(report_id=report_id, **output.model_dump())
        return await self._repository.save(analysis)
