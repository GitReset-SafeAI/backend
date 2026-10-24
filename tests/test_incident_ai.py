"""Database-aligned endpoint contract checks."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.incident_ai.classifier import ClassificationError
from app.modules.incident_ai.repository import AnalysisStorageError
from app.modules.incident_ai.router import get_incident_analysis_service
from app.modules.incident_ai.schemas import AnalysisOutput, IncidentAnalysis
from app.modules.incident_ai.service import IncidentAnalysisService

REPORT_ID = "00000000-0000-0000-0000-000000000001"
FIELDS = {
    "id",
    "report_id",
    "created_at",
    "analysis_status",
    "IncidentCategory",
    "SafetyConcernCategory",
    "reported_time",
    "immediate_danger_reported",
    "severity",
    "confidence",
    "extracted_details",
}
PAYLOAD = {
    "analysis_status": "completed",
    "IncidentCategory": "UNWANTED_FOLLOWING",
    "SafetyConcernCategory": "POOR_STREET_LIGHTING",
    "reported_time": "last night",
    "immediate_danger_reported": None,
    "severity": 6,
    "confidence": 0.9,
    "extracted_details": {
        "summary": "The reporter describes being followed on a dark road.",
        "reported_behaviours": ["following"],
        "reported_location": "road",
    },
}


class FakeClassifier:
    async def classify(self, text: str) -> AnalysisOutput:
        return AnalysisOutput.model_validate(PAYLOAD)


class FailingClassifier:
    async def classify(self, text: str) -> AnalysisOutput:
        raise ClassificationError("unavailable")


class FakeRepository:
    def __init__(self) -> None:
        self.saved: list[IncidentAnalysis] = []

    async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis:
        self.saved.append(analysis)
        return analysis


@pytest.mark.parametrize("failed", [False, True])
def test_response_matches_database_columns(failed: bool) -> None:
    app = create_app()
    classifier = FailingClassifier() if failed else FakeClassifier()
    repository = FakeRepository()
    app.dependency_overrides[get_incident_analysis_service] = lambda: IncidentAnalysisService(
        classifier, repository
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incident-ai/analyze",
            json={"report_id": REPORT_ID, "translated_text": "Someone followed me on a dark road."},
        )
    assert response.status_code == 200
    assert len(repository.saved) == 1
    assert repository.saved[0].model_dump(mode="json") == response.json()
    result = response.json()
    assert set(result) == FIELDS
    assert result["report_id"] == REPORT_ID
    UUID(result["id"])
    assert datetime.fromisoformat(result["created_at"].replace("Z", "+00:00")).tzinfo
    if failed:
        assert result["analysis_status"] == "needs_review"
        assert result["IncidentCategory"] == "UNCLEAR"
        assert result["severity"] is None
    else:
        assert {k: result[k] for k in PAYLOAD} == PAYLOAD


@pytest.mark.parametrize("severity", [-1, 11, 2.5, True])
def test_invalid_severity_rejected(severity: object) -> None:
    with pytest.raises(ValueError):
        AnalysisOutput.model_validate(PAYLOAD | {"severity": severity})


def test_request_requires_report_id() -> None:
    with TestClient(create_app()) as client:
        assert (
            client.post(
                "/api/v1/incident-ai/analyze", json={"translated_text": "A report"}
            ).status_code
            == 422
        )


def test_database_failure_is_not_reported_as_success() -> None:
    class FailedRepository:
        async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis:
            raise AnalysisStorageError("unavailable")

    app = create_app()
    app.dependency_overrides[get_incident_analysis_service] = lambda: IncidentAnalysisService(
        FakeClassifier(), FailedRepository()
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incident-ai/analyze",
            json={"report_id": REPORT_ID, "translated_text": "Someone followed me."},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Could not save incident analysis"}
