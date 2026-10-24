"""Creation API tests using synthetic identities and an in-process Data API mock."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import require_verified_reporter_id
from app.core.config import Settings, get_settings
from app.schemas.report import ReportResponse, ReportSubmission

REPORTER_ID = UUID("11111111-1111-4111-8111-111111111111")


class MockDataAPI:
    """Emulate Data API creation responses, recording synthetic rows for assertions."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.requests: list[httpx.Request] = []
        self.status_code = 201
        self.failure: httpx.HTTPError | None = None
        self.response_override: object = None
        self.row_override: dict[str, object] = {}
        self.missing_field: str | None = None
        self.clients: list[httpx.AsyncClient] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "example.invalid"
        assert request.url.path == "/rest/v1/reports"
        assert request.method == "POST"
        assert request.headers["apikey"] == "dummy-server-secret"
        assert "authorization" not in request.headers
        assert request.headers["prefer"] == "return=representation"
        if self.failure is not None:
            raise self.failure
        if self.status_code != 201:
            return httpx.Response(self.status_code, json={"message": "dummy private SQL detail"})

        payload = json.loads(request.content)
        assert "id" not in payload and "submitted_at" not in payload
        row = {**payload, "id": str(uuid4()), "submitted_at": "2026-10-23T14:00:00Z"}
        self.rows.append(row.copy())
        row.update(self.row_override)
        if self.missing_field is not None:
            del row[self.missing_field]
        result = self.response_override if self.response_override is not None else [row]
        return httpx.Response(201, json=result)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in (
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWKS_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_VERSION", "0.1.0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def data_api() -> MockDataAPI:
    return MockDataAPI()


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch, data_api: MockDataAPI) -> FastAPI:
    # Import only after the isolated-settings fixture prevents local .env access.
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        supabase_url="https://example.invalid",
        supabase_jwks_url="https://example.invalid/auth/v1/.well-known/jwks.json",
        supabase_secret_key="dummy-server-secret",
    )
    original_async_client = httpx.AsyncClient

    def mock_client(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 10.0
        client = original_async_client(**kwargs, transport=httpx.MockTransport(data_api))
        data_api.clients.append(client)
        return client

    monkeypatch.setattr("app.api.dependencies.httpx.AsyncClient", mock_client)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    # Test-only trusted identity; production has no default or fake reporter.
    app.dependency_overrides[require_verified_reporter_id] = lambda: REPORTER_ID
    return app


@pytest.fixture
def payload() -> dict[str, object]:
    return {
        "original_text": "Synthetic demo: someone followed me after I left the bus.",
        "translated_text": "Synthetic demo: someone followed me after I left the bus.",
        "input_method": "text",
        "language_hint": "en",
    }


def test_create_minimum_report(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 201
    saved = ReportResponse.model_validate(response.json())
    assert saved.original_text == payload["original_text"]
    assert saved.translated_text == payload["translated_text"]
    assert saved.submitted_at == datetime(2026, 10, 23, 14, tzinfo=UTC)
    assert isinstance(saved.id, UUID)
    assert len(data_api.rows) == 1
    assert data_api.rows[0] == {**saved.model_dump(mode="json"), "user_id": str(REPORTER_ID)}
    assert set(response.json()) == set(ReportResponse.model_fields)
    assert "user_id" not in response.json()
    for field in ("lighting_condition", "crowd_level", "current_safety_status"):
        assert response.json()[field] is None
        assert data_api.rows[0][field] is None
    assert all(client.is_closed for client in data_api.clients)


def test_create_report_with_every_field(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    payload.update(
        input_method="voice",
        language_hint="mixed",
        latitude=6.5,
        longitude=80.5,
        occurred_at="2026-10-23T19:00:00+05:30",
        lighting_condition="PARTIALLY_LIT",
        crowd_level="ALMOST_EMPTY",
        current_safety_status="MAY_STILL_BE_IN_DANGER",
    )
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 201
    saved = ReportResponse.model_validate(response.json())
    submitted = ReportSubmission.model_validate(payload)
    assert {
        name: getattr(saved, name) for name in ReportSubmission.model_fields
    } == submitted.model_dump()
    assert data_api.rows[0] == {
        **submitted.model_dump(mode="json"),
        "user_id": str(REPORTER_ID),
        "id": str(saved.id),
        "submitted_at": saved.submitted_at.isoformat().replace("+00:00", "Z"),
    }
    for name in ("lighting_condition", "crowd_level", "current_safety_status"):
        assert response.json()[name] == payload[name]
    selected = set(data_api.requests[0].url.params["select"].split(","))
    assert selected == set(ReportResponse.model_fields) | {"user_id"}


def test_explicit_nulls_are_stored(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    optional = (
        "latitude",
        "longitude",
        "occurred_at",
        "lighting_condition",
        "crowd_level",
        "current_safety_status",
    )
    payload.update(dict.fromkeys(optional))
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 201
    assert all(response.json()[name] is None for name in optional)
    assert all(data_api.rows[0][name] is None for name in optional)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lighting_condition", "invalid"),
        ("crowd_level", "invalid"),
        ("current_safety_status", "invalid"),
        ("input_method", "audio"),
        ("language_hint", "unsupported"),
        ("translated_text", None),
        ("original_text", " " * 5001),
        ("occurred_at", "2026-10-23T14:00:00"),
        ("latitude", 91),
        ("longitude", 181),
        ("latitude", 0),
        ("user_id", "22222222-2222-4222-8222-222222222222"),
        ("id", "22222222-2222-4222-8222-222222222222"),
        ("submitted_at", "2026-10-23T14:00:00Z"),
    ],
)
def test_invalid_input_never_reaches_storage(
    application: FastAPI,
    data_api: MockDataAPI,
    payload: dict[str, object],
    field: str,
    value: object,
) -> None:
    payload[field] = value
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert payload["translated_text"] is None or payload["translated_text"] not in response.text
    assert data_api.requests == []
    assert data_api.rows == []


def test_missing_required_input(application: FastAPI, data_api: MockDataAPI) -> None:
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json={})
    assert response.status_code == 422
    assert {error["loc"][-1] for error in response.json()["detail"]} == {
        "original_text",
        "translated_text",
        "input_method",
        "language_hint",
    }
    assert data_api.requests == []


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer dummy-unverified-token"}, {"X-User-ID": str(REPORTER_ID)}],
)
def test_authentication_is_required(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object], headers: dict[str, str]
) -> None:
    del application.dependency_overrides[require_verified_reporter_id]
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Invalid or missing authentication credentials."}
    assert data_api.clients == []
    assert data_api.requests == []


