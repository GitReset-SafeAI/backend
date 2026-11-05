"""Registration/login contract tests using synthetic credentials and mocked Auth."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

PAYLOAD = {
    "name": "Nimali",
    "email": "nimali@example.lk",
    "password": "synthetic-password-123",
    "nic": "200012345678",
    "phone_number": "+94771234567",
}
SESSION = {
    "access_token": "synthetic-access-token",
    "refresh_token": "synthetic-refresh-token",
    "token_type": "bearer",
    "expires_in": 3600,
}


@pytest.mark.parametrize(
    "code,logged",
    [
        ("unexpected_failure", "unexpected_failure"),
        ("private-provider-detail@example.lk", "unknown"),
    ],
)
def test_provider_diagnostics_do_not_log_private_data(registration, caplog, code, logged):
    api, _, upstream, _ = registration
    upstream.update(status=500, body={"code": code, "msg": "private SQL and email detail"})
    with caplog.at_level("WARNING", logger="app.services.authentication"):
        assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 503
    assert f"status=500 code={logged}" in caplog.text
    for private in (
        PAYLOAD["email"],
        PAYLOAD["password"],
        PAYLOAD["nic"],
        "private SQL",
        "private-provider",
    ):
        assert private not in caplog.text


@pytest.fixture
def registration(monkeypatch):
    settings = Settings(
        _env_file=None,
        supabase_url="https://example.invalid",
        supabase_publishable_key="sb_publishable_synthetic",
    )
    requests = []
    upstream = {"status": 200, "body": {"id": "11111111-1111-4111-8111-111111111111"}}

    def handle(request):
        requests.append(request)
        assert request.headers["apikey"] == "sb_publishable_synthetic"
        assert "authorization" not in request.headers
        if upstream.get("timeout"):
            raise httpx.ReadTimeout("sensitive upstream detail")
        return httpx.Response(upstream["status"], json=upstream["body"])

    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs == {"timeout": 10.0, "trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr("app.services.authentication.httpx.AsyncClient", client)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as api:
        yield api, requests, upstream, settings


@pytest.mark.parametrize(
    "field,message,reason",
    [
        ("msg", "Error sending confirmation email", "confirmation_email_delivery_failed"),
        ("message", "Database error saving new user", "database_user_creation_failed"),
        ("error_description", "Database error creating new user", "database_user_creation_failed"),
        ("error", "Email address not authorized", "email_recipient_not_authorized"),
        ("msg", "private unknown upstream error", "unclassified"),
        ("msg", {"nested": "private"}, "unclassified"),
    ],
)
def test_legacy_server_error_diagnostics(registration, caplog, field, message, reason):
    api, _, upstream, _ = registration
    upstream.update(status=500, body={"code": 500, field: message})
    with caplog.at_level("WARNING", logger="app.services.authentication"):
        response = api.post("/api/v1/authentication/sign-up", json=PAYLOAD)
    assert response.status_code == 503
    assert f"reason={reason}" in caplog.text
    assert "private" not in caplog.text
    assert response.json() == {"detail": "Authentication is unavailable."}


def test_signup_contract_and_no_nic_or_tokens_in_response(registration):
    api, requests, upstream, _ = registration
    upstream["body"].update(SESSION)
    response = api.post("/api/v1/authentication/sign-up", json=PAYLOAD)
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert str(requests[0].url) == "https://example.invalid/auth/v1/signup"
    assert json.loads(requests[0].content) == {
        "email": PAYLOAD["email"],
        "password": PAYLOAD["password"],
        "data": {"name": PAYLOAD["name"], "phone_number": PAYLOAD["phone_number"]},
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("email", "invalid"),
        ("password", "short"),
        ("name", "   "),
        ("nic", "invalid-nic"),
        ("phone_number", "0771234567"),
        ("role", "admin"),
    ],
)
def test_invalid_registration_never_calls_supabase(registration, field, value):
    api, requests, _, _ = registration
    response = api.post("/api/v1/authentication/sign-up", json={**PAYLOAD, field: value})
    assert response.status_code == 422
    assert requests == []
    assert PAYLOAD["password"] not in response.text
    assert PAYLOAD["nic"] not in response.text
    assert all("input" not in detail for detail in response.json()["detail"])


@pytest.mark.parametrize("code", ["user_already_exists", "email_exists"])
def test_existing_accounts_get_same_signup_result(registration, code):
    api, _, upstream, _ = registration
    upstream.update(status=422, body={"code": code, "msg": "private account detail"})
    assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 204


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (429, "over_email_send_rate_limit", 429),
        (422, "weak_password", 422),
        (500, "unexpected_failure", 503),
        (401, "bad_api_key", 503),
        (302, "redirect", 503),
    ],
)
def test_signup_provider_failures_are_sanitized(registration, status, code, expected):
    api, _, upstream, _ = registration
    upstream.update(status=status, body={"code": code, "msg": "private upstream detail"})
    response = api.post("/api/v1/authentication/sign-up", json=PAYLOAD)
    assert response.status_code == expected
    assert "private" not in response.text


def test_signin_returns_only_session_contract(registration):
    api, requests, upstream, _ = registration
    upstream["body"] = {**SESSION, "user": {"private": "detail"}}
    response = api.post(
        "/api/v1/authentication/sign-in",
        json={
            "email": PAYLOAD["email"],
            "password": PAYLOAD["password"],
        },
    )
    assert response.status_code == 200
    assert response.json() == SESSION
    assert response.headers["cache-control"] == "no-store"
    assert str(requests[0].url) == "https://example.invalid/auth/v1/token?grant_type=password"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("invalid_credentials", 401),
        ("email_not_confirmed", 403),
    ],
)
def test_signin_error_contract(registration, code, expected):
    api, _, upstream, _ = registration
    upstream.update(status=400, body={"code": code})
    response = api.post(
        "/api/v1/authentication/sign-in",
        json={
            "email": PAYLOAD["email"],
            "password": PAYLOAD["password"],
        },
    )
    assert response.status_code == expected


def test_timeout_and_malformed_provider_response(registration):
    api, _, upstream, _ = registration
    upstream["timeout"] = True
    assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 503
    upstream["timeout"] = False
    upstream["body"] = []
    assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 503
    upstream["body"] = {}
    assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 503
    assert (
        api.post(
            "/api/v1/authentication/sign-in",
            json={
                "email": PAYLOAD["email"],
                "password": PAYLOAD["password"],
            },
        ).status_code
        == 503
    )


def test_signup_without_confirmation_discards_returned_session(registration):
    api, _, upstream, _ = registration
    upstream["body"] = {**SESSION, "user": upstream["body"]}
    response = api.post("/api/v1/authentication/sign-up", json=PAYLOAD)
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize(
    "url,key",
    [
        ("", ""),
        ("http://example.invalid", "key"),
        ("https://example.invalid/path", "key"),
        ("https://user:pass@example.invalid", "key"),
        ("https://example.invalid:invalid", "key"),
    ],
)
def test_missing_or_invalid_configuration_fails_closed(registration, url, key):
    api, requests, _, settings = registration
    settings.supabase_url = url
    from pydantic import SecretStr

    settings.supabase_publishable_key = SecretStr(key)
    assert api.post("/api/v1/authentication/sign-up", json=PAYLOAD).status_code == 503
    assert requests == []
