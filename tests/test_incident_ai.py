"""Incident analysis endpoint and failure behavior."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.modules.incident_ai.classifier import ClassificationError, _extract_message_content
from app.modules.incident_ai.router import get_incident_analysis_service
from app.modules.incident_ai.schemas import ExtractedDetails, IncidentAnalysis
from app.modules.incident_ai.service import IncidentAnalysisService


class SuccessfulClassifier:
    async def classify(self, translated_text: str) -> IncidentAnalysis:
        assert translated_text == "A man followed me from the bus stop to my street."
        return IncidentAnalysis(
            analysis_status="completed",
            category="stalking",
            severity="high",
            confidence=0.91,
            extracted_details=ExtractedDetails(
                summary="The reporter describes being followed from a bus stop.",
                reported_behaviours=["following"],
                reported_location="from the bus stop to the reporter's street",
                reported_time=None,
                immediate_danger_reported=None,
            ),
            review_reason=None,
        )


class FailingClassifier:
    async def classify(self, translated_text: str) -> IncidentAnalysis:
        raise ClassificationError("provider failed")


@pytest.fixture
def app_client() -> Iterator[tuple[TestClient, object]]:
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, app
    get_settings.cache_clear()


def test_analyze_incident_returns_structured_result(app_client: tuple[TestClient, object]) -> None:
    client, app = app_client
    app.dependency_overrides[get_incident_analysis_service] = lambda: IncidentAnalysisService(
        SuccessfulClassifier()
    )

    response = client.post(
        "/api/v1/incident-ai/analyze",
        json={"translated_text": "A man followed me from the bus stop to my street."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "analysis_status": "completed",
        "category": "stalking",
        "severity": "high",
        "confidence": 0.91,
        "extracted_details": {
            "summary": "The reporter describes being followed from a bus stop.",
            "reported_behaviours": ["following"],
            "reported_location": "from the bus stop to the reporter's street",
            "reported_time": None,
            "immediate_danger_reported": None,
        },
        "review_reason": None,
    }


def test_provider_failure_returns_review_fallback(app_client: tuple[TestClient, object]) -> None:
    client, app = app_client
    app.dependency_overrides[get_incident_analysis_service] = lambda: IncidentAnalysisService(
        FailingClassifier()
    )

    response = client.post(
        "/api/v1/incident-ai/analyze",
        json={"translated_text": "I cannot explain what happened."},
    )

    assert response.status_code == 200
    assert response.json()["analysis_status"] == "needs_review"
    assert response.json()["category"] == "unclear"
    assert response.json()["severity"] == "undetermined"
    assert response.json()["confidence"] is None
    assert response.json()["review_reason"] == "analysis_unavailable"


def test_groq_message_content_is_extracted() -> None:
    content = '{"analysis_status":"needs_review"}'
    response = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    assert _extract_message_content(response) == content


@pytest.mark.parametrize("translated_text", ["", "   ", "x" * 5001])
def test_invalid_translated_text_is_rejected(
    app_client: tuple[TestClient, object], translated_text: str
) -> None:
    client, _ = app_client
    response = client.post("/api/v1/incident-ai/analyze", json={"translated_text": translated_text})
    assert response.status_code == 422
