"""Database-aligned endpoint contract checks."""

from datetime import datetime
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_report_repository, require_verified_reporter_id
from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.incident_ai.classifier import ClassificationError
from app.modules.incident_ai.repository import AnalysisStorageError
from app.modules.incident_ai.router import get_incident_analysis_service
from app.modules.incident_ai.schemas import AnalysisOutput, IncidentAnalysis
from app.modules.incident_ai.service import IncidentAnalysisService
from app.repositories.reports import ReportRepository

REPORT_ID = "00000000-0000-0000-0000-000000000001"
REPORTER_ID = UUID("11111111-1111-4111-8111-111111111111")
STORED_TRANSLATION = "Stored English: someone may have followed me."
FIELDS = {
    "id",
    "report_id",
    "created_at",
    "analysis_status",
    "IncidentCategory",
    "SafetyConcernCategory",
    "reported_time",
    "street_lights",
    "immediate_danger_reported",
    "severity",
    "confidence",
    "extracted_details",
}
PAYLOAD = {
    "analysis_status": "completed",
    "IncidentCategory": "UNWANTED_FOLLOWING",
    "SafetyConcernCategory": "POOR_STREET_LIGHTING",
    "reported_time": "night",
    "street_lights": "poorly_lit_or_dark",
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
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def classify(self, text: str) -> AnalysisOutput:
        self.texts.append(text)
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


class FakeOwnedReports:
    async def get_owned_translation(self, report_id: UUID, reporter_id: UUID) -> str | None:
        assert reporter_id == REPORTER_ID
        return STORED_TRANSLATION if report_id == UUID(REPORT_ID) else None


@pytest.fixture
def application() -> FastAPI:
    app = create_app()
    settings = Settings(
        _env_file=None,
        supabase_url="https://example.invalid",
        supabase_jwks_url="https://example.invalid/auth/v1/.well-known/jwks.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_verified_reporter_id] = lambda: REPORTER_ID
    app.dependency_overrides[get_report_repository] = FakeOwnedReports
    return app


@pytest.mark.parametrize("failed", [False, True])
def test_response_matches_database_columns(application: FastAPI, failed: bool) -> None:
    app = application
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


def test_request_requires_report_id(application: FastAPI) -> None:
    with TestClient(application) as client:
        assert (
            client.post(
                "/api/v1/incident-ai/analyze", json={"translated_text": "A report"}
            ).status_code
            == 422
        )


def test_database_failure_is_not_reported_as_success(application: FastAPI) -> None:
    class FailedRepository:
        async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis:
            raise AnalysisStorageError("unavailable")

    app = application
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


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize(
    "supplied",
    [
        {"reported_time": "afternoon", "street_lights": "day_light"},
        {"reported_time": "afternoon"},
        {"street_lights": "not_sure"},
    ],
)
def test_supplied_details_override_model_and_are_saved(
    application: FastAPI, failed: bool, supplied: dict
) -> None:
    app = application
    repository = FakeRepository()
    app.dependency_overrides[get_incident_analysis_service] = lambda: IncidentAnalysisService(
        FailingClassifier() if failed else FakeClassifier(), repository
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/incident-ai/analyze",
            json={
                "report_id": REPORT_ID,
                "translated_text": "Someone followed me.",
                **supplied,
            },
        )
    assert response.status_code == 200
    result = response.json()
    for field, value in supplied.items():
        assert result[field] == value
    if "reported_time" not in supplied:
        assert result["reported_time"] == ("not_sure" if failed else PAYLOAD["reported_time"])
    if "street_lights" not in supplied:
        assert result["street_lights"] == ("not_sure" if failed else PAYLOAD["street_lights"])
    assert repository.saved[0].model_dump(mode="json") == result


def test_invalid_lighting_rejected_before_provider_call(application: FastAPI) -> None:
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/incident-ai/analyze",
            json={
                "report_id": REPORT_ID,
                "translated_text": "A report",
                "street_lights": "invalid",
            },
        )
    assert response.status_code == 422


class OwnedReportAPI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.body: object = [
            {"id": REPORT_ID, "user_id": str(REPORTER_ID), "translated_text": STORED_TRANSLATION}
        ]
        self.status_code = 200
        self.failure: httpx.HTTPError | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "example.invalid"
        assert request.url.path == "/rest/v1/reports"
        assert dict(request.url.params) == {
            "select": "id,user_id,translated_text",
            "id": f"eq.{REPORT_ID}",
            "user_id": f"eq.{REPORTER_ID}",
            "limit": "1",
        }
        if self.failure:
            raise self.failure
        return httpx.Response(self.status_code, json=self.body)


@pytest.fixture
def owned_api(application: FastAPI) -> OwnedReportAPI:
    api = OwnedReportAPI()

    async def repository():
        async with httpx.AsyncClient(
            base_url="https://example.invalid/rest/v1/", transport=httpx.MockTransport(api)
        ) as client:
            yield ReportRepository(client)

    application.dependency_overrides[get_report_repository] = repository
    return api


@pytest.fixture
def analysis_dependencies(application: FastAPI) -> tuple[FakeClassifier, FakeRepository]:
    classifier = FakeClassifier()
    repository = FakeRepository()
    application.dependency_overrides[get_incident_analysis_service] = lambda: (
        IncidentAnalysisService(classifier, repository)
    )
    return classifier, repository


@pytest.mark.parametrize("replacement", [None, "Caller replacement must never be analyzed."])
def test_analysis_uses_owned_stored_translation(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
    replacement: str | None,
) -> None:
    payload = {"report_id": REPORT_ID}
    if replacement is not None:
        payload["translated_text"] = replacement
    with TestClient(application) as client:
        response = client.post("/api/v1/incident-ai/analyze", json=payload)
    assert response.status_code == 200
    classifier, repository = analysis_dependencies
    assert classifier.texts == [STORED_TRANSLATION]
    assert len(repository.saved) == 1
    assert len(owned_api.requests) == 1
    assert response.json()["report_id"] == REPORT_ID


@pytest.mark.parametrize("exists_for_other_user", [False, True])
def test_other_users_report_and_missing_report_are_indistinguishable(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
    exists_for_other_user: bool,
) -> None:
    # Model PostgREST filtering; a foreign user's stored row must not match both filters.
    other_user = "22222222-2222-4222-8222-222222222222"
    database_rows = (
        [{"id": REPORT_ID, "user_id": other_user, "translated_text": "Other private translation"}]
        if exists_for_other_user
        else []
    )
    owned_api.body = [row for row in database_rows if row["user_id"] == str(REPORTER_ID)]
    with TestClient(application) as client:
        response = client.post("/api/v1/incident-ai/analyze", json={"report_id": REPORT_ID})
    assert response.status_code == 404
    assert response.json() == {"detail": "Report not found."}
    classifier, repository = analysis_dependencies
    assert classifier.texts == repository.saved == []
    assert len(owned_api.requests) == 1
    assert "Other private translation" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        [None],
        [{}, {}],
        [{"id": REPORT_ID, "user_id": str(REPORTER_ID)}],
        [{"id": REPORT_ID, "user_id": str(REPORTER_ID), "translated_text": None}],
        [{"id": REPORT_ID, "user_id": str(REPORTER_ID), "translated_text": " "}],
        [{"id": REPORT_ID, "user_id": str(REPORTER_ID), "translated_text": 123}],
        [{"id": REPORT_ID, "user_id": str(REPORTER_ID), "translated_text": "a" * 5001}],
        [{"id": "invalid", "user_id": str(REPORTER_ID), "translated_text": "Private text"}],
        [{"id": str(REPORTER_ID), "user_id": str(REPORTER_ID), "translated_text": "Private"}],
        [{"id": REPORT_ID, "user_id": "invalid", "translated_text": "Private text"}],
        [{"id": REPORT_ID, "user_id": REPORT_ID, "translated_text": "Another user's text"}],
    ],
)
def test_invalid_owned_report_response_never_reaches_classification(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
    body: object,
) -> None:
    owned_api.body = body
    with TestClient(application) as client:
        response = client.post("/api/v1/incident-ai/analyze", json={"report_id": REPORT_ID})
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    classifier, repository = analysis_dependencies
    assert classifier.texts == repository.saved == []


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_owned_report_storage_errors_do_not_run_analysis(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
    status: int,
) -> None:
    owned_api.status_code = status
    with TestClient(application) as client:
        response = client.post("/api/v1/incident-ai/analyze", json={"report_id": REPORT_ID})
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    classifier, repository = analysis_dependencies
    assert classifier.texts == repository.saved == []


def test_owned_report_timeout_does_not_run_analysis(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
) -> None:
    owned_api.failure = httpx.ReadTimeout("dummy-private-provider-error")
    with TestClient(application) as client:
        response = client.post("/api/v1/incident-ai/analyze", json={"report_id": REPORT_ID})
    assert response.status_code == 503
    assert "dummy-private-provider-error" not in response.text
    classifier, repository = analysis_dependencies
    assert classifier.texts == repository.saved == []


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer invalid"}, {"X-User-ID": str(REPORTER_ID)}]
)
def test_analysis_requires_verified_authentication_before_report_read(
    application: FastAPI,
    owned_api: OwnedReportAPI,
    analysis_dependencies: tuple[FakeClassifier, FakeRepository],
    headers: dict[str, str],
) -> None:
    del application.dependency_overrides[require_verified_reporter_id]
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/incident-ai/analyze", json={"report_id": REPORT_ID}, headers=headers
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert owned_api.requests == []
    classifier, repository = analysis_dependencies
    assert classifier.texts == repository.saved == []
