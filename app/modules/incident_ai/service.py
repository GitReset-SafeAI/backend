"""Incident analysis orchestration and safe provider-failure handling."""

from typing import Protocol

from app.modules.incident_ai.classifier import ClassificationError
from app.modules.incident_ai.schemas import ExtractedDetails, IncidentAnalysis


class Classifier(Protocol):
    async def classify(self, translated_text: str) -> IncidentAnalysis: ...


class IncidentAnalysisService:
    def __init__(self, classifier: Classifier) -> None:
        self._classifier = classifier

    async def analyze(self, translated_text: str) -> IncidentAnalysis:
        try:
            return await self._classifier.classify(translated_text)
        except ClassificationError:
            return IncidentAnalysis(
                analysis_status="needs_review",
                category="unclear",
                severity="undetermined",
                confidence=None,
                extracted_details=ExtractedDetails(
                    summary=None,
                    reported_behaviours=[],
                    reported_location=None,
                    reported_time=None,
                    immediate_danger_reported=None,
                ),
                review_reason="analysis_unavailable",
            )
