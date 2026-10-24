"""Real signature verification and protected report creation with in-process HTTP mocks."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app.api.dependencies import get_report_analyzer
from app.core.config import Settings, get_settings
from app.core.security import (
    AuthenticatedUser,
    get_token_verifier,
)
from app.schemas.report import ReportResponse

BASE_URL = "https://example.invalid"
JWKS_URL = f"{BASE_URL}/auth/v1/.well-known/jwks.json"
USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
PAYLOAD = {
    "original_text": "Synthetic private report.",
    "translated_text": "Synthetic private report.",
    "input_method": "text",
    "language_hint": "en",
}


@pytest.mark.parametrize("role,status", [("authenticated", 200), ("anon", 403)])
def test_transcription_uses_existing_jwt_gate_without_storage(
    auth, wav_audio: bytes, role: str, status: int
) -> None:
    from app.api.dependencies import get_transcription_service
    from app.modules.reporting.transcription.service import TranscriptionService

    calls = []

    class Provider:
        async def transcribe(self, audio, language_hint):
            calls.append((audio, language_hint.value))
            return "Synthetic reviewed-later draft."

    auth.client.app.dependency_overrides[get_transcription_service] = lambda: TranscriptionService(
        Provider(), max_bytes=10485760, max_duration=60
    )
    response = auth.client.post(
        "/api/v1/reporting/transcriptions?language_hint=si",
        content=wav_audio,
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {auth.token({'role': role})}",
        },
    )
    assert response.status_code == status
    assert len(auth.jwks_requests) == 1
    assert auth.storage_requests == auth.rows == []
    if status == 200:
        assert response.json() == {
            "transcript": "Synthetic reviewed-later draft.",
            "requested_language_hint": "si",
            "review_required": True,
        }
        assert calls == [(wav_audio, "si")]
    else:
        assert response.json() == {"detail": "Permission denied."}
        assert calls == []


@pytest.fixture(scope="module")
def signing_keys() -> dict[str, object]:
    return {
        "es": ec.generate_private_key(ec.SECP256R1()),
        "rotated": ec.generate_private_key(ec.SECP256R1()),
        "rsa": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


def public_jwk(key: object, kid: str, algorithm: str = "ES256") -> dict[str, object]:
    implementation = jwt.algorithms.get_default_algorithms()[algorithm]
    return {
        **json.loads(implementation.to_jwk(key.public_key())),
        "kid": kid,
        "alg": algorithm,
        "use": "sig",
        "key_ops": ["verify"],
    }


class AuthHarness:
    def __init__(self, signing_keys: dict[str, object]) -> None:
        self.keys = signing_keys
        self.jwks: object = {"keys": [public_jwk(signing_keys["es"], "es")]}
        self.jwks_status = 200
        self.jwks_failure: httpx.HTTPError | None = None
        self.jwks_requests: list[httpx.Request] = []
        self.storage_requests: list[httpx.Request] = []
        self.rows: list[dict[str, object]] = []
        self.storage_status = 201
        self.owner_override: str | None = None
        self.clock = [1000.0]
        self.client: TestClient
        self.settings = Settings(
            _env_file=None,
            supabase_url=BASE_URL,
            supabase_jwks_url=JWKS_URL,
            supabase_secret_key="dummy-server-secret",
        )

    def token(
        self,
        changes: dict[str, object] | None = None,
        remove: tuple[str, ...] = (),
        algorithm: str = "ES256",
        kid: str = "es",
        key_name: str = "es",
        headers: dict[str, object] | None = None,
    ) -> str:
        now = int(datetime.now(UTC).timestamp())
        claims = {
            "iss": f"{BASE_URL}/auth/v1",
            "aud": "authenticated",
            "sub": USER_ID,
            "role": "authenticated",
            "exp": now + 3600,
            "iat": now - 1,
            "nbf": now - 1,
            **(changes or {}),
        }
        for name in remove:
            del claims[name]
        key = self.keys[key_name]
        if algorithm == "none":
            key = None
        elif algorithm == "HS256":
            key = "dummy-shared-signing-secret-at-least-32-characters"
        # Sign raw JSON so malformed claim types can reach the verifier even
        # when the higher-level encoder rejects them before signing.
        return jwt.api_jws.encode(
            json.dumps(claims).encode("utf-8"),
            key,
            algorithm=algorithm,
            headers={"kid": kid, **(headers or {})},
        )

    def fetch_jwks(self, request: httpx.Request) -> httpx.Response:
        self.jwks_requests.append(request)
        assert str(request.url) == JWKS_URL
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "apikey" not in request.headers
        if self.jwks_failure:
            raise self.jwks_failure
        return httpx.Response(self.jwks_status, json=self.jwks)

    def store(self, request: httpx.Request) -> httpx.Response:
        self.storage_requests.append(request)
        assert request.url.host == "example.invalid"
        assert request.url.path == "/rest/v1/reports"
        assert request.headers["apikey"] == "dummy-server-secret"
        assert "authorization" not in request.headers
        if self.storage_status != 201:
            return httpx.Response(self.storage_status, json={"message": "private SQL detail"})
        row = {
            **json.loads(request.content),
            "id": "33333333-3333-4333-8333-333333333333",
            "submitted_at": "2026-10-23T14:00:00Z",
            "RAPID_SUBMISSIONS": False,
        }
        self.rows.append(row.copy())
        if self.owner_override:
            row["user_id"] = self.owner_override
        return httpx.Response(201, json=[row])

    def submit(self, token: str, **fields: object) -> httpx.Response:
        return self.client.post(
            "/api/v1/reports",
            json={**PAYLOAD, **fields},
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.fixture
def auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, signing_keys: dict[str, object]
) -> Iterator[AuthHarness]:
    # Never load the real .env or use inherited credentials.
    monkeypatch.chdir(tmp_path)
    for name in (
        "SUPABASE_URL",
        "SUPABASE_JWKS_URL",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_VERSION", "0.1.0")
    get_settings.cache_clear()
    get_token_verifier.cache_clear()
    harness = AuthHarness(signing_keys)
    original_client = httpx.Client
    original_async_client = httpx.AsyncClient

    def jwks_client(**kwargs: object) -> httpx.Client:
        assert kwargs == {"timeout": 5.0, "trust_env": False, "follow_redirects": False}
        return original_client(**kwargs, transport=httpx.MockTransport(harness.fetch_jwks))

    def storage_client(**kwargs: object) -> httpx.AsyncClient:
        return original_async_client(**kwargs, transport=httpx.MockTransport(harness.store))

    monkeypatch.setattr("app.core.security.httpx.Client", jwks_client)
    monkeypatch.setattr("app.api.dependencies.httpx.AsyncClient", storage_client)
    monkeypatch.setattr("app.core.security.monotonic", lambda: harness.clock[0])
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_report_analyzer] = lambda: AsyncMock()
    app.dependency_overrides[get_settings] = lambda: harness.settings
    with TestClient(app) as client:
        harness.client = client
        yield harness
    get_token_verifier.cache_clear()
    get_settings.cache_clear()


def assert_rejected(auth: AuthHarness, response: httpx.Response, status_code: int = 401) -> None:
    assert response.status_code == status_code
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"detail": "Invalid or missing authentication credentials."}
    elif status_code == 503:
        assert response.json() == {"detail": "Authentication is unavailable."}
    assert auth.storage_requests == []


@pytest.mark.parametrize(
    "header", [None, "", "Bearer", "Bearer ", "Basic abc", "Bearer a b", "Bearer broken"]
)
def test_missing_or_malformed_credentials(auth: AuthHarness, header: str | None) -> None:
    headers = {} if header is None else {"Authorization": header}
    response = auth.client.post("/api/v1/reports", json=PAYLOAD, headers=headers)
    assert_rejected(auth, response)
    assert auth.jwks_requests == []


@pytest.mark.parametrize("algorithm,key_name", [("ES256", "es"), ("RS256", "rsa")])
def test_verified_identity_and_report_ownership(
    auth: AuthHarness, algorithm: str, key_name: str
) -> None:
    auth.jwks = {"keys": [public_jwk(auth.keys[key_name], key_name, algorithm)]}
    token = auth.token({"sub": OTHER_ID}, algorithm=algorithm, kid=key_name, key_name=key_name)
    identity = get_token_verifier(BASE_URL, JWKS_URL).verify(token)
    assert identity == AuthenticatedUser(id=UUID(OTHER_ID))
    response = auth.submit(
        token,
        lighting_condition="DAYLIGHT",
        reported_time="02:30",
        current_safety_status="MAY_STILL_BE_IN_DANGER",
    )
    assert response.status_code == 201
    saved = ReportResponse.model_validate(response.json())
    assert saved.current_safety_status == "MAY_STILL_BE_IN_DANGER"
    assert auth.rows[0]["user_id"] == OTHER_ID
    assert "user_id" not in response.json()
    assert len(auth.jwks_requests) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("iss", "https://other.invalid/auth/v1"),
        ("iss", [f"{BASE_URL}/auth/v1"]),
        ("aud", "other"),
        ("aud", 1),
        ("sub", ""),
        ("sub", "not-a-uuid"),
        ("sub", 1),
        ("sub", None),
        ("exp", 1),
        ("exp", "9999999999"),
        ("exp", None),
        ("exp", True),
        ("exp", float("inf")),
        ("nbf", 9999999999),
        ("nbf", "1"),
        ("nbf", None),
        ("iat", 9999999999),
        ("iat", "1"),
    ],
)
def test_invalid_claims(auth: AuthHarness, field: str, value: object) -> None:
    assert_rejected(auth, auth.submit(auth.token({field: value})))


@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "exp"])
def test_missing_required_claim(auth: AuthHarness, claim: str) -> None:
    assert_rejected(auth, auth.submit(auth.token(remove=(claim,))))


def test_invalid_signature(auth: AuthHarness) -> None:
    assert_rejected(auth, auth.submit(auth.token(key_name="rotated")))


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_unsupported_algorithm(auth: AuthHarness, algorithm: str) -> None:
    assert_rejected(auth, auth.submit(auth.token(algorithm=algorithm)))
    assert auth.jwks_requests == []


@pytest.mark.parametrize("headers", [{"kid": ""}, {"kid": "x" * 257}, {"crit": ["unsupported"]}])
def test_invalid_header(auth: AuthHarness, headers: dict[str, object]) -> None:
    assert_rejected(auth, auth.submit(auth.token(headers=headers)))
    assert auth.jwks_requests == []


@pytest.mark.parametrize("role", [None, "anon", "service_role"])
def test_verified_token_without_user_permission(auth: AuthHarness, role: object) -> None:
    response = auth.submit(auth.token({"role": role}))
    assert_rejected(auth, response, 403)
    assert response.json() == {"detail": "Permission denied."}


@pytest.mark.parametrize(
    "field,value",
    [
        ("supabase_url", ""),
        ("supabase_url", "http://example.invalid"),
        ("supabase_url", "https://example.invalid/path"),
        ("supabase_url", "https://example.invalid:invalid"),
        ("supabase_url", "https://user:password@example.invalid"),
        ("supabase_jwks_url", ""),
        ("supabase_jwks_url", "https://other.invalid/jwks"),
    ],
)
def test_invalid_verification_configuration(auth: AuthHarness, field: str, value: str) -> None:
    setattr(auth.settings, field, value)
    assert_rejected(auth, auth.submit(auth.token()), 503)
    assert auth.jwks_requests == []


@pytest.mark.parametrize(
    "document", [None, [], {}, {"keys": []}, {"keys": None}, {"keys": [None]}, {"keys": [{}]}]
)
def test_unusable_jwks(auth: AuthHarness, document: object) -> None:
    auth.jwks = document
    assert_rejected(auth, auth.submit(auth.token()), 503)


def test_jwks_timeout_is_bounded_and_fails_closed(auth: AuthHarness) -> None:
    auth.jwks_failure = httpx.ReadTimeout("dummy internal detail")
    for _ in range(3):
        assert_rejected(auth, auth.submit(auth.token()), 503)
    assert len(auth.jwks_requests) == 1


@pytest.mark.parametrize("status_code", [302, 404, 500])
def test_jwks_http_failure(auth: AuthHarness, status_code: int) -> None:
    auth.jwks_status = status_code
    assert_rejected(auth, auth.submit(auth.token()), 503)


@pytest.mark.parametrize(
    "changes",
    [
        {"kid": None},
        {"use": "enc"},
        {"key_ops": "verify"},
        {"key_ops": ["sign"]},
        {"d": "not-a-public-key"},
        {"crv": "P-384"},
        {"kty": "RSA"},
        {"x": "invalid"},
    ],
)
def test_invalid_jwk_is_unavailable(auth: AuthHarness, changes: dict[str, object]) -> None:
    auth.jwks = {"keys": [{**public_jwk(auth.keys["es"], "es"), **changes}]}
    assert_rejected(auth, auth.submit(auth.token()), 503)


@pytest.mark.parametrize("kind", ["duplicate", "too-many", "too-large"])
def test_jwks_limits(auth: AuthHarness, kind: str) -> None:
    key = public_jwk(auth.keys["es"], "es")
    if kind == "duplicate":
        auth.jwks = {"keys": [key, key]}
    elif kind == "too-many":
        auth.jwks = {"keys": [{**key, "kid": str(number)} for number in range(33)]}
    else:
        auth.jwks = {"keys": [key], "padding": "x" * 131072}
    assert_rejected(auth, auth.submit(auth.token()), 503)


def test_token_algorithm_must_match_jwk(auth: AuthHarness) -> None:
    assert_rejected(auth, auth.submit(auth.token(algorithm="RS256", key_name="rsa")))


def test_token_urls_are_ignored(auth: AuthHarness) -> None:
    response = auth.submit(auth.token(headers={"jku": "https://untrusted.invalid/keys"}))
    assert response.status_code == 201
    assert all(str(request.url) == JWKS_URL for request in auth.jwks_requests)


def test_unknown_key_requests_are_rate_limited_and_rotation_refreshes(auth: AuthHarness) -> None:
    for number in range(10):
        assert_rejected(auth, auth.submit(auth.token(kid=f"unknown-{number}")))
    assert len(auth.jwks_requests) == 1
    auth.jwks = {"keys": [public_jwk(auth.keys["rotated"], "rotated")]}
    auth.clock[0] += 31
    assert auth.submit(auth.token(key_name="rotated", kid="rotated")).status_code == 201
    assert len(auth.jwks_requests) == 2
    # Refresh replaced the set; the removed key is no longer trusted.
    response = auth.submit(auth.token())
    assert response.status_code == 401
    assert len(auth.storage_requests) == 1
    assert len(auth.jwks_requests) == 2


def test_expired_cache_does_not_accept_stale_keys_during_outage(auth: AuthHarness) -> None:
    assert auth.submit(auth.token()).status_code == 201
    auth.clock[0] += 601
    auth.jwks_status = 503
    for _ in range(2):
        response = auth.submit(auth.token())
        assert response.status_code == 503
        assert response.json() == {"detail": "Authentication is unavailable."}
    assert len(auth.storage_requests) == 1
    assert len(auth.jwks_requests) == 2


def test_failed_rotation_refresh_is_throttled_but_current_known_key_works(
    auth: AuthHarness,
) -> None:
    assert auth.submit(auth.token()).status_code == 201
    auth.clock[0] += 31
    auth.jwks_status = 500
    for _ in range(2):
        assert auth.submit(auth.token(kid="new-key")).status_code == 503
    assert auth.submit(auth.token()).status_code == 201
    assert len(auth.jwks_requests) == 2
    assert len(auth.storage_requests) == 2


def test_expired_cache_refresh_and_optional_timing_claims(auth: AuthHarness) -> None:
    token = auth.token(remove=("iat", "nbf"))
    assert auth.submit(token).status_code == 201
    auth.clock[0] += 601
    assert auth.submit(token).status_code == 201
    assert len(auth.jwks_requests) == 2


@pytest.mark.parametrize(
    "fields",
    [{}, {"lighting_condition": None, "reported_time": None, "current_safety_status": None}],
)
def test_authenticated_minimum_and_null_context(
    auth: AuthHarness, fields: dict[str, object]
) -> None:
    response = auth.submit(auth.token(), **fields)
    assert response.status_code == 201
    assert auth.rows[0]["user_id"] == USER_ID
    for name in ("lighting_condition", "reported_time", "current_safety_status"):
        assert response.json()[name] is None
        assert auth.rows[0][name] is None


def test_client_owner_cannot_override_verified_identity(auth: AuthHarness) -> None:
    response = auth.submit(auth.token(), user_id=OTHER_ID)
    assert response.status_code == 422
    assert auth.storage_requests == []
    response = auth.client.post(
        "/api/v1/reports",
        json=PAYLOAD,
        headers={"Authorization": f"Bearer {auth.token()}", "X-User-ID": OTHER_ID},
    )
    assert response.status_code == 201
    assert auth.rows[0]["user_id"] == USER_ID


@pytest.mark.parametrize("field", ["nic", "nic_number"])
def test_nic_is_not_part_of_report_contract(auth: AuthHarness, field: str) -> None:
    response = auth.submit(auth.token(), **{field: "dummy-not-a-real-nic"})
    assert response.status_code == 422
    assert auth.storage_requests == []


def test_owner_mismatch_in_saved_row_fails(auth: AuthHarness) -> None:
    auth.owner_override = OTHER_ID
    response = auth.submit(auth.token())
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}


def test_authenticated_storage_failure_is_generic(
    auth: AuthHarness, caplog: pytest.LogCaptureFixture
) -> None:
    auth.storage_status = 500
    token = auth.token()
    response = auth.submit(token)
    assert response.status_code == 503
    assert response.json() == {"detail": "Report storage is unavailable."}
    for private in (token, "dummy-server-secret", "private SQL detail", PAYLOAD["original_text"]):
        assert private not in response.text
        assert private not in caplog.text
