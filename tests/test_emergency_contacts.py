"""User isolation, request validation, and the private contact API contract."""

import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.security import AuthenticatedUser
from app.main import create_app

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
CONTACT = "33333333-3333-4333-8333-333333333333"
INPUT = {"name": "Amma", "phone": "+94771234567", "relationship": "family"}
PATH = "/api/v1/emergency-contacts"


@pytest.fixture
def contacts_api(monkeypatch):
    calls = []
    state = {"status": 200, "rows": [{"id": CONTACT, "user_id": OWNER, **INPUT}]}
    original = httpx.AsyncClient

    def handle(request):
        calls.append(request)
        assert request.url.host == "example.invalid"
        assert request.headers["apikey"] == "sb_publishable_test"
        assert request.headers["authorization"] == "Bearer test-user-token"
        if state.get("timeout"):
            raise httpx.ReadTimeout("private transport detail")
        return httpx.Response(state["status"], json=state["rows"])

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 10.0
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr("app.api.routes.emergency_contacts.httpx.AsyncClient", client)
    settings = Settings(
        _env_file=None,
        supabase_url="https://example.invalid",
        supabase_publishable_key="sb_publishable_test",
        supabase_jwks_url="https://example.invalid/auth/v1/.well-known/jwks.json",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(UUID(OWNER))
    with TestClient(app, headers={"Authorization": "Bearer test-user-token"}) as api:
        yield api, calls, state


def test_list_is_user_scoped_and_private(contacts_api):
    api, calls, _ = contacts_api
    response = api.get(PATH)
    assert response.status_code == 200
    assert response.json() == [{"id": CONTACT, **INPUT}]
    assert response.headers["cache-control"] == "no-store"
    assert calls[0].url.params["user_id"] == f"eq.{OWNER}"
    assert calls[0].url.params["order"] == "primary_at.desc,created_at.asc,id.asc"


def test_add_uses_user_scoped_atomic_allocation(contacts_api):
    api, calls, _ = contacts_api
    response = api.post(PATH, json=INPUT)
    assert response.status_code == 201
    assert response.json() == {"id": CONTACT, **INPUT}
    assert calls[0].url.path == "/rest/v1/rpc/add_emergency_contact"
    assert json.loads(calls[0].content) == {
        "p_name": "Amma",
        "p_phone": INPUT["phone"],
        "p_relationship": "family",
    }


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", f"/{CONTACT}", INPUT),
        ("DELETE", f"/{CONTACT}", None),
        ("POST", f"/{CONTACT}/primary", None),
    ],
)
def test_changes_filter_both_owner_and_contact(contacts_api, method, suffix, body):
    api, calls, _ = contacts_api
    response = api.request(method, PATH + suffix, **({"json": body} if body else {}))
    assert response.status_code == (200 if method == "PATCH" else 204)
    assert calls[0].url.params["user_id"] == f"eq.{OWNER}"
    assert calls[0].url.params["id"] == f"eq.{CONTACT}"
    if suffix.endswith("primary"):
        assert calls[0].method == "PATCH"
        assert set(json.loads(calls[0].content)) == {"primary_at"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "   "),
        ("phone", "0771234567"),
        ("relationship", "unknown"),
        ("user_id", OTHER),
        ("primary_at", "2099-01-01"),
        ("slot", 0),
    ],
)
def test_invalid_or_client_owned_fields_are_rejected(contacts_api, field, value):
    api, calls, _ = contacts_api
    response = api.post(PATH, json={**INPUT, field: value})
    assert response.status_code == 422
    assert calls == []
    assert INPUT["phone"] not in response.text
    assert all("input" not in detail for detail in response.json()["detail"])


def test_relationship_can_be_omitted(contacts_api):
    api, _, state = contacts_api
    state["rows"][0]["relationship"] = None
    response = api.post(PATH, json={"name": "Amma", "phone": INPUT["phone"]})
    assert response.status_code == 201
    assert "relationship" not in response.json()


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("PATCH", f"/{CONTACT}"),
        ("DELETE", f"/{CONTACT}"),
        ("POST", f"/{CONTACT}/primary"),
    ],
)
def test_inaccessible_contact_returns_not_found(contacts_api, method, suffix):
    api, _, state = contacts_api
    state["rows"] = []
    response = api.request(method, PATH + suffix, **({"json": INPUT} if method == "PATCH" else {}))
    assert response.status_code == 404


def test_limit_and_provider_error_are_sanitized(contacts_api):
    api, _, state = contacts_api
    state.update(status=409, rows={"message": "private SQL"})
    assert api.post(PATH, json=INPUT).status_code == 409
    state["status"] = 500
    response = api.get(PATH)
    assert response.status_code == 503
    assert "private SQL" not in response.text


def test_storage_must_not_return_another_users_contact(contacts_api):
    api, _, state = contacts_api
    state["rows"][0]["user_id"] = OTHER
    response = api.get(PATH)
    assert response.status_code == 503
    assert INPUT["phone"] not in response.text


def test_timeout_fails_without_leaking_details(contacts_api):
    api, _, state = contacts_api
    state["timeout"] = True
    response = api.get(PATH)
    assert response.status_code == 503
    assert "private" not in response.text


def test_missing_auth_is_rejected_before_storage(contacts_api):
    api, calls, _ = contacts_api
    del api.app.dependency_overrides[get_current_user]
    api.headers.pop("Authorization")
    assert api.get(PATH).status_code == 401
    assert calls == []
