"""Analysis orchestration and provider failure fallback."""

import json
from typing import Protocol
from uuid import UUID

from app.modules.incident_ai.classifier import ClassificationError
from app.modules.incident_ai.schemas import (
    AnalysisOutput,
    ExtractedDetails,
    IncidentAnalysis,
    StreetLights,
)


class Classifier(Protocol):
    async def classify(self, translated_text: str) -> AnalysisOutput: ...


class AnalysisRepository(Protocol):
    async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis: ...


class IncidentAnalysisService:
    def __init__(self, classifier: Classifier, repository: AnalysisRepository) -> None:
        self._classifier = classifier
        self._repository = repository

    async def analyze(
        self,
        translated_text: str,
        report_id: UUID,
        reported_time: str | None = None,
        street_lights: StreetLights | None = None,
    ) -> IncidentAnalysis:
        classifier_input = translated_text
        if reported_time is not None or street_lights is not None:
            classifier_input = json.dumps(
                {
                    "report_text": translated_text,
                    "reporter_supplied_details": {
                        "reported_time": reported_time,
                        "street_lights": street_lights,
                    },
                }
            )
        try:
            output = await self._classifier.classify(classifier_input)
        except ClassificationError:
            output = AnalysisOutput(
                analysis_status="needs_review",
                IncidentCategory="UNCLEAR",
                SafetyConcernCategory=None,
                reported_time="not_sure",
                street_lights="not_sure",
                immediate_danger_reported=None,
                severity=None,
                confidence=None,
                extracted_details=ExtractedDetails(
                    summary=None, reported_behaviours=[], reported_location=None
                ),
            )
        if reported_time is not None:
            output.reported_time = reported_time
        if street_lights is not None:
            output.street_lights = street_lights
        analysis = IncidentAnalysis(report_id=report_id, **output.model_dump())
        return await self._repository.save(analysis)