@pytest.mark.parametrize("status_code", [400, 401, 403, 409, 500, 503])
def test_storage_http_failures_are_safe(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object], status_code: int
) -> None:
    data_api.status_code = status_code
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    assert len(data_api.requests) == 1
    assert data_api.rows == []


def test_storage_timeout_is_safe(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    data_api.failure = httpx.ReadTimeout("dummy secret provider detail")
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    assert len(data_api.requests) == 1  # No automatic retry of a possibly committed insert.


@pytest.mark.parametrize(
    "field",
    [
        "latitude",
        "lighting_condition",
        "crowd_level",
        "current_safety_status",
        "id",
        "submitted_at",
        "user_id",
    ],
)
def test_incomplete_saved_rows_are_not_successes(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object], field: str
) -> None:
    data_api.missing_field = field
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}


@pytest.mark.parametrize(
    "row_override",
    [
        {"user_id": "22222222-2222-4222-8222-222222222222"},
        {"translated_text": "Discarded original input"},
        {"id": "invalid"},
        {"submitted_at": "2026-10-23T14:00:00"},
        {"current_safety_status": "SAFE_NOW"},
    ],
)
def test_mismatched_saved_rows_are_not_successes(
    application: FastAPI,
    data_api: MockDataAPI,
    payload: dict[str, object],
    row_override: dict[str, object],
) -> None:
    data_api.row_override = row_override
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}


@pytest.mark.parametrize("result", [[], {}, [None], [{}, {}]])
def test_invalid_storage_responses_are_not_successes(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object], result: object
) -> None:
    data_api.response_override = result
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503


def test_missing_storage_configuration_is_safe(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    application.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    assert data_api.clients == []


def test_no_public_report_listing(application: FastAPI, data_api: MockDataAPI) -> None:
    with TestClient(application) as client:
        response = client.get("/api/v1/reports")
    assert response.status_code == 405
    assert data_api.requests == []


def test_client_owner_header_cannot_change_owner(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/reports",
            json=payload,
            headers={"X-User-ID": "22222222-2222-4222-8222-222222222222"},
        )
    assert response.status_code == 201
    assert data_api.rows[0]["user_id"] == str(REPORTER_ID)


def test_invalid_internal_identity_never_reaches_storage(
    application: FastAPI, data_api: MockDataAPI, payload: dict[str, object]
) -> None:
    application.dependency_overrides[require_verified_reporter_id] = lambda: str(REPORTER_ID)
    with TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503
    assert data_api.requests == []


def test_malformed_json_is_redacted(application: FastAPI, data_api: MockDataAPI) -> None:
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/reports",
            content='{"original_text":"dummy-private-text",',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert "dummy-private-text" not in response.text
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert data_api.requests == []


def test_storage_failure_does_not_log_private_data(
    application: FastAPI,
    data_api: MockDataAPI,
    payload: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_api.failure = httpx.ReadError(f"dummy-server-secret: {payload['original_text']}")
    with caplog.at_level("INFO"), TestClient(application) as client:
        response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 503
    assert "dummy-server-secret" not in caplog.text
    assert payload["original_text"] not in caplog.text
    assert payload["original_text"] not in response.text
